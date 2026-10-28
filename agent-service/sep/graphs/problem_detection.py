"""Graph E: Problem Detection (ITIL problem management).

Clusters OPEN cases without a resolving solution by embedding similarity (in SQL),
then lets the LLM judge which clusters share one underlying cause and propose a
Problem record (with release-change evidence and known workarounds).
"""
from __future__ import annotations

import json
from typing import Any

from .. import db
from ..agents import knowledge, prompts
from ..agents.schemas import ProblemProposals
from ..agents import suggestions
from ..llm import generate_structured, get_llm
from ..tracing import Tracer

_PAIRS_SQL = """
WITH open_cases AS (
    SELECT c.id, c.case_number, c.title, c.epim_version, k.embedding
    FROM cases c
    JOIN search.knowledge_chunks k ON k.entity_type = 'case' AND k.entity_id = c.id AND k.chunk_type = 'problem'
    WHERE c.status NOT IN ('resolved', 'knowledge_review', 'closed') AND c.problem_id IS NULL
      AND NOT EXISTS (SELECT 1 FROM case_solutions cs WHERE cs.case_id = c.id AND cs.outcome = 'resolved')
)
SELECT a.case_number AS a, b.case_number AS b, 1 - (a.embedding <=> b.embedding) AS similarity
FROM open_cases a JOIN open_cases b ON a.case_number < b.case_number
WHERE 1 - (a.embedding <=> b.embedding) >= %s
"""


def _clusters(pairs: list[dict]) -> list[set[str]]:
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for p in pairs:
        parent[find(p["a"])] = find(p["b"])
    groups: dict[str, set[str]] = {}
    for x in list(parent):
        groups.setdefault(find(x), set()).add(x)
    return [g for g in groups.values() if len(g) >= 3]


def run(trigger: str = "manual", threshold: float = 0.72) -> dict[str, Any]:
    tracer = Tracer("problem_detection", None, trigger, {"threshold": threshold})
    try:
        pairs = db.fetch_all(_PAIRS_SQL, (threshold,))
        clusters = _clusters(pairs)
        tracer.step("cluster", "tool_result", f"{len(pairs)} similar pair(s) among open cases -> {len(clusters)} cluster(s) of 3+",
                    payload=[sorted(c) for c in clusters], tool_name="embedding_clustering")
        if not clusters:
            tracer.finish({"problems": []})
            return {"run_id": tracer.run_id, "problems": []}
        material, case_ids = [], {}
        for cl in clusters:
            details = [knowledge.case_detail(n) for n in sorted(cl)]
            for d in details:
                row = db.fetch_one("SELECT id FROM cases WHERE case_number = %s", (d["number"],))
                case_ids[d["number"]] = str(row["id"])
            versions = sorted({d["version"] for d in details if d["version"]})
            changes = knowledge.release_changes("0.0", versions[-1] if versions else None, ["EXPORT", "MEDIA", "JOBS"])
            material.append({"cases": details, "versions": versions,
                             "release_changes": [c for c in changes if c["version"] in versions]})
        result = generate_structured(get_llm(), prompts.PROBLEMS,
                                     f"CLUSTERS OF OPEN CASES:\n{json.dumps(material, default=str, indent=1)}",
                                     ProblemProposals, usage=tracer.usage)
        tracer.models.add(get_llm().model)
        proposals = [p for p in result.problems if len([n for n in p.case_numbers if n in case_ids]) >= 2]
        db.execute("UPDATE ai_suggestions SET status = 'superseded' WHERE type = 'problem' AND status = 'pending'")
        rows = [suggestions.problem_row(tracer.run_id, p, case_ids) for p in proposals]
        suggestions.write(rows)
        tracer.step("write", "output", f"Proposed {len(rows)} problem record(s) for review",
                    payload=[p.model_dump() for p in proposals])
        output = {"problems": [p.model_dump() for p in proposals]}
        tracer.finish(output)
        return {"run_id": tracer.run_id, **output}
    except Exception as e:
        tracer.step("error", "error", f"{type(e).__name__}: {e}")
        tracer.finish(error=f"{type(e).__name__}: {e}")
        raise
