"""Configure Directus on top of the SQL schema (idempotent).

- collection folders, icons, display templates, field interfaces
- relations: M2O (from FKs), O2M and M2M alias fields, relations to directus_users
- roles, policies, field-level permissions (Engineer, Solution Architect, AI Agent)
- users (demo team + agent service account with static token)
- Flows: case created / case updated -> agent service; nightly problem detection
- Insights dashboard
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from ..config import get_settings
from ..db import execute
from ..directus import Directus, DirectusError

log = logging.getLogger("sep.bootstrap")

DEMO_PASSWORD = "SepDemo!2026"

FOLDERS = {
    "sep_work": ("Case Work", "support_agent", 1),
    "sep_knowledge": ("Knowledge", "menu_book", 2),
    "sep_customers": ("Customers", "domain", 3),
    "sep_product": ("Product & Releases", "inventory_2", 4),
    "sep_ai": ("AI Traceability", "smart_toy", 5),
}

COLLECTIONS: dict[str, dict[str, Any]] = {
    "cases": {"group": "sep_work", "icon": "support_agent", "sort": 1, "display_template": "{{case_number}} · {{title}}",
              "note": "Customer cases (incidents). Raw customer text is untrusted input."},
    "investigation_steps": {"group": "sep_work", "icon": "manage_search", "sort": 2,
                            "display_template": "{{seq}}. {{action}} ({{result}})"},
    "case_solutions": {"group": "sep_work", "icon": "link", "sort": 3,
                       "display_template": "{{solution_id.solution_number}} â†’ {{outcome}}",
                       "note": "Which solution was applied in which case, with outcome (feeds the track record)."},
    "case_links": {"group": "sep_work", "icon": "account_tree", "sort": 4},
    "problems": {"group": "sep_work", "icon": "bug_report", "sort": 5, "display_template": "{{title}}",
                 "note": "ITIL problems: one underlying cause behind several cases."},
    "solutions": {"group": "sep_knowledge", "icon": "lightbulb", "sort": 1,
                  "display_template": "{{solution_number}} · {{title}}",
                  "note": "Reusable knowledge in KCS structure (Issue, Environment, Cause, Resolution)."},
    "root_causes": {"group": "sep_knowledge", "icon": "category", "sort": 2, "display_template": "{{code}}"},
    "solution_modules": {"group": "sep_knowledge", "hidden": True, "icon": "import_export"},
    "solution_integrations": {"group": "sep_knowledge", "hidden": True, "icon": "import_export"},
    "customers": {"group": "sep_customers", "icon": "domain", "sort": 1, "display_template": "{{name}}"},
    "environments": {"group": "sep_customers", "icon": "dns", "sort": 2,
                     "display_template": "{{customer_id.name}} {{name}} ({{release_id.version}})"},
    "environment_modules": {"group": "sep_customers", "hidden": True, "icon": "import_export"},
    "environment_integrations": {"group": "sep_customers", "hidden": True, "icon": "import_export"},
    "releases": {"group": "sep_product", "icon": "new_releases", "sort": 1, "display_template": "EPIM {{version}}"},
    "release_changes": {"group": "sep_product", "icon": "change_circle", "sort": 2,
                        "display_template": "{{release_id.version}} {{change_type}}: {{title}}"},
    "modules": {"group": "sep_product", "icon": "widgets", "sort": 3, "display_template": "{{code}}"},
    "integrations": {"group": "sep_product", "icon": "hub", "sort": 4, "display_template": "{{name}}"},
    "ai_suggestions": {"group": "sep_ai", "icon": "auto_awesome", "sort": 1, "display_template": "{{type}}: {{title}}",
                       "note": "Everything agents propose. Humans accept, edit or reject."},
    "agent_runs": {"group": "sep_ai", "icon": "play_circle", "sort": 2, "display_template": "{{graph}} {{status}}"},
    "agent_steps": {"group": "sep_ai", "icon": "timeline", "sort": 3, "display_template": "{{seq}} {{node}} {{summary}}"},
}

M2O = {  # field -> (related collection, display template)
    ("cases", "environment_id"): ("environments", "{{customer_id.name}} {{name}}"),
    ("cases", "module_id"): ("modules", "{{code}}"),
    ("cases", "root_cause_id"): ("root_causes", "{{code}}"),
    ("cases", "problem_id"): ("problems", "{{title}}"),
    ("investigation_steps", "case_id"): ("cases", "{{case_number}}"),
    ("case_solutions", "case_id"): ("cases", "{{case_number}} {{title}}"),
    ("case_solutions", "solution_id"): ("solutions", "{{solution_number}} {{title}}"),
    ("case_links", "from_case_id"): ("cases", "{{case_number}}"),
    ("case_links", "to_case_id"): ("cases", "{{case_number}}"),
    ("solutions", "root_cause_id"): ("root_causes", "{{code}}"),
    ("environments", "customer_id"): ("customers", "{{name}}"),
    ("environments", "release_id"): ("releases", "{{version}}"),
    ("release_changes", "release_id"): ("releases", "{{version}}"),
    ("release_changes", "module_id"): ("modules", "{{code}}"),
    ("problems", "root_cause_id"): ("root_causes", "{{code}}"),
    ("ai_suggestions", "case_id"): ("cases", "{{case_number}}"),
    ("ai_suggestions", "run_id"): ("agent_runs", "{{graph}}"),
    ("agent_runs", "case_id"): ("cases", "{{case_number}}"),
    ("agent_steps", "run_id"): ("agent_runs", "{{graph}}"),
}

USER_FKS = [("cases", "assigned_to"), ("investigation_steps", "author"), ("solutions", "approved_by"),
            ("case_solutions", "applied_by"), ("ai_suggestions", "decided_by")]

O2M = [  # (one collection, alias, many collection, many field, template)
    ("cases", "investigation", "investigation_steps", "case_id", "{{seq}}. {{action}} â†’ {{result}}"),
    ("cases", "solutions_applied", "case_solutions", "case_id", "{{solution_id.solution_number}} â†’ {{outcome}}"),
    ("cases", "suggestions", "ai_suggestions", "case_id", "{{type}}: {{title}} ({{status}})"),
    ("cases", "agent_runs", "agent_runs", "case_id", "{{graph}} {{status}}"),
    ("solutions", "cases", "case_solutions", "solution_id", "{{case_id.case_number}} â†’ {{outcome}}"),
    ("customers", "environments", "environments", "customer_id", "{{name}} ({{release_id.version}})"),
    ("releases", "changes", "release_changes", "release_id", "{{change_type}}: {{title}}"),
    ("agent_runs", "steps", "agent_steps", "run_id", "{{seq}} {{node}}: {{summary}}"),
    ("problems", "cases", "cases", "problem_id", "{{case_number}} {{title}}"),
]

M2M = [  # (collection, alias, junction, junction field -> this, junction field -> other, other collection, template)
    ("solutions", "modules", "solution_modules", "solution_id", "module_id", "modules", "{{module_id.code}}"),
    ("solutions", "integrations", "solution_integrations", "solution_id", "integration_id", "integrations",
     "{{integration_id.name}}"),
    ("environments", "modules", "environment_modules", "environment_id", "module_id", "modules", "{{module_id.code}}"),
    ("environments", "integrations", "environment_integrations", "environment_id", "integration_id", "integrations",
     "{{integration_id.name}}"),
]


def _choices(*values: str) -> dict:
    return {"choices": [{"text": v.replace("_", " ").title(), "value": v} for v in values]}


FIELD_META: dict[tuple[str, str], dict[str, Any]] = {
    ("cases", "status"): {"interface": "select-dropdown", "display": "labels", "width": "half",
                          "options": _choices("new", "analysing", "triage", "waiting_customer", "investigating",
                                              "resolved", "knowledge_review", "closed")},
    ("cases", "severity"): {"interface": "select-dropdown", "display": "labels", "width": "half",
                            "options": _choices("S1", "S2", "S3", "S4")},
    ("cases", "channel"): {"interface": "select-dropdown", "width": "half", "options": _choices("email", "portal", "phone")},
    ("cases", "customer_report"): {"interface": "input-multiline", "note": "Untrusted customer text"},
    ("cases", "symptoms"): {"interface": "input-multiline"},
    ("cases", "error_messages"): {"interface": "input-multiline", "options": {"font": "monospace"}},
    ("cases", "customer_goal"): {"interface": "input-multiline"},
    ("cases", "steps_to_reproduce"): {"interface": "input-multiline"},
    ("cases", "resolution_summary"): {"interface": "input-multiline"},
    ("cases", "missing_info"): {"interface": "input-code", "options": {"language": "JSON"}},
    ("cases", "case_number"): {"readonly": True, "width": "half"},
    ("solutions", "status"): {"interface": "select-dropdown", "display": "labels", "width": "half",
                              "options": _choices("draft", "not_validated", "validated", "needs_review", "archived")},
    ("solutions", "solution_number"): {"readonly": True, "width": "half"},
    ("solutions", "issue"): {"interface": "input-multiline", "note": "KCS Issue: symptoms in the customer's words"},
    ("solutions", "environment_conditions"): {"interface": "input-multiline", "note": "KCS Environment"},
    ("solutions", "cause"): {"interface": "input-multiline", "note": "KCS Cause"},
    ("solutions", "resolution_steps"): {"interface": "input-multiline", "note": "KCS Resolution"},
    ("solutions", "verification_steps"): {"interface": "input-multiline"},
    ("solutions", "times_applied"): {"readonly": True, "width": "third", "note": "Maintained by DB trigger"},
    ("solutions", "times_successful"): {"readonly": True, "width": "third"},
    ("solutions", "times_failed"): {"readonly": True, "width": "third"},
    ("investigation_steps", "result"): {"interface": "select-dropdown", "display": "labels",
                                        "options": _choices("confirmed", "ruled_out", "inconclusive")},
    ("case_solutions", "outcome"): {"interface": "select-dropdown", "display": "labels",
                                    "options": _choices("resolved", "partial", "failed", "not_tried")},
    ("ai_suggestions", "status"): {"interface": "select-dropdown", "display": "labels",
                                   "options": _choices("pending", "accepted", "rejected", "edited", "superseded")},
    ("ai_suggestions", "payload"): {"interface": "input-code", "options": {"language": "JSON"}},
    ("ai_suggestions", "citations"): {"interface": "input-code", "options": {"language": "JSON"}},
    ("agent_runs", "input"): {"interface": "input-code", "options": {"language": "JSON"}},
    ("agent_runs", "output"): {"interface": "input-code", "options": {"language": "JSON"}},
    ("agent_steps", "payload"): {"interface": "input-code", "options": {"language": "JSON"}},
}

BUSINESS = ["cases", "investigation_steps", "case_solutions", "case_links", "problems", "solutions", "root_causes",
            "solution_modules", "solution_integrations", "customers", "environments", "environment_modules",
            "environment_integrations", "releases", "release_changes", "modules", "integrations"]
AI = ["ai_suggestions", "agent_runs", "agent_steps"]


def wait_for(url: str, timeout_s: int = 300) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if httpx.get(f"{url}/server/ping", timeout=5).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(3)
    raise TimeoutError(f"Directus not reachable at {url}")


class Bootstrap:
    def __init__(self, d: Directus):
        self.d = d

    # -- schema ------------------------------------------------------------------
    def folders(self) -> None:
        existing = {c["collection"] for c in self.d.get("/collections")}
        for key, (label, icon, sort) in FOLDERS.items():
            if key not in existing:
                self.d.post("/collections", {"collection": key, "schema": None,
                                             "meta": {"icon": icon, "sort": sort, "collapse": "open",
                                                      "translations": [{"language": "en-US", "translation": label}]}})

    def collections(self) -> None:
        for name, meta in COLLECTIONS.items():
            self.d.patch(f"/collections/{name}", {"meta": {"accountability": "all", **meta}})

    def fields(self) -> None:
        for coll in COLLECTIONS:
            self._field(coll, "id", {"hidden": True, "readonly": True})
        for (coll, field), meta in FIELD_META.items():
            self._field(coll, field, meta)
        for (coll, field), (related, template) in M2O.items():
            self._field(coll, field, {"interface": "select-dropdown-m2o", "display": "related-values",
                                      "special": ["m2o"], "options": {"template": template},
                                      "display_options": {"template": template}})

    def _field(self, coll: str, field: str, meta: dict) -> None:
        try:
            self.d.patch(f"/fields/{coll}/{field}", {"meta": meta})
        except DirectusError as e:
            log.warning("field %s.%s: %s", coll, field, e)

    def user_relations(self) -> None:
        existing = {(r["collection"], r["field"]) for r in self.d.get("/relations")}
        for coll, field in USER_FKS:
            if (coll, field) not in existing:
                self.d.post("/relations", {"collection": coll, "field": field, "related_collection": "directus_users",
                                           "schema": {"on_delete": "SET NULL"}, "meta": {"sort_field": None}})
            self._field(coll, field, {"interface": "select-dropdown-m2o", "display": "user", "special": ["m2o"],
                                      "options": {"template": "{{first_name}} {{last_name}}"}})

    def aliases(self) -> None:
        fields = {(f["collection"], f["field"]) for f in self.d.get("/fields")}
        for one, alias, many, many_field, template in O2M:
            if (one, alias) not in fields:
                self.d.post(f"/fields/{one}", {"field": alias, "type": "alias",
                                               "meta": {"special": ["o2m"], "interface": "list-o2m",
                                                        "display": "related-values",
                                                        "options": {"template": template},
                                                        "display_options": {"template": template}}})
            self._relation_meta(many, many_field, one, {"one_field": alias})
        for coll, alias, junction, this_field, other_field, other, template in M2M:
            if (coll, alias) not in fields:
                self.d.post(f"/fields/{coll}", {"field": alias, "type": "alias",
                                                "meta": {"special": ["m2m"], "interface": "list-m2m",
                                                         "display": "related-values",
                                                         "options": {"template": template},
                                                         "display_options": {"template": template}}})
            self._relation_meta(junction, this_field, coll, {"one_field": alias, "junction_field": other_field})
            self._relation_meta(junction, other_field, other, {"junction_field": this_field})

    def _relation_meta(self, many: str, many_field: str, one: str, meta: dict) -> None:
        rel = self.d.get(f"/relations/{many}/{many_field}")
        if rel.get("meta"):
            self.d.patch(f"/relations/{many}/{many_field}", {"meta": meta})
            return
        # FKs created in SQL have no directus_relations row and the API cannot create one via PATCH.
        execute("""insert into directus_relations (many_collection, many_field, one_collection, one_field,
                                                   junction_field, one_deselect_action)
                   values (%s, %s, %s, %s, %s, 'nullify')""",
                (many, many_field, one, meta.get("one_field"), meta.get("junction_field")))

    # -- access control ------------------------------------------------------------
    def _ensure(self, path: str, name: str, body: dict) -> str:
        found = self.d.get(path, filter={"name": {"_eq": name}}, limit=1)
        if found:
            return found[0]["id"]
        return self.d.post(path, {"name": name, **body})["id"]

    def _permissions(self, policy: str, rules: list[tuple[str, str, list[str] | None]]) -> None:
        existing = {(p["collection"], p["action"]) for p in self.d.get("/permissions", filter={"policy": {"_eq": policy}}, limit=-1)}
        for coll, action, fields in rules:
            if (coll, action) in existing:
                continue
            body = {"policy": policy, "collection": coll, "action": action,
                    "fields": fields or ["*"], "permissions": None, "validation": None}
            try:
                self.d.post("/permissions", body)
            except DirectusError as e:
                # Unlicensed Directus 12 restricts field-level rules; degrade to collection level there.
                if e.status != 403 or not fields:
                    raise
                log.warning("field-level permission rejected for %s.%s, using all fields", coll, action)
                self.d.post("/permissions", {**body, "fields": ["*"]})

    def access(self) -> dict[str, str]:
        p_eng = self._ensure("/policies", "SEP Engineer", {"icon": "support_agent", "app_access": True, "admin_access": False,
                                                            "description": "Work cases, log investigations, review AI suggestions"})
        p_arc = self._ensure("/policies", "SEP Knowledge Owner", {"icon": "verified", "app_access": True, "admin_access": False,
                                                                   "description": "Approve and maintain solutions (knowledge)"})
        p_agent = self._ensure("/policies", "SEP AI Agent", {"icon": "smart_toy", "app_access": False, "admin_access": False,
                                                              "description": "Read knowledge, write suggestions only"})
        read_all = [(c, "read", None) for c in BUSINESS + AI] + [("directus_users", "read", ["id", "first_name", "last_name", "email", "avatar"])]
        self._permissions(p_eng, read_all + [
            ("cases", "create", None), ("cases", "update", None),
            ("investigation_steps", "create", None), ("investigation_steps", "update", None),
            ("case_solutions", "create", None), ("case_solutions", "update", None),
            ("case_links", "create", None),
            ("ai_suggestions", "update", ["status", "feedback_note", "decided_by", "decided_at"]),
        ])
        self._permissions(p_arc, [
            ("solutions", "create", None), ("solutions", "update", None),
            ("solution_modules", "create", None), ("solution_modules", "delete", None),
            ("solution_integrations", "create", None), ("solution_integrations", "delete", None),
            ("problems", "create", None), ("problems", "update", None),
            ("root_causes", "create", None),
        ])
        # Least privilege for the agent: read, create suggestions, mark old ones superseded, move case status.
        self._permissions(p_agent, read_all + [
            ("ai_suggestions", "create", None),
            ("ai_suggestions", "update", ["status"]),
            ("cases", "update", ["status"]),
        ])
        r_eng = self._ensure("/roles", "Support Engineer", {"icon": "support_agent"})
        r_arc = self._ensure("/roles", "Solution Architect", {"icon": "architecture"})
        r_agent = self._ensure("/roles", "AI Agent", {"icon": "smart_toy"})
        links = {(a["role"], a["policy"]) for a in self.d.get("/access", limit=-1) if a.get("role")}
        for role, policy in ((r_eng, p_eng), (r_arc, p_eng), (r_arc, p_arc), (r_agent, p_agent)):
            if (role, policy) not in links:
                self.d.post("/access", {"role": role, "policy": policy})
        return {"Support Engineer": r_eng, "Solution Architect": r_arc, "AI Agent": r_agent}

    def users(self, roles: dict[str, str], team: list[dict], agent_token: str) -> None:
        existing = {u["email"]: u["id"] for u in self.d.get("/users", fields="id,email", limit=-1)}
        for u in team:
            if u["email"] not in existing:
                self.d.post("/users", {"email": u["email"], "first_name": u["first_name"], "last_name": u["last_name"],
                                       "password": DEMO_PASSWORD, "role": roles[u["role"]], "status": "active"})
        agent_email = "agent@sep.example.com"
        body = {"first_name": "SEP", "last_name": "Agent", "role": roles["AI Agent"], "token": agent_token,
                "status": "active", "description": "Service account of the agent service"}
        if agent_email in existing:
            self.d.patch(f"/users/{existing[agent_email]}", body)
        else:
            self.d.post("/users", {"email": agent_email, **body})

    # -- automation -------------------------------------------------------------
    def flows(self) -> None:
        existing = {f["name"]: f["id"] for f in self.d.get("/flows", limit=-1)}
        headers = [{"header": "X-SEP-Secret", "value": "{{$env.SEP_AGENT_SERVICE_SECRET}}"},
                   {"header": "Content-Type", "value": "application/json"}]
        specs = [
            ("SEP · Analyse new case", "bolt", {"type": "action", "scope": ["items.create"], "collections": ["cases"]},
             "event", "/flows/case-event",
             '{"event": "{{$trigger.event}}", "key": "{{$trigger.key}}", "payload": {{$trigger.payload}}}',
             "Runs the Case Analysis agent graph when a case is created"),
            ("SEP · Case updated", "sync", {"type": "action", "scope": ["items.update"], "collections": ["cases"]},
             "event", "/flows/case-event",
             '{"event": "{{$trigger.event}}", "keys": {{$trigger.keys}}, "payload": {{$trigger.payload}}}',
             "Re-indexes the case; starts Knowledge Capture when status becomes 'resolved'"),
            ("SEP · Nightly problem detection", "bug_report", {"cron": "0 3 * * *"}, "schedule",
             "/runs/problem-detection", "{}", "Clusters open cases without solution and proposes ITIL problems"),
        ]
        for name, icon, options, trigger, path, body, description in specs:
            if name in existing:
                continue
            flow = self.d.post("/flows", {"name": name, "icon": icon, "status": "active", "trigger": trigger,
                                          "accountability": "all", "options": options, "description": description})
            op = self.d.post("/operations", {
                "flow": flow["id"], "name": "Call agent service", "key": "call_agent_service", "type": "request",
                "position_x": 19, "position_y": 1,
                "options": {"method": "POST", "url": "{{$env.SEP_AGENT_SERVICE_URL}}" + path,
                            "headers": headers, "body": body}})
            self.d.patch(f"/flows/{flow['id']}", {"operation": op["id"]})

    def settings(self) -> None:
        # Custom modules only appear in the module bar once enabled there; put the workspace first.
        modules = ["sep-workspace", "content", "users", "files", "insights", "settings"]
        self.d.patch("/settings", {
            "project_name": "Solution Enablement Platform", "project_descriptor": "Viamedici EPIM support",
            "project_color": "#2F6FEB", "default_language": "en-US",
            "module_bar": [{"type": "module", "id": m, "enabled": True, **({"locked": True} if m == "settings" else {})}
                           for m in modules]})

    def dashboard(self) -> None:
        if self.d.get("/dashboards", filter={"name": {"_eq": "Solution Enablement"}}):
            return
        dash = self.d.post("/dashboards", {"name": "Solution Enablement", "icon": "insights",
                                           "note": "Knowledge reuse and AI assistance at a glance"})
        panels = [
            ("Open cases", "metric", 0, 0, 8, 6, {"collection": "cases", "field": "id", "function": "count",
                                                   "filter": {"status": {"_nin": ["closed", "resolved"]}}}),
            ("Validated solutions", "metric", 8, 0, 8, 6, {"collection": "solutions", "field": "id", "function": "count",
                                                           "filter": {"status": {"_eq": "validated"}}}),
            ("Solutions applied (total)", "metric", 16, 0, 8, 6, {"collection": "solutions", "field": "times_applied",
                                                                  "function": "sum"}),
            ("AI suggestions accepted", "metric", 24, 0, 8, 6, {"collection": "ai_suggestions", "field": "id",
                                                                "function": "count",
                                                                "filter": {"status": {"_eq": "accepted"}}}),
            ("Agent runs", "metric", 32, 0, 8, 6, {"collection": "agent_runs", "field": "id", "function": "count"}),
            ("Cases per month", "time-series", 0, 6, 24, 14, {"collection": "cases", "dateField": "opened_at",
                                                              "valueField": "id", "function": "count",
                                                              "precision": "month", "range": "auto"}),
            ("Cases by module", "bar-chart", 24, 6, 24, 14, {"collection": "cases", "xAxis": "module_id",
                                                             "yAxis": "id", "function": "count"}),
            ("Recent agent runs", "list", 0, 20, 24, 14, {"collection": "agent_runs", "displayTemplate":
                                                          "{{graph}} · {{status}} · {{latency_ms}} ms",
                                                          "sortField": "started_at", "sortDirection": "desc", "limit": 10}),
        ]
        for name, type_, x, y, w, h, options in panels:
            try:
                self.d.post("/panels", {"dashboard": dash["id"], "name": name, "type": type_, "position_x": x + 1,
                                        "position_y": y + 1, "width": w, "height": h, "show_header": True,
                                        "options": options})
            except DirectusError as e:
                log.warning("panel %s: %s", name, e)


def run(team: list[dict]) -> None:
    s = get_settings()
    wait_for(s.directus_url)
    d = Directus.login(s.directus_url, s.directus_admin_email, s.directus_admin_password)
    b = Bootstrap(d)
    for step in (b.folders, b.collections, b.fields, b.user_relations, b.aliases):
        log.info("bootstrap: %s", step.__name__)
        step()
    roles = b.access()
    b.users(roles, team, s.directus_agent_token)
    b.flows()
    b.settings()
    b.dashboard()
    d.post("/utils/cache/clear", {})
    log.info("bootstrap complete")
