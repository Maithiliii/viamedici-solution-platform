"""Agents never change cases or solutions directly. Their output becomes
ai_suggestions (written through Directus with the agent's restricted role),
which an engineer or architect accepts, edits or rejects.
"""
from __future__ import annotations

from typing import Any

from ..directus import get_directus
from .schemas import (ApplicabilityResult, IntakeResult, InvestigationPlan, KnowledgeDecision, ProblemProposal,
                      RetrievalResult)
from .tools import RetrievalLedger

_VERDICT_ORDER = {"applies": 0, "applies_with_changes": 1, "unknown": 2, "not_applicable": 3}
_REL_ORDER = {"high": 0, "medium": 1, "low": 2}


def _cite(numbers: list[str], ledger: RetrievalLedger) -> list[dict]:
    return [{"number": n, "type": ledger.seen.get(n, {}).get("type", "solution" if n.startswith("SOL") else "case")}
            for n in dict.fromkeys(numbers)]


def supersede(case_id: str, types: list[str]) -> int:
    d = get_directus()
    rows = d.items("ai_suggestions", fields=["id"], filter={
        "case_id": {"_eq": case_id}, "status": {"_eq": "pending"}, "type": {"_in": types}})
    keys = [r["id"] for r in rows]
    if keys:
        d.update_many("ai_suggestions", keys, {"status": "superseded"})
    return len(keys)


def write(rows: list[dict]) -> int:
    if rows:
        get_directus().create("ai_suggestions", rows)
    return len(rows)


def analysis_rows(case_id: str, run_id: str, ir: IntakeResult | None, retrieval: RetrievalResult,
                  applicability: ApplicabilityResult, facts: dict, plan: InvestigationPlan,
                  ledger: RetrievalLedger) -> list[dict]:
    rows: list[dict[str, Any]] = []
    base = {"case_id": case_id, "run_id": run_id, "status": "pending"}

    if ir is not None:
        rows.append({**base, "type": "case_structure", "rank": 0, "confidence": "medium",
                     "title": f"Structured case - module {ir.module_code}, severity {ir.severity_suggestion}",
                     "rationale": ir.summary,
                     "payload": ir.model_dump(include={"summary", "module_code", "affected_integrations", "symptoms",
                                                       "error_codes", "customer_goal", "suspected_trigger",
                                                       "severity_suggestion", "severity_reason"})})
        if ir.missing_info:
            rows.append({**base, "type": "clarification", "rank": 0, "confidence": "medium",
                         "title": f"Ask the customer for {len(ir.missing_info)} missing detail(s)",
                         "rationale": "; ".join(m.why for m in ir.missing_info),
                         "payload": {"missing_info": [m.model_dump() for m in ir.missing_info],
                                     "message": ir.clarification_message}})

    verdicts = {i.number: i for i in applicability.items}
    rel = {c.number: c for c in retrieval.candidates}
    for rank, c in enumerate([c for c in retrieval.candidates if c.type == "case"], 1):
        info = ledger.seen.get(c.number, {})
        rows.append({**base, "type": "similar_case", "rank": rank, "confidence": c.relevance,
                     "title": f"{c.number}: {info.get('title', '')}"[:255], "rationale": c.why_it_matches,
                     "citations": _cite([c.number], ledger),
                     "payload": {"number": c.number, "entity_id": info.get("id"), "relevance": c.relevance}})

    solution_numbers = list(dict.fromkeys([c.number for c in retrieval.candidates if c.type == "solution"] + list(facts)))
    solution_numbers.sort(key=lambda n: (_VERDICT_ORDER.get(verdicts[n].verdict, 2) if n in verdicts else 2,
                                         _REL_ORDER.get(rel[n].relevance, 1) if n in rel else 1))
    for rank, n in enumerate(solution_numbers, 1):
        v, f = verdicts.get(n), facts.get(n, {})
        title = f.get("title") or ledger.seen.get(n, {}).get("title", "")
        rationale = (rel[n].why_it_matches + " ") if n in rel else ""
        rows.append({**base, "type": "solution", "rank": rank,
                     "confidence": {"applies": "high", "applies_with_changes": "medium"}.get(v.verdict, "low") if v else "low",
                     "title": f"{n}: {title}"[:255], "rationale": rationale.strip(),
                     "citations": _cite([n], ledger),
                     "payload": {"number": n, "verdict": v.verdict if v else "unknown",
                                 "verdict_reason": v.reason if v else "", "adaptations": v.adaptations if v else "",
                                 "valid_versions": f.get("valid_versions"), "track_record": f.get("track_record"),
                                 "release_changes_since_proven": f.get("release_changes_since_proven", []),
                                 "history_with_this_customer": f.get("history_with_this_customer", [])}})

    rows += plan_rows(case_id, run_id, plan, ledger)
    return rows


def plan_rows(case_id: str, run_id: str, plan: InvestigationPlan, ledger: RetrievalLedger) -> list[dict]:
    base = {"case_id": case_id, "run_id": run_id, "status": "pending"}
    rows = []
    for rank, h in enumerate(plan.hypotheses, 1):
        rows.append({**base, "type": "hypothesis", "rank": rank, "confidence": h.likelihood, "title": h.title[:255],
                     "rationale": h.reasoning, "citations": _cite(h.citations, ledger),
                     "payload": {"confirm_by": h.confirm_by}})
    for rank, s in enumerate(plan.next_steps, 1):
        rows.append({**base, "type": "next_step", "rank": rank, "confidence": "medium", "title": s.action[:255],
                     "rationale": s.why, "citations": _cite(s.citations, ledger),
                     "payload": {"action": s.action, "expected_finding": s.expected_finding}})
    return rows


def knowledge_row(case_id: str, run_id: str, decision: KnowledgeDecision, ledger: RetrievalLedger,
                  target_id: str | None) -> dict:
    type_ = {"link": "knowledge_link", "update": "knowledge_update", "create": "knowledge_create"}[decision.action]
    title = {
        "link": f"Link this case to {decision.target_solution} (no new knowledge needed)",
        "update": f"Update {decision.target_solution}: {decision.changes_summary}",
        "create": f"New solution: {decision.solution.title if decision.solution else ''}",
    }[decision.action]
    return {"case_id": case_id, "run_id": run_id, "status": "pending", "type": type_, "rank": 0,
            "confidence": "medium", "title": title[:255], "rationale": decision.reasoning,
            "citations": _cite([decision.target_solution] if decision.target_solution else [], ledger),
            "payload": {"action": decision.action, "target_solution": decision.target_solution,
                        "target_solution_id": target_id, "changes_summary": decision.changes_summary,
                        "solution": decision.solution.model_dump() if decision.solution else None}}


def problem_row(run_id: str, p: ProblemProposal, case_ids: dict[str, str]) -> dict:
    return {"case_id": None, "run_id": run_id, "status": "pending", "type": "problem", "rank": 0,
            "confidence": p.confidence, "title": p.title[:255], "rationale": p.description,
            "citations": [{"number": n, "type": "case"} for n in p.case_numbers],
            "payload": {**p.model_dump(), "case_ids": [case_ids[n] for n in p.case_numbers if n in case_ids]}}
