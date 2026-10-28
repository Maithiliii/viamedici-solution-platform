"""Graph B: Investigation Guide (trigger: engineer logged findings / asked for next steps).

load -> rebuild context from previous analysis -> (re-search if new evidence) -> re-plan -> write
"""
from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ..agents import knowledge, steps, suggestions
from ..agents.knowledge import CaseContext
from ..agents.schemas import ApplicabilityResult, Candidate, InvestigationPlan, RetrievalResult
from ..agents.tools import RetrievalLedger
from ..directus import get_directus
from ..search import SearchQuery, hybrid_search
from ..textutil import extract_codes
from ..tracing import Tracer


class State(TypedDict, total=False):
    case_id: str
    tracer: Tracer
    ledger: RetrievalLedger
    ctx: CaseContext
    retrieval: RetrievalResult
    applicability: ApplicabilityResult
    plan: InvestigationPlan


def load(state: State) -> State:
    tracer = state["tracer"]
    ctx = knowledge.load_case(state["case_id"])
    tracer.step("load", "node_start", f"Loaded {ctx.number} with {len(ctx.steps)} investigation step(s)")
    # previous analysis = accepted/pending solution + similar-case suggestions
    prev = get_directus().items("ai_suggestions", fields=["type", "payload", "rationale", "confidence", "status"],
                                filter={"case_id": {"_eq": ctx.id}, "type": {"_in": ["solution", "similar_case"]},
                                        "status": {"_in": ["pending", "accepted", "edited"]}})
    candidates, items = [], []
    for s in prev:
        n = (s.get("payload") or {}).get("number")
        if not n:
            continue
        candidates.append(Candidate(type="solution" if s["type"] == "solution" else "case", number=n,
                                    relevance=s.get("confidence") or "medium", why_it_matches=s.get("rationale") or ""))
        state["ledger"].add(n, {"type": "solution" if s["type"] == "solution" else "case"})
        if s["type"] == "solution" and s["payload"].get("verdict"):
            from ..agents.schemas import ApplicabilityItem
            items.append(ApplicabilityItem(number=n, verdict=s["payload"]["verdict"],
                                           reason=s["payload"].get("verdict_reason", "")))
    return {"ctx": ctx, "retrieval": RetrievalResult(candidates=candidates), "applicability": ApplicabilityResult(items=items)}


def research(state: State) -> State:
    """New evidence from findings (e.g. an error code) can point to knowledge not found before."""
    ctx, tracer, ledger = state["ctx"], state["tracer"], state["ledger"]
    findings = "\n".join(f"{s['action']} {s.get('finding') or ''}" for s in ctx.steps if s["result"] != "ruled_out")
    if not findings.strip():
        return {}
    codes = extract_codes(findings)
    hits = hybrid_search(SearchQuery(text=f"{ctx.title}\n{findings}", codes=" ".join(codes),
                                     module_codes=[ctx.module_code] if ctx.module_code else [],
                                     version_major=ctx.version_major, integrations=ctx.integration_codes,
                                     exclude_ids=[ctx.id], limit=5))
    known = ledger.known
    new = [h for h in hits if h.number not in known]
    for h in hits:
        ledger.add(h.number, {"type": h.entity_type, "title": h.title, "id": h.entity_id})
    tracer.step("research", "tool_result", f"Searched with findings{' and codes ' + ', '.join(codes) if codes else ''}: "
                f"{len(new)} new record(s) {', '.join(h.number for h in new)}", tool_name="hybrid_search",
                payload=[h.to_dict(200) for h in hits])
    retrieval = state["retrieval"]
    retrieval.candidates += [Candidate(type=h.entity_type, number=h.number, relevance="medium",
                                       why_it_matches="Found with the engineer's new findings") for h in new]
    return {"retrieval": retrieval}


def replan(state: State) -> State:
    return {"plan": steps.plan(state["ctx"], None, state["retrieval"], state["applicability"], state["tracer"],
                               state["ledger"], update=True, node="guide")}


def write(state: State) -> State:
    ctx, tracer = state["ctx"], state["tracer"]
    suggestions.supersede(ctx.id, ["hypothesis", "next_step"])
    n = suggestions.write(suggestions.plan_rows(ctx.id, tracer.run_id, state["plan"], state["ledger"]))
    if ctx.status in ("triage", "new", "analysing"):
        get_directus().update("cases", ctx.id, {"status": "investigating"})
    tracer.step("write", "output", f"Updated plan: {n} suggestion(s)")
    return {}


def build():
    g = StateGraph(State)
    for name, fn in (("load", load), ("research", research), ("replan", replan), ("write", write)):
        g.add_node(name, fn)
    g.add_edge(START, "load")
    g.add_edge("load", "research")
    g.add_edge("research", "replan")
    g.add_edge("replan", "write")
    g.add_edge("write", END)
    return g.compile()


GRAPH = build()


def run(case_id: str, trigger: str = "manual") -> dict[str, Any]:
    tracer = Tracer("investigation_guide", case_id, trigger, {"case_id": case_id})
    try:
        state = GRAPH.invoke({"case_id": case_id, "tracer": tracer, "ledger": RetrievalLedger()})
        output = {"assessment": state["plan"].assessment, "plan": state["plan"].model_dump()}
        tracer.finish(output)
        return {"run_id": tracer.run_id, **output}
    except Exception as e:
        tracer.step("error", "error", f"{type(e).__name__}: {e}")
        tracer.finish(error=f"{type(e).__name__}: {e}")
        raise
