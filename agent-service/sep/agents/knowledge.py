"""Read access to cases, solutions, environments and releases for the agents.

Business records are read through the Directus API (agent service account).
Aggregations Directus cannot express (per-version track record view, version
range comparisons) use read-only SQL.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from .. import db
from ..directus import get_directus
from ..guardrails import sanitize_untrusted
from ..textutil import truncate, version_major, version_tuple

CASE_FIELDS = [
    "id", "case_number", "title", "customer_report", "customer_goal", "symptoms", "error_messages",
    "steps_to_reproduce", "epim_version", "severity", "status", "channel", "resolution_summary", "opened_at",
    "resolved_at", "missing_info",
    "module_id.code", "module_id.name", "root_cause_id.code", "root_cause_id.name",
    "environment_id.id", "environment_id.name", "environment_id.deployment", "environment_id.database",
    "environment_id.config_notes", "environment_id.release_id.version",
    "environment_id.customer_id.id", "environment_id.customer_id.name", "environment_id.customer_id.tier",
    "environment_id.customer_id.industry",
]


@dataclass
class CaseContext:
    raw: dict
    id: str
    number: str
    title: str
    report: str                      # PII-masked customer report
    injection_signals: list[str]
    pii_masked: int
    version: str | None
    customer_id: str | None
    customer: str
    module_code: str | None
    status: str
    environment: dict
    integrations: list[dict] = field(default_factory=list)
    licensed_modules: list[str] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)

    @property
    def version_major(self) -> int | None:
        return version_major(self.version)

    @property
    def integration_codes(self) -> list[str]:
        return [i["code"] for i in self.integrations]

    def search_text(self) -> str:
        r = self.raw
        return "\n".join(x for x in (self.title, self.report, r.get("symptoms"), r.get("error_messages")) if x)

    def prompt_block(self) -> str:
        """Structured, model-friendly description of the case and environment."""
        r = self.raw
        integ = ", ".join(f"{i['name']} ({i['code']}{' ' + i['version'] if i.get('version') else ''})"
                          for i in self.integrations) or "none recorded"
        lines = [
            f"Case {self.number} | status {self.status} | severity {r.get('severity')} | channel {r.get('channel')}",
            f"Customer: {self.customer} ({(r.get('environment_id') or {}).get('customer_id', {}).get('industry', '')})",
            f"Environment: {self.environment.get('name')} | EPIM version at case time: {self.version or 'unknown'} "
            f"| current environment version: {self.environment.get('current_version') or 'unknown'} "
            f"| deployment: {self.environment.get('deployment')} | DB: {self.environment.get('database')}",
            f"Integrations: {integ}",
            f"Licensed modules: {', '.join(self.licensed_modules) or 'unknown'}",
            f"Environment notes: {self.environment.get('config_notes') or '-'}",
            f"Module (if triaged): {self.module_code or 'not set'}",
            f"Title: {self.title}",
        ]
        if r.get("symptoms"):
            lines.append(f"Symptoms (engineer): {r['symptoms']}")
        if r.get("error_messages"):
            lines.append(f"Error messages: {r['error_messages']}")
        return "\n".join(lines)

    def steps_block(self) -> str:
        if not self.steps:
            return "No investigation steps recorded yet."
        return "\n".join(
            f"{s['seq']}. [{s['result'].upper()}] {s['action']} -> {s.get('finding') or 'no finding yet'}"
            f"{' (suggested by agent)' if s.get('source') == 'agent_suggested' else ''}"
            for s in self.steps
        )


def load_case(case_id: str) -> CaseContext:
    d = get_directus()
    c = d.item("cases", case_id, CASE_FIELDS)
    env = c.get("environment_id") or {}
    integrations, modules = [], []
    if env.get("id"):
        integrations = [
            {"code": x["integration_id"]["code"], "name": x["integration_id"]["name"], "version": x.get("version")}
            for x in d.items("environment_integrations", fields=["version", "integration_id.code", "integration_id.name"],
                             filter={"environment_id": {"_eq": env["id"]}})
        ]
        modules = [x["module_id"]["code"] for x in
                   d.items("environment_modules", fields=["module_id.code"], filter={"environment_id": {"_eq": env["id"]}})]
    steps = d.items("investigation_steps", fields=["seq", "action", "finding", "result", "source"],
                    filter={"case_id": {"_eq": case_id}}, sort=["seq", "created_at"])
    sanitized = sanitize_untrusted(c.get("customer_report") or "")
    return CaseContext(
        raw=c, id=c["id"], number=c["case_number"], title=c["title"], report=sanitized.text,
        injection_signals=sanitized.injection_signals, pii_masked=sanitized.pii_masked,
        version=c.get("epim_version") or (env.get("release_id") or {}).get("version"),
        customer_id=(env.get("customer_id") or {}).get("id"), customer=(env.get("customer_id") or {}).get("name", "unknown"),
        module_code=(c.get("module_id") or {}).get("code"), status=c["status"],
        environment={"id": env.get("id"), "name": env.get("name"), "deployment": env.get("deployment"),
                     "database": env.get("database"), "config_notes": env.get("config_notes"),
                     "current_version": (env.get("release_id") or {}).get("version")},
        integrations=integrations, licensed_modules=modules, steps=steps,
    )


@lru_cache
def module_catalog() -> list[dict]:
    return get_directus().items("modules", fields=["code", "name", "description"], sort=["code"])


@lru_cache
def integration_catalog() -> list[dict]:
    return get_directus().items("integrations", fields=["code", "name", "type"], sort=["code"])


@lru_cache
def root_cause_catalog() -> list[dict]:
    return get_directus().items("root_causes", fields=["code", "category", "name"], sort=["code"])


# ---------------------------------------------------------------------------
# Detail lookups used as agent tools
# ---------------------------------------------------------------------------

def solution_detail(number: str) -> dict[str, Any] | None:
    d = get_directus()
    rows = d.items("solutions", filter={"solution_number": {"_eq": number}}, limit=1, fields=[
        "id", "solution_number", "title", "issue", "environment_conditions", "cause", "resolution_steps",
        "verification_steps", "min_version", "max_version", "status", "times_applied", "times_successful",
        "times_failed", "root_cause_id.code", "root_cause_id.name"])
    if not rows:
        return None
    s = rows[0]
    links = d.items("case_solutions", filter={"solution_id": {"_eq": s["id"]}}, fields=[
        "outcome", "relation", "version_at_apply", "notes", "case_id.case_number", "case_id.title",
        "case_id.environment_id.customer_id.name"], sort=["applied_at"])
    modules = [x["module_id"]["code"] for x in d.items("solution_modules", fields=["module_id.code"],
                                                       filter={"solution_id": {"_eq": s["id"]}})]
    integrations = [x["integration_id"]["code"] for x in d.items("solution_integrations", fields=["integration_id.code"],
                                                                 filter={"solution_id": {"_eq": s["id"]}})]
    return {
        "number": s["solution_number"], "title": s["title"], "status": s["status"],
        "issue": s["issue"], "environment_conditions": s["environment_conditions"], "cause": s["cause"],
        "resolution_steps": s["resolution_steps"], "verification_steps": s["verification_steps"],
        "valid_versions": {"min": s["min_version"], "max": s["max_version"]},
        "root_cause": (s.get("root_cause_id") or {}).get("name"),
        "modules": modules, "integrations": integrations,
        "track_record": {"applied": s["times_applied"], "resolved": s["times_successful"], "failed": s["times_failed"],
                         "by_major_version": track_record_by_version(s["id"])},
        "used_in_cases": [
            {"case": l["case_id"]["case_number"], "title": l["case_id"]["title"],
             "customer": ((l["case_id"].get("environment_id") or {}).get("customer_id") or {}).get("name"),
             "version": l["version_at_apply"], "outcome": l["outcome"], "relation": l["relation"],
             "notes": l.get("notes")}
            for l in links
        ],
    }


def track_record_by_version(solution_id: str) -> list[dict]:
    return [
        {"major": f"{r['version_major']}.x", "applied": r["applied"], "resolved": r["resolved"],
         "partial": r["partial"], "failed": r["failed"]}
        for r in db.fetch_all("SELECT * FROM solution_version_track_record WHERE solution_id = %s ORDER BY version_major",
                              (solution_id,))
    ]


def case_detail(number: str) -> dict[str, Any] | None:
    d = get_directus()
    rows = d.items("cases", filter={"case_number": {"_eq": number}}, limit=1, fields=[
        "id", "case_number", "title", "customer_report", "symptoms", "error_messages", "epim_version", "status",
        "resolution_summary", "opened_at", "module_id.code", "root_cause_id.name",
        "environment_id.customer_id.name"])
    if not rows:
        return None
    c = rows[0]
    steps = d.items("investigation_steps", fields=["seq", "action", "finding", "result"],
                    filter={"case_id": {"_eq": c["id"]}}, sort=["seq"])
    sols = d.items("case_solutions", filter={"case_id": {"_eq": c["id"]}},
                   fields=["outcome", "relation", "notes", "solution_id.solution_number", "solution_id.title"])
    return {
        "number": c["case_number"], "title": c["title"], "status": c["status"],
        "customer": ((c.get("environment_id") or {}).get("customer_id") or {}).get("name"),
        "version": c["epim_version"], "opened": c["opened_at"], "module": (c.get("module_id") or {}).get("code"),
        "report": truncate(sanitize_untrusted(c.get("customer_report") or "").text, 900),
        "error_messages": c.get("error_messages"),
        "root_cause": (c.get("root_cause_id") or {}).get("name"),
        "resolution": c.get("resolution_summary"),
        "investigation": [f"[{s['result']}] {s['action']} -> {s.get('finding') or ''}" for s in steps],
        "solutions": [{"solution": s["solution_id"]["solution_number"], "title": s["solution_id"]["title"],
                       "relation": s["relation"], "outcome": s["outcome"], "notes": s.get("notes")} for s in sols],
    }


def release_changes(from_version: str | None, to_version: str | None, module_codes: list[str] | None = None) -> list[dict]:
    """Release changes in (from_version, to_version], optionally limited to modules."""
    lo, hi = version_tuple(from_version) or (0, 0), version_tuple(to_version) or (99, 99)
    rows = db.fetch_all(
        """SELECT r.version, r.version_major, r.version_minor, rc.change_type, rc.title, rc.description, m.code AS module
           FROM release_changes rc JOIN releases r ON r.id = rc.release_id LEFT JOIN modules m ON m.id = rc.module_id
           ORDER BY r.version_major, r.version_minor""")
    out = []
    for r in rows:
        v = (r["version_major"], r["version_minor"])
        if lo < v <= hi and (not module_codes or r["module"] in module_codes):
            out.append({"version": r["version"], "module": r["module"], "type": r["change_type"],
                        "title": r["title"], "description": r["description"]})
    return out


def previous_version(customer_id: str | None, before_version: str | None) -> str | None:
    """Version this customer ran before the current one (from case history): upgrade detection."""
    if not customer_id or not before_version:
        return None
    rows = db.fetch_all(
        """SELECT DISTINCT c.epim_version FROM cases c JOIN environments e ON e.id = c.environment_id
           WHERE e.customer_id = %s AND c.epim_version IS NOT NULL""", (customer_id,))
    cur = version_tuple(before_version)
    older = sorted((version_tuple(r["epim_version"]), r["epim_version"]) for r in rows
                   if version_tuple(r["epim_version"]) and version_tuple(r["epim_version"]) < cur)
    return older[-1][1] if older else None


def customer_history(customer_id: str | None, exclude_case: str | None = None, limit: int = 15) -> list[dict]:
    if not customer_id:
        return []
    rows = db.fetch_all(
        """SELECT c.case_number, c.title, c.epim_version, c.status, c.opened_at::date AS opened,
                  coalesce(jsonb_agg(jsonb_build_object('solution', s.solution_number, 'outcome', cs.outcome))
                           FILTER (WHERE s.id IS NOT NULL), '[]') AS solutions
           FROM cases c JOIN environments e ON e.id = c.environment_id
           LEFT JOIN case_solutions cs ON cs.case_id = c.id AND cs.relation = 'applied'
           LEFT JOIN solutions s ON s.id = cs.solution_id
           WHERE e.customer_id = %s AND (%s::uuid IS NULL OR c.id <> %s::uuid)
           GROUP BY c.id ORDER BY c.opened_at DESC LIMIT %s""",
        (customer_id, exclude_case, exclude_case, limit))
    return [{**r, "opened": str(r["opened"])} for r in rows]


def applicability_facts(number: str, ctx: CaseContext) -> dict[str, Any] | None:
    """Deterministic facts for the applicability evaluator (no LLM involved)."""
    s = solution_detail(number)
    if not s:
        return None
    vmin, vmax = s["valid_versions"]["min"], s["valid_versions"]["max"]
    cur = version_tuple(ctx.version)
    in_range = None
    if cur:
        in_range = (not vmin or cur >= version_tuple(vmin)) and (not vmax or cur <= version_tuple(vmax))
    # changes released after the newest version where the solution was proven
    proven_versions = [u["version"] for u in s["used_in_cases"] if u["outcome"] == "resolved" and u["version"]]
    newest_proven = max(proven_versions, key=lambda v: version_tuple(v) or (0, 0)) if proven_versions else vmax
    changes = release_changes(newest_proven, ctx.version, s["modules"]) if ctx.version else []
    same_customer = [u for u in s["used_in_cases"] if u.get("customer") == ctx.customer]
    return {
        "solution": number, "title": s["title"], "status": s["status"],
        "valid_versions": f"{vmin or 'any'} - {vmax or 'latest'}", "case_version": ctx.version,
        "version_in_range": in_range,
        "module_overlap": sorted(set(s["modules"]) & ({ctx.module_code} if ctx.module_code else set(ctx.licensed_modules))),
        "integration_overlap": sorted(set(s["integrations"]) & set(ctx.integration_codes)),
        "solution_integrations": s["integrations"],
        "track_record": s["track_record"],
        "newest_version_proven": newest_proven,
        "release_changes_since_proven": [c for c in changes if c["type"] in ("breaking", "fix", "deprecation")],
        "history_with_this_customer": same_customer,
        "cause": truncate(s["cause"], 500),
    }
