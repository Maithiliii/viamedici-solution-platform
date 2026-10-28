"""Graph A: Case Analysis (trigger: case created, or 'Re-analyse').

load -> intake -> retrieval (ReAct agent) -> applicability -> planner -> write suggestions
Deterministic workflow with agentic nodes: autonomy only where the path varies.
"""
from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ..agents import knowledge, steps, suggestions
from ..agents.knowledge import CaseContext
from ..agents.schemas import ApplicabilityResult, IntakeResult, InvestigationPlan, RetrievalResult
from ..agents.tools import RetrievalLedger
from ..directus import get_directus
from ..tracing import Tracer


class State(TypedDict, total=False):
    case_id: str
    exclude_ids: list[str]
    tracer: Tracer
    ledger: RetrievalLedger
    ctx: CaseContext
    intake: IntakeResult
    retrieval: RetrievalResult
    applicability: ApplicabilityResult
    facts: dict
    plan: InvestigationPlan
    written: int


def load(state: State) -> State:
    tracer = state["tracer"]
    ctx = knowledge.load_case(state["case_id"])
    tracer.step("load", "node_start", f"Loaded {ctx.number} ({ctx.customer}, EPIM {ctx.version})",
                payload={"integrations": ctx.integration_codes, "modules": ctx.licensed_modules})
    if ctx.status == "new":
        get_directus().update("cases", ctx.id, {"status": "analysing"})
    return {"ctx": ctx}


def intake_node(state: State) -> State:
    return {"intake": steps.intake(state["ctx"], state["tracer"])}


def retrieval_node(state: State) -> State:
    return {"retrieval": steps.retrieve(state["ctx"], state["intake"], state["tracer"], state["ledger"],
                                        state.get("exclude_ids"))}


def applicability_node(state: State) -> State:
    result, facts = steps.assess_applicability(state["ctx"], state["retrieval"].candidates, state["tracer"])
    return {"applicability": result, "facts": facts}


def planner_node(state: State) -> State:
    return {"plan": steps.plan(state["ctx"], state["intake"], state["retrieval"], state["applicability"],
                               state["tracer"], state["ledger"])}


def write_node(state: State) -> State:
    tracer, ctx = state["tracer"], state["ctx"]
    n_old = suggestions.supersede(ctx.id, ["case_structure", "clarification", "similar_case", "solution",
                                           "hypothesis", "next_step"])
    rows = suggestions.analysis_rows(ctx.id, tracer.run_id, state["intake"], state["retrieval"],
                                     state["applicability"], state["facts"], state["plan"], state["ledger"])
    n = suggestions.write(rows)
    if ctx.status in ("new", "analysing"):
        get_directus().update("cases", ctx.id, {"status": "triage"})
    tracer.step("write", "output", f"Wrote {n} suggestion(s) for the engineer ({n_old} older ones superseded)")
    return {"written": n}


def build():
    g = StateGraph(State)
    g.add_node("load", load)
    g.add_node("intake", intake_node)
    g.add_node("retrieval", retrieval_node)
    g.add_node("applicability", applicability_node)
    g.add_node("planner", planner_node)
    g.add_node("write", write_node)
    g.add_edge(START, "load")
    g.add_edge("load", "intake")
    g.add_edge("intake", "retrieval")
    g.add_edge("retrieval", "applicability")
    g.add_edge("applicability", "planner")
    g.add_edge("planner", "write")
    g.add_edge("write", END)
    return g.compile()


GRAPH = build()


def run(case_id: str, trigger: str = "manual", exclude_ids: list[str] | None = None, write: bool = True) -> dict[str, Any]:
    tracer = Tracer("case_analysis", case_id, trigger, {"case_id": case_id})
    try:
        if write:
            state = GRAPH.invoke({"case_id": case_id, "tracer": tracer, "ledger": RetrievalLedger(),
                                  "exclude_ids": exclude_ids or []})
        else:  # evaluation mode: same steps, no writes
            state: State = {"case_id": case_id, "tracer": tracer, "ledger": RetrievalLedger(), "exclude_ids": exclude_ids or []}
            ctx = knowledge.load_case(case_id)
            state["ctx"] = ctx
            for fn in (intake_node, retrieval_node, applicability_node, planner_node):
                state.update(fn(state))
        output = {
            "assessment": state["plan"].assessment,
            "no_relevant_history": state["plan"].no_relevant_history,
            "intake": state["intake"].model_dump(),
            "candidates": [c.model_dump() for c in state["retrieval"].candidates],
            "applicability": [i.model_dump() for i in state["applicability"].items],
            "plan": state["plan"].model_dump(),
        }
        tracer.finish(output)
        return {"run_id": tracer.run_id, **output}
    except Exception as e:
        tracer.step("error", "error", f"{type(e).__name__}: {e}")
        tracer.finish(error=f"{type(e).__name__}: {e}")
        raise
