"""Graph C: Knowledge Capture (trigger: case resolved).

load -> duplicate check + decide (link / update / create) -> proposal for architect approval.
The approval itself is a human action in the Workspace (Directus endpoint applies it with the
architect's permissions), i.e. a durable human-in-the-loop interrupt that survives restarts.
"""
from __future__ import annotations

from typing import Any

from ..agents import knowledge, steps, suggestions
from ..agents.tools import RetrievalLedger
from ..directus import get_directus
from ..tracing import Tracer


def run(case_id: str, trigger: str = "manual") -> dict[str, Any]:
    tracer = Tracer("knowledge_capture", case_id, trigger, {"case_id": case_id})
    try:
        ctx = knowledge.load_case(case_id)
        tracer.step("load", "node_start", f"Loaded resolved case {ctx.number}")
        if ctx.status not in ("resolved", "knowledge_review", "closed"):
            tracer.step("load", "guardrail", f"Case status is '{ctx.status}', capture runs only for resolved cases")
            tracer.finish({"skipped": True})
            return {"run_id": tracer.run_id, "skipped": True}
        ledger = RetrievalLedger()
        decision, existing = steps.capture(ctx, tracer, ledger)
        target_id = None
        if decision.target_solution:
            rows = get_directus().items("solutions", fields=["id"], limit=1,
                                        filter={"solution_number": {"_eq": decision.target_solution}})
            target_id = rows[0]["id"] if rows else None
        suggestions.supersede(ctx.id, ["knowledge_create", "knowledge_update", "knowledge_link"])
        suggestions.write([suggestions.knowledge_row(ctx.id, tracer.run_id, decision, ledger, target_id)])
        if ctx.status == "resolved":
            get_directus().update("cases", ctx.id, {"status": "knowledge_review"})
        tracer.step("write", "output", f"Knowledge proposal: {decision.action} {decision.target_solution or ''} "
                                       "- waiting for architect approval")
        output = decision.model_dump()
        tracer.finish(output)
        return {"run_id": tracer.run_id, **output}
    except Exception as e:
        tracer.step("error", "error", f"{type(e).__name__}: {e}")
        tracer.finish(error=f"{type(e).__name__}: {e}")
        raise
