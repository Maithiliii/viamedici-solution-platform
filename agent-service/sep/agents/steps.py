"""The individual agent steps used by the graphs."""
from __future__ import annotations

import json
import logging

from ..config import get_settings
from ..guardrails import fence, find_refs, validate_citations
from ..llm import generate_structured, get_llm
from ..search import SearchQuery, hybrid_search
from ..tracing import Tracer
from . import knowledge, prompts
from .knowledge import CaseContext
from .react import run_tool_agent
from .schemas import (ApplicabilityResult, AskAnswer, Candidate, IntakeResult, InvestigationPlan,
                      KnowledgeDecision, RetrievalResult)
from .tools import RetrievalLedger, build_tools

log = logging.getLogger("sep.agents")


def _structured(tracer: Tracer, node: str, system: str, prompt: str, schema):
    llm = get_llm()
    before = tracer.usage.calls
    result = generate_structured(llm, system, prompt, schema, usage=tracer.usage)
    tracer.models.add(llm.model)
    tracer.step(node, "output", f"{schema.__name__} ({tracer.usage.calls - before} LLM call(s), model {llm.model})",
                payload=result.model_dump())
    return result


# ---------------------------------------------------------------------------
# 1. Intake
# ---------------------------------------------------------------------------

def intake(ctx: CaseContext, tracer: Tracer) -> IntakeResult:
    tracer.step("intake", "node_start", "Structuring the customer report")
    if ctx.injection_signals:
        tracer.step("intake", "guardrail", "Possible prompt injection in customer text - treated as data only",
                    payload={"patterns": ctx.injection_signals})
    if ctx.pii_masked:
        tracer.step("intake", "guardrail", f"Masked {ctx.pii_masked} personal data item(s) before LLM call")
    modules = "\n".join(f"- {m['code']}: {m['name']} - {m['description']}" for m in knowledge.module_catalog())
    integrations = ", ".join(f"{i['code']} ({i['name']})" for i in knowledge.integration_catalog())
    prompt = (
        f"MODULE CODES:\n{modules}\n\nINTEGRATION CODES: {integrations}\n\n"
        f"CASE AND ENVIRONMENT:\n{ctx.prompt_block()}\n\n{fence('CUSTOMER REPORT', ctx.report)}"
    )
    result = _structured(tracer, "intake", prompts.INTAKE, prompt, IntakeResult)
    valid_modules = {m["code"] for m in knowledge.module_catalog()}
    if result.module_code not in valid_modules:
        result.module_code = ctx.module_code or "EXPORT"
    valid_integrations = {i["code"] for i in knowledge.integration_catalog()}
    result.affected_integrations = [c for c in result.affected_integrations if c in valid_integrations]
    return result


# ---------------------------------------------------------------------------
# 2. Retrieval (agentic)
# ---------------------------------------------------------------------------

def retrieve(ctx: CaseContext, ir: IntakeResult, tracer: Tracer, ledger: RetrievalLedger,
             exclude_ids: list[str] | None = None) -> RetrievalResult:
    tracer.step("retrieval", "node_start", "Searching historical knowledge")
    s = get_settings()
    module = [ir.module_code] if ir.module_code else []
    integrations = sorted(set(ctx.integration_codes) | set(ir.affected_integrations))
    query_text = "\n".join([ctx.title, ir.summary, " ".join(ir.symptoms), " ".join(ir.error_codes)])
    hits = hybrid_search(SearchQuery(text=query_text, module_codes=module, version_major=ctx.version_major,
                                     integrations=integrations, exclude_ids=[ctx.id, *(exclude_ids or [])],
                                     limit=s.search_top_k))
    for h in hits:
        ledger.add(h.number, {"type": h.entity_type, "title": h.title, "score": h.score, "id": h.entity_id})
    tracer.step("retrieval", "tool_result", f"Initial hybrid search: {len(hits)} hits: "
                + ", ".join(h.number for h in hits), payload=[h.to_dict(200) for h in hits], tool_name="hybrid_search")

    upgrade_hint = ""
    prev = knowledge.previous_version(ctx.customer_id, ctx.version)
    if prev and ctx.version and prev != ctx.version:
        upgrade_hint = f"\nThe customer's case history shows version {prev} before {ctx.version} (recent upgrade?)."
        # Upgrade-aware retrieval: what changed between the two versions is often the cause, so search with it.
        changes = [c for c in knowledge.release_changes(prev, ctx.version)
                   if c["type"] in ("breaking", "behaviour", "behavior", "deprecation")]
        if changes:
            upgrade_hint += "\nRelease changes in that upgrade:\n" + "\n".join(
                f"- {c['version']} [{c['type']}] {c['module'] or ''}: {c['title']}" for c in changes)
            seen = {h.entity_id for h in hits}
            extra = hybrid_search(SearchQuery(
                text="\n".join(f"{c['title']}. {c['description'] or ''}" for c in changes) + "\n" + query_text,
                entity_types=("solution",), version_major=ctx.version_major, exclude_ids=[ctx.id, *(exclude_ids or [])],
                limit=4))
            extra = [h for h in extra if h.entity_id not in seen]
            for h in extra:
                ledger.add(h.number, {"type": h.entity_type, "title": h.title, "score": h.score, "id": h.entity_id})
            hits += extra
            tracer.step("retrieval", "tool_result",
                        f"Upgrade {prev} -> {ctx.version}: {len(changes)} relevant release change(s); "
                        f"change-based search added {', '.join(h.number for h in extra) or 'nothing new'}",
                        payload={"changes": changes, "hits": [h.to_dict(200) for h in extra]}, tool_name="release_changes")

    task = (
        f"NEW CASE:\n{ctx.prompt_block()}\n\nINTAKE ANALYSIS:\n{ir.model_dump_json(indent=1)}\n"
        f"{upgrade_hint}\n\nSuggested queries: {ir.search_queries}\n\n"
        f"INITIAL HYBRID SEARCH RESULTS:\n{json.dumps([h.to_dict(300) for h in hits], default=str, indent=1)}\n\n"
        "Investigate with the tools, then call finish."
    )
    try:
        result = run_tool_agent(system=prompts.RETRIEVAL, task=task, tools=build_tools(ctx, ledger, exclude_ids),
                                finish_schema=RetrievalResult, tracer=tracer, node="retrieval",
                                max_steps=s.retrieval_max_steps)
    except Exception as e:  # degrade gracefully: fall back to plain hybrid search
        log.exception("retrieval agent failed")
        tracer.step("retrieval", "error", f"Retrieval agent failed ({e}); using hybrid search ranking")
        result = RetrievalResult(candidates=[
            Candidate(type=h.entity_type, number=h.number, relevance="medium",
                      why_it_matches=f"Hybrid search rank {i + 1} (score {h.score:.4f})")
            for i, h in enumerate(hits[:6])])

    valid = [c for c in result.candidates if c.number in ledger.known]
    dropped = [c.number for c in result.candidates if c.number not in ledger.known]
    if dropped:
        tracer.step("retrieval", "guardrail", f"Dropped {len(dropped)} candidate(s) not found in retrieved records: {dropped}")
    result.candidates = valid
    return result


# ---------------------------------------------------------------------------
# 3. Applicability (facts + evaluator)
# ---------------------------------------------------------------------------

def assess_applicability(ctx: CaseContext, candidates: list[Candidate], tracer: Tracer) -> tuple[ApplicabilityResult, dict]:
    tracer.step("applicability", "node_start", "Checking whether past solutions apply to this environment")
    solution_numbers: list[str] = []
    for c in candidates:
        if c.type == "solution":
            solution_numbers.append(c.number)
        else:  # solutions used in similar cases are candidates too
            detail = knowledge.case_detail(c.number)
            for so in (detail or {}).get("solutions", []):
                solution_numbers.append(so["solution"])
    solution_numbers = list(dict.fromkeys(solution_numbers))[:6]
    facts = {n: f for n in solution_numbers if (f := knowledge.applicability_facts(n, ctx))}
    tracer.step("applicability", "tool_result", f"Collected facts for {len(facts)} solution(s): {', '.join(facts)}",
                payload=facts, tool_name="applicability_facts")
    if not facts:
        return ApplicabilityResult(items=[]), facts
    prompt = (f"NEW CASE:\n{ctx.prompt_block()}\n\nFACTS PER CANDIDATE SOLUTION:\n"
              f"{json.dumps(facts, default=str, indent=1)}\n\nGive one item per solution.")
    result = _structured(tracer, "applicability", prompts.APPLICABILITY, prompt, ApplicabilityResult)
    result.items = [i for i in result.items if i.number in facts]
    return result, facts


# ---------------------------------------------------------------------------
# 4. Investigation planner / guide
# ---------------------------------------------------------------------------

def plan(ctx: CaseContext, ir: IntakeResult | None, retrieval: RetrievalResult, applicability: ApplicabilityResult,
         tracer: Tracer, ledger: RetrievalLedger, update: bool = False, node: str = "planner") -> InvestigationPlan:
    tracer.step(node, "node_start", "Re-planning with new findings" if update else "Building the investigation plan")
    material = {
        "candidates": [c.model_dump() for c in retrieval.candidates],
        "applicability": [i.model_dump() for i in applicability.items],
        "known_records": {k: v.get("title") for k, v in ledger.seen.items()},
    }
    release_ctx = ""
    prev = knowledge.previous_version(ctx.customer_id, ctx.version)
    if prev and ctx.version:
        changes = [c for c in knowledge.release_changes(prev, ctx.version) if c["type"] in ("breaking", "deprecation")]
        if changes:
            release_ctx = f"\nBREAKING CHANGES between {prev} and {ctx.version}:\n{json.dumps(changes, indent=1)}"
    prompt = (
        f"NEW CASE:\n{ctx.prompt_block()}\n\n{fence('CUSTOMER REPORT', ctx.report)}\n\n"
        + (f"INTAKE:\n{ir.model_dump_json(indent=1)}\n\n" if ir else "")
        + f"INVESTIGATION LOG:\n{ctx.steps_block()}\n{release_ctx}\n\n"
        f"RETRIEVED HISTORY AND APPLICABILITY:\n{json.dumps(material, default=str, indent=1)}"
    )
    system = prompts.GUIDE_UPDATE if update else prompts.PLANNER
    result = _structured(tracer, node, system, prompt, InvestigationPlan)

    # citation validation: only numbers the agents actually retrieved
    known = ledger.known
    invalid_total: list[str] = []
    for h in result.hypotheses:
        h.citations, bad = validate_citations(h.citations, known)
        invalid_total += bad
    for s in result.next_steps:
        s.citations, bad = validate_citations(s.citations, known)
        invalid_total += bad
    free_text_refs = find_refs(result.assessment) - known
    invalid_total += sorted(free_text_refs)
    if invalid_total:
        tracer.step(node, "guardrail", f"Removed {len(invalid_total)} unverifiable citation(s): {sorted(set(invalid_total))}")
    return result


# ---------------------------------------------------------------------------
# 5. Knowledge capture
# ---------------------------------------------------------------------------

def capture(ctx: CaseContext, tracer: Tracer, ledger: RetrievalLedger) -> tuple[KnowledgeDecision, list[dict]]:
    tracer.step("capture", "node_start", "Deciding how this case becomes reusable knowledge")
    r = ctx.raw
    applied = knowledge.case_detail(ctx.number)["solutions"]
    query = "\n".join(x for x in [ctx.title, ctx.report, r.get("error_messages"), r.get("resolution_summary"),
                                  (r.get("root_cause_id") or {}).get("name")] if x)
    hits = hybrid_search(SearchQuery(text=query, entity_types=("solution",),
                                     module_codes=[ctx.module_code] if ctx.module_code else [],
                                     version_major=ctx.version_major, integrations=ctx.integration_codes, limit=5))
    existing = []
    for h in hits:
        d = knowledge.solution_detail(h.number)
        if d:
            ledger.add(d["number"], {"type": "solution", "title": d["title"]})
            existing.append({k: d[k] for k in ("number", "title", "issue", "environment_conditions", "cause",
                                                 "resolution_steps", "valid_versions", "modules", "track_record")})
    tracer.step("capture", "tool_result", f"Existing solutions checked for duplicates: {', '.join(e['number'] for e in existing)}",
                payload=[e["number"] for e in existing], tool_name="hybrid_search")
    root_causes = ", ".join(f"{rc['code']}" for rc in knowledge.root_cause_catalog())
    prompt = (
        f"RESOLVED CASE:\n{ctx.prompt_block()}\n\n{fence('CUSTOMER REPORT', ctx.report)}\n\n"
        f"INVESTIGATION LOG:\n{ctx.steps_block()}\n\n"
        f"ROOT CAUSE (engineer): {(r.get('root_cause_id') or {}).get('name') or 'not set'}\n"
        f"RESOLUTION SUMMARY (engineer): {r.get('resolution_summary') or 'not set'}\n"
        f"SOLUTIONS APPLIED IN THIS CASE: {json.dumps(applied)}\n\n"
        f"EXISTING SIMILAR SOLUTIONS:\n{json.dumps(existing, default=str, indent=1)}\n\n"
        f"ROOT CAUSE CODES: {root_causes}\nMODULE CODES: {', '.join(m['code'] for m in knowledge.module_catalog())}"
    )
    decision = _structured(tracer, "capture", prompts.CAPTURE, prompt, KnowledgeDecision)
    if decision.target_solution and decision.target_solution not in {e["number"] for e in existing} | {a["solution"] for a in applied}:
        tracer.step("capture", "guardrail", f"Target {decision.target_solution} was not among checked solutions; switching to 'create'")
        decision.action, decision.target_solution = "create", None
    if decision.action in ("update", "create") and decision.solution is None:
        decision.action = "link" if decision.target_solution else decision.action
    return decision, existing


# ---------------------------------------------------------------------------
# 6. Ask the case
# ---------------------------------------------------------------------------

def ask(ctx: CaseContext, question: str, tracer: Tracer, ledger: RetrievalLedger, context_material: dict) -> AskAnswer:
    tracer.step("ask", "node_start", f"Question: {question}")
    hits = hybrid_search(SearchQuery(text=f"{ctx.title}\n{question}", module_codes=[ctx.module_code] if ctx.module_code else [],
                                     version_major=ctx.version_major, integrations=ctx.integration_codes,
                                     exclude_ids=[ctx.id], limit=5))
    details = []
    for h in hits:
        d = knowledge.solution_detail(h.number) if h.entity_type == "solution" else knowledge.case_detail(h.number)
        if d:
            ledger.add(h.number, {"type": h.entity_type, "title": h.title})
            details.append(d)
    tracer.step("ask", "tool_result", f"Retrieved {len(details)} record(s): {', '.join(h.number for h in hits)}",
                tool_name="hybrid_search")
    prompt = (
        f"CASE:\n{ctx.prompt_block()}\n\n{fence('CUSTOMER REPORT', ctx.report)}\n\nINVESTIGATION LOG:\n{ctx.steps_block()}\n\n"
        f"CURRENT AI ANALYSIS:\n{json.dumps(context_material, default=str)[:6000]}\n\n"
        f"RETRIEVED RECORDS:\n{json.dumps(details, default=str)[:12000]}\n\n"
        f"ENGINEER QUESTION: {question}"
    )
    answer = _structured(tracer, "ask", prompts.ASK, prompt, AskAnswer)
    answer.citations, _ = validate_citations(answer.citations, ledger.known)
    return answer
