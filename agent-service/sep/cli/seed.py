"""Load the synthetic dataset (data/dataset/*.yaml) into PostgreSQL.

Run: python -m sep.cli.seed [--force]
Seeding uses SQL directly (bulk load, deterministic). Users must exist in Directus
already (created by the bootstrap step) so cases can be assigned to engineers.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
from pathlib import Path

import yaml

from .. import db
from ..config import get_settings

log = logging.getLogger("sep.seed")


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_dataset(data_dir: Path) -> dict:
    ds = data_dir / "dataset"
    solutions = []
    for name in ("knowledge_channels.yaml", "knowledge_data.yaml", "knowledge_platform.yaml"):
        solutions += _load_yaml(ds / name)["solutions"]
    return {
        "taxonomy": _load_yaml(ds / "taxonomy.yaml"),
        "solutions": sorted(solutions, key=lambda s: s["number"]),
        "open_cases": _load_yaml(ds / "open_cases.yaml")["cases"],
        "demo_cases": _load_yaml(ds / "demo_cases.yaml")["cases"],
    }


def _ts(date, hour: int = 9) -> dt.datetime:
    d = date if isinstance(date, dt.date) else dt.date.fromisoformat(str(date))
    return dt.datetime.combine(d, dt.time(hour, 0), tzinfo=dt.timezone.utc)


def seed(force: bool = False) -> dict:
    s = get_settings()
    data = load_dataset(Path(s.data_dir))
    tax = data["taxonomy"]

    with db.connection() as conn:
        existing = conn.execute("SELECT count(*) AS n FROM solutions").fetchone()["n"]
        if existing and not force:
            log.info("Dataset already present (%s solutions) - skipping. Use --force to reload.", existing)
            return {"skipped": True}
        with conn.transaction():
            if force:
                conn.execute("""TRUNCATE ai_suggestions, agent_steps, agent_runs, case_links, case_solutions,
                                investigation_steps, cases, problems, solution_modules, solution_integrations,
                                solutions, environment_integrations, environment_modules, environments, customers,
                                release_changes, releases, root_causes, integrations, modules CASCADE""")
                conn.execute("ALTER SEQUENCE case_number_seq RESTART WITH 1001")
                conn.execute("ALTER SEQUENCE solution_number_seq RESTART WITH 1")
                conn.execute("TRUNCATE search.knowledge_chunks")

            users = {r["email"]: r["id"] for r in conn.execute("SELECT id, email FROM directus_users").fetchall()}
            user_ids = {u["key"]: users.get(u["email"]) for u in tax["users"]}

            mod = {m["code"]: conn.execute(
                "INSERT INTO modules (code, name, description) VALUES (%s,%s,%s) RETURNING id",
                (m["code"], m["name"], m.get("description"))).fetchone()["id"] for m in tax["modules"]}
            integ = {i["code"]: conn.execute(
                "INSERT INTO integrations (code, name, type, description) VALUES (%s,%s,%s,%s) RETURNING id",
                (i["code"], i["name"], i["type"], i.get("description"))).fetchone()["id"] for i in tax["integrations"]}
            rel = {}
            for r in tax["releases"]:
                major, minor = (int(x) for x in str(r["version"]).split("."))
                rel[str(r["version"])] = conn.execute(
                    """INSERT INTO releases (version, version_major, version_minor, release_date, summary)
                       VALUES (%s,%s,%s,%s,%s) RETURNING id""",
                    (str(r["version"]), major, minor, r["date"], r.get("summary"))).fetchone()["id"]
                for ch in r.get("changes", []):
                    conn.execute(
                        """INSERT INTO release_changes (release_id, module_id, change_type, title, description)
                           VALUES (%s,%s,%s,%s,%s)""",
                        (rel[str(r["version"])], mod.get(ch.get("module")), ch["type"], ch["title"], ch.get("description")))
            rc = {r["code"]: conn.execute(
                "INSERT INTO root_causes (code, category, name, description) VALUES (%s,%s,%s,%s) RETURNING id",
                (r["code"], r["category"], r["name"], r.get("description"))).fetchone()["id"] for r in tax["root_causes"]}

            env = {}
            for c in tax["customers"]:
                cid = conn.execute(
                    "INSERT INTO customers (name, industry, tier, region) VALUES (%s,%s,%s,%s) RETURNING id",
                    (c["name"], c["industry"], c["tier"], c["region"])).fetchone()["id"]
                eid = conn.execute(
                    """INSERT INTO environments (customer_id, name, release_id, deployment, database, config_notes)
                       VALUES (%s,'PROD',%s,%s,%s,%s) RETURNING id""",
                    (cid, rel[str(c["version"])], c["deployment"], c.get("database"), c.get("config_notes"))).fetchone()["id"]
                env[c["key"]] = eid
                for m in c["modules"]:
                    conn.execute("INSERT INTO environment_modules (environment_id, module_id) VALUES (%s,%s)", (eid, mod[m]))
                for i in c["integrations"]:
                    conn.execute(
                        "INSERT INTO environment_integrations (environment_id, integration_id, version) VALUES (%s,%s,%s)",
                        (eid, integ[i["code"]], i.get("version")))

            # solutions first (cases reference them through case_solutions)
            sol_ids = {}
            for sol in data["solutions"]:
                num = int(sol["number"].split("-")[1])
                sol_ids[sol["number"]] = conn.execute(
                    """INSERT INTO solutions (solution_number, title, issue, environment_conditions, cause, resolution_steps,
                           verification_steps, root_cause_id, min_version, max_version, status, approved_by, approved_at,
                           date_created)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (sol["number"], sol["title"], sol["issue"].strip(), (sol.get("environment_conditions") or "").strip(),
                     (sol.get("cause") or "").strip(), sol["resolution_steps"].strip(),
                     (sol.get("verification_steps") or "").strip(), rc.get(sol.get("root_cause")),
                     sol.get("min_version"), sol.get("max_version"), sol.get("status", "validated"),
                     user_ids.get("lena"), _ts(min(c["date"] for c in sol["cases"]), 17),
                     _ts(min(c["date"] for c in sol["cases"]), 16))).fetchone()["id"]
                for m in sol.get("modules", []):
                    conn.execute("INSERT INTO solution_modules (solution_id, module_id) VALUES (%s,%s)", (sol_ids[sol["number"]], mod[m]))
                for i in sol.get("integrations", []):
                    conn.execute("INSERT INTO solution_integrations (solution_id, integration_id) VALUES (%s,%s)",
                                 (sol_ids[sol["number"]], integ[i]))
                conn.execute("SELECT setval('solution_number_seq', greatest(%s, (SELECT last_value FROM solution_number_seq)))", (num,))

            # all cases in chronological order -> case numbers follow time
            all_cases = []
            for sol in data["solutions"]:
                for c in sol["cases"]:
                    all_cases.append((c, sol))
            for c in data["open_cases"]:
                all_cases.append((c, None))
            all_cases.sort(key=lambda t: str(t[0]["date"]))

            holdout = []
            n_cases = n_steps = 0
            for c, sol in all_cases:
                resolved = sol is not None and c.get("outcome", "resolved") in ("resolved", "partial")
                status = c.get("status") or ("closed" if sol is not None else "investigating")
                case_id = conn.execute(
                    """INSERT INTO cases (title, customer_report, customer_goal, symptoms, error_messages, environment_id,
                           epim_version, module_id, root_cause_id, severity, status, channel, resolution_summary,
                           assigned_to, opened_at, resolved_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id, case_number""",
                    (c["title"], c["report"].strip(), c.get("goal"), c.get("symptoms"), c.get("errors") or None,
                     env[c["customer"]], str(c["version"]), mod.get(c.get("module")),
                     rc.get(sol.get("root_cause")) if resolved else None, c.get("severity", "S3"), status,
                     c.get("channel", "portal"), c.get("resolution") if resolved else None,
                     user_ids.get(c.get("engineer")), _ts(c["date"]),
                     _ts(c["date"] + dt.timedelta(days=2), 15) if resolved else None)).fetchone()
                n_cases += 1
                for seq, (action, finding, result) in enumerate(c.get("steps", []), 1):
                    conn.execute(
                        """INSERT INTO investigation_steps (case_id, seq, action, finding, result, source, author, created_at)
                           VALUES (%s,%s,%s,%s,%s,'engineer',%s,%s)""",
                        (case_id["id"], seq, action, finding, result, user_ids.get(c.get("engineer")),
                         _ts(c["date"], 10 + seq)))
                    n_steps += 1
                for other in c.get("other_solutions", []):
                    conn.execute(
                        """INSERT INTO case_solutions (case_id, solution_id, relation, outcome, version_at_apply, notes, applied_by, applied_at)
                           VALUES (%s,%s,'applied',%s,%s,%s,%s,%s)""",
                        (case_id["id"], sol_ids[other["solution"]], other["outcome"], str(c["version"]), other.get("notes"),
                         user_ids.get(c.get("engineer")), _ts(c["date"], 12)))
                if sol is not None:
                    conn.execute(
                        """INSERT INTO case_solutions (case_id, solution_id, relation, outcome, version_at_apply, notes, applied_by, applied_at)
                           VALUES (%s,%s,'applied',%s,%s,%s,%s,%s)""",
                        (case_id["id"], sol_ids[sol["number"]], c.get("outcome", "resolved"), str(c["version"]),
                         c.get("resolution"), user_ids.get(c.get("engineer")), _ts(c["date"] + dt.timedelta(days=1), 14)))
                if c.get("holdout"):
                    holdout.append({"case_id": str(case_id["id"]), "case_number": case_id["case_number"],
                                    "expected_solution": sol["number"] if sol else None, "title": c["title"]})

    eval_dir = Path(s.eval_dir)
    eval_dir.mkdir(parents=True, exist_ok=True)
    (eval_dir / "holdout.json").write_text(json.dumps(holdout, indent=2), encoding="utf-8")
    summary = {"solutions": len(data["solutions"]), "cases": n_cases, "investigation_steps": n_steps,
               "customers": len(tax["customers"]), "holdout_cases": len(holdout)}
    log.info("Seeded %s", summary)
    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="wipe and reload the dataset")
    args = parser.parse_args()
    print(json.dumps(seed(force=args.force), indent=2))


if __name__ == "__main__":
    main()
