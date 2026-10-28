"""Builds the hybrid search index (search.knowledge_chunks) from cases and solutions.

Chunks per entity:
  case:     'problem'    title + customer report + symptoms + errors
            'resolution' root cause + resolution summary + confirmed findings (resolved cases only)
  solution: 'issue'      title + issue + environment conditions
            'fix'        cause + resolution steps
Metadata carries the structured features used for boosting and for the UI.
"""
from __future__ import annotations

import json
import logging
from typing import Iterable

from . import db
from .embeddings import get_embeddings
from .textutil import extract_codes, join, version_major

log = logging.getLogger("sep.indexer")

_CASE_SQL = """
SELECT c.id, c.case_number, c.title, c.customer_report, c.symptoms, c.error_messages, c.status,
       c.epim_version, c.resolution_summary, c.customer_goal,
       m.code AS module_code, rc.name AS root_cause_name, cu.name AS customer_name,
       coalesce((SELECT array_agg(i.code) FROM environment_integrations ei
                 JOIN integrations i ON i.id = ei.integration_id
                 WHERE ei.environment_id = c.environment_id), '{}') AS integration_codes,
       coalesce((SELECT string_agg(s.action || ': ' || coalesce(s.finding, ''), E'\n' ORDER BY s.seq)
                 FROM investigation_steps s WHERE s.case_id = c.id AND s.result = 'confirmed'), '') AS confirmed,
       coalesce((SELECT jsonb_agg(jsonb_build_object('number', so.solution_number, 'outcome', cs.outcome))
                 FROM case_solutions cs JOIN solutions so ON so.id = cs.solution_id
                 WHERE cs.case_id = c.id AND cs.relation = 'applied'), '[]') AS solutions
FROM cases c
LEFT JOIN modules m       ON m.id = c.module_id
LEFT JOIN root_causes rc  ON rc.id = c.root_cause_id
LEFT JOIN environments e  ON e.id = c.environment_id
LEFT JOIN customers cu    ON cu.id = e.customer_id
{where}
"""

_SOLUTION_SQL = """
SELECT s.id, s.solution_number, s.title, s.issue, s.environment_conditions, s.cause, s.resolution_steps,
       s.status, s.min_version, s.max_version, s.times_applied, s.times_successful, s.times_failed,
       rc.name AS root_cause_name,
       coalesce((SELECT array_agg(m.code) FROM solution_modules sm JOIN modules m ON m.id = sm.module_id
                 WHERE sm.solution_id = s.id), '{}') AS module_codes,
       coalesce((SELECT array_agg(i.code) FROM solution_integrations si JOIN integrations i ON i.id = si.integration_id
                 WHERE si.solution_id = s.id), '{}') AS integration_codes
FROM solutions s
LEFT JOIN root_causes rc ON rc.id = s.root_cause_id
{where}
"""


def _case_chunks(c: dict) -> list[tuple[str, str, str, dict]]:
    meta = {
        "number": c["case_number"], "title": c["title"], "status": c["status"],
        "module_codes": [c["module_code"]] if c["module_code"] else [],
        "integration_codes": list(c["integration_codes"] or []),
        "version": c["epim_version"], "version_major": version_major(c["epim_version"]),
        "customer": c["customer_name"], "solutions": c["solutions"],
    }
    problem = join(c["title"], c["customer_report"], c["symptoms"], c["error_messages"])
    chunks = [("problem", problem, " ".join(extract_codes(join(c["error_messages"], c["customer_report"]))), meta)]
    if c["status"] in ("resolved", "knowledge_review", "closed") and c["resolution_summary"]:
        resolution = join(f"Root cause: {c['root_cause_name']}" if c["root_cause_name"] else None,
                          c["resolution_summary"], c["confirmed"])
        chunks.append(("resolution", resolution, " ".join(extract_codes(resolution)), meta))
    return chunks


def _solution_chunks(s: dict) -> list[tuple[str, str, str, dict]]:
    meta = {
        "number": s["solution_number"], "title": s["title"], "status": s["status"],
        "module_codes": list(s["module_codes"] or []),
        "integration_codes": list(s["integration_codes"] or []),
        "min_version": s["min_version"], "max_version": s["max_version"],
        "min_major": version_major(s["min_version"]), "max_major": version_major(s["max_version"]),
        "times_applied": s["times_applied"], "times_successful": s["times_successful"],
        "times_failed": s["times_failed"],
    }
    issue = join(s["title"], s["issue"], s["environment_conditions"])
    fix = join(f"Root cause: {s['root_cause_name']}" if s["root_cause_name"] else None,
               s["cause"], s["resolution_steps"])
    return [
        ("issue", issue, " ".join(extract_codes(issue)), meta),
        ("fix", fix, " ".join(extract_codes(fix)), meta),
    ]


def _upsert(entity_type: str, entity_id, chunks: list[tuple[str, str, str, dict]]) -> int:
    if not chunks:
        return 0
    vectors = get_embeddings().embed_documents([c[1] for c in chunks])
    with db.connection() as conn:
        conn.execute("DELETE FROM search.knowledge_chunks WHERE entity_type = %s AND entity_id = %s",
                     (entity_type, entity_id))
        for (chunk_type, content, codes, meta), vec in zip(chunks, vectors):
            conn.execute(
                """INSERT INTO search.knowledge_chunks (entity_type, entity_id, chunk_type, content, codes, embedding, metadata)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (entity_type, entity_id, chunk_type, content, codes, vec, json.dumps(meta, default=str)),
            )
    return len(chunks)


def index_case(case_id: str) -> int:
    rows = db.fetch_all(_CASE_SQL.replace("{where}", "WHERE c.id = %s"), (case_id,))
    return sum(_upsert("case", r["id"], _case_chunks(r)) for r in rows)


def index_solution(solution_id: str) -> int:
    rows = db.fetch_all(_SOLUTION_SQL.replace("{where}", "WHERE s.id = %s"), (solution_id,))
    return sum(_upsert("solution", r["id"], _solution_chunks(r)) for r in rows)


def remove(entity_type: str, entity_id: str) -> None:
    db.execute("DELETE FROM search.knowledge_chunks WHERE entity_type = %s AND entity_id = %s",
               (entity_type, entity_id))


def rebuild_all(progress: bool = True) -> dict:
    cases = db.fetch_all(_CASE_SQL.replace("{where}", ""))
    solutions = db.fetch_all(_SOLUTION_SQL.replace("{where}", "WHERE s.status <> 'archived'"))
    db.execute("TRUNCATE search.knowledge_chunks")
    n_chunks = 0
    for r in _iter(cases, "cases", progress):
        n_chunks += _upsert("case", r["id"], _case_chunks(r))
    for r in _iter(solutions, "solutions", progress):
        n_chunks += _upsert("solution", r["id"], _solution_chunks(r))
    return {"cases": len(cases), "solutions": len(solutions), "chunks": n_chunks}


def _iter(rows: list[dict], label: str, progress: bool) -> Iterable[dict]:
    for i, r in enumerate(rows, 1):
        if progress and (i % 20 == 0 or i == len(rows)):
            log.info("indexed %s/%s %s", i, len(rows), label)
        yield r
