"""Tools exposed to the agents. Read-only by design: agents cannot change data via tools."""
from __future__ import annotations

from typing import Any, Callable

from ..config import get_settings
from ..llm import ToolSpec
from ..search import SearchQuery, hybrid_search
from . import knowledge
from .knowledge import CaseContext


class RetrievalLedger:
    """Remembers every record the agents saw, for citation validation."""

    def __init__(self) -> None:
        self.seen: dict[str, dict] = {}

    def add(self, number: str, info: dict) -> None:
        if number:
            self.seen.setdefault(number, {}).update(info)

    @property
    def known(self) -> set[str]:
        return set(self.seen)


def build_tools(ctx: CaseContext, ledger: RetrievalLedger, exclude_ids: list[str] | None = None) -> list[ToolSpec]:
    exclude = [ctx.id, *(exclude_ids or [])]
    s = get_settings()

    def search_knowledge(query: str, scope: str = "all", module: str | None = None) -> list[dict]:
        types = {"all": ("case", "solution"), "solutions": ("solution",), "cases": ("case",)}.get(scope, ("case", "solution"))
        hits = hybrid_search(SearchQuery(
            text=query, entity_types=types, module_codes=[module] if module else ([ctx.module_code] if ctx.module_code else []),
            version_major=ctx.version_major, integrations=ctx.integration_codes, exclude_ids=exclude,
            limit=s.search_top_k))
        out = []
        for h in hits:
            d = h.to_dict(snippet_len=300)
            ledger.add(h.number, {"type": h.entity_type, "title": h.title, "score": h.score, "id": h.entity_id})
            out.append(d)
        return out

    def get_solution(number: str) -> dict:
        d = knowledge.solution_detail(number.strip().upper())
        if not d:
            return {"error": f"Solution {number} not found"}
        ledger.add(d["number"], {"type": "solution", "title": d["title"]})
        for u in d["used_in_cases"]:
            ledger.add(u["case"], {"type": "case", "title": u["title"]})
        return d

    def get_case(number: str) -> dict:
        number = number.strip().upper()
        if number == ctx.number:
            return {"error": "That is the current case."}
        d = knowledge.case_detail(number)
        if not d:
            return {"error": f"Case {number} not found"}
        ledger.add(d["number"], {"type": "case", "title": d["title"]})
        for so in d["solutions"]:
            ledger.add(so["solution"], {"type": "solution", "title": so["title"]})
        return d

    def get_customer_history() -> list[dict]:
        rows = knowledge.customer_history(ctx.customer_id, exclude_case=ctx.id)
        for r in rows:
            ledger.add(r["case_number"], {"type": "case", "title": r["title"]})
            for so in r["solutions"]:
                ledger.add(so["solution"], {"type": "solution"})
        return [{"number": r["case_number"], **{k: v for k, v in r.items() if k != "case_number"}} for r in rows]

    def get_release_changes(from_version: str, to_version: str, module: str | None = None) -> list[dict]:
        return knowledge.release_changes(from_version, to_version, [module] if module else None)

    def spec(fn: Callable[..., Any], description: str, properties: dict, required: list[str]) -> ToolSpec:
        return ToolSpec(name=fn.__name__, description=description,
                        parameters={"type": "object", "properties": properties, "required": required}, fn=fn)

    module_codes = [m["code"] for m in knowledge.module_catalog()]
    return [
        spec(search_knowledge,
             "Hybrid search (semantic + keyword + error codes) over historical cases and solutions. Results are boosted "
             "by the current case's module, version and integrations.",
             {"query": {"type": "string", "description": "Search text in support language"},
              "scope": {"type": "string", "enum": ["all", "solutions", "cases"]},
              "module": {"type": "string", "enum": module_codes, "description": "Optional module code to boost"}},
             ["query"]),
        spec(get_solution, "Full solution: issue, cause, steps, valid versions, track record per version, cases where it was used.",
             {"number": {"type": "string", "description": "e.g. SOL-012"}}, ["number"]),
        spec(get_case, "Full historical case: report, investigation steps (confirmed/ruled out), root cause, solutions and outcomes.",
             {"number": {"type": "string", "description": "e.g. CS-1042"}}, ["number"]),
        spec(get_customer_history, "Previous cases of the same customer with the solutions tried and their outcomes.", {}, []),
        spec(get_release_changes, "EPIM release changes (features, fixes, BREAKING changes) between two versions.",
             {"from_version": {"type": "string", "description": "exclusive, e.g. 9.0"},
              "to_version": {"type": "string", "description": "inclusive, e.g. 9.1"},
              "module": {"type": "string", "enum": module_codes}}, ["from_version", "to_version"]),
    ]
