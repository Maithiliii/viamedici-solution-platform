"""Hybrid search: thin Python wrapper around the search.hybrid_search SQL function."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import db
from .embeddings import get_embeddings
from .textutil import extract_codes

ALL_MODES = ("vector", "fts", "trgm")


@dataclass
class SearchQuery:
    text: str
    entity_types: tuple[str, ...] = ("case", "solution")
    module_codes: list[str] = field(default_factory=list)
    version_major: int | None = None
    integrations: list[str] = field(default_factory=list)
    exclude_ids: list[str] = field(default_factory=list)
    limit: int = 8
    modes: tuple[str, ...] = ALL_MODES
    use_boost: bool = True
    codes: str | None = None  # defaults to codes extracted from text


@dataclass
class SearchHit:
    entity_type: str
    entity_id: str
    number: str
    title: str
    score: float
    rrf_score: float
    boost: float
    snippet: str
    metadata: dict
    breakdown: dict

    def to_dict(self, snippet_len: int = 400) -> dict[str, Any]:
        m = self.metadata
        d: dict[str, Any] = {
            "type": self.entity_type, "number": self.number, "title": self.title,
            "score": round(self.score, 5), "status": m.get("status"),
            "modules": m.get("module_codes"), "integrations": m.get("integration_codes"),
            "match": self.breakdown,
            "snippet": (self.snippet or "")[:snippet_len],
        }
        if self.entity_type == "solution":
            d.update(valid_versions=f"{m.get('min_version') or 'any'} - {m.get('max_version') or 'latest'}",
                     track_record=f"applied {m.get('times_applied', 0)}x, resolved {m.get('times_successful', 0)}x, "
                                  f"failed {m.get('times_failed', 0)}x")
        else:
            d.update(version=m.get("version"), customer=m.get("customer"), solutions=m.get("solutions"))
        return d


def hybrid_search(q: SearchQuery) -> list[SearchHit]:
    embedding = get_embeddings().embed_query(q.text) if "vector" in q.modes else None
    codes = q.codes if q.codes is not None else " ".join(extract_codes(q.text))
    rows = db.fetch_all(
        """SELECT * FROM search.hybrid_search(
               %(text)s, %(emb)s::vector, %(codes)s, %(types)s, %(modules)s, %(vmajor)s,
               %(integrations)s, %(exclude)s::uuid[], %(limit)s, %(modes)s, %(boost)s)""",
        {
            "text": q.text, "emb": embedding, "codes": codes, "types": list(q.entity_types),
            "modules": q.module_codes, "vmajor": q.version_major, "integrations": q.integrations,
            "exclude": q.exclude_ids, "limit": q.limit, "modes": list(q.modes), "boost": q.use_boost,
        },
    )
    return [
        SearchHit(
            entity_type=r["entity_type"], entity_id=str(r["entity_id"]),
            number=r["metadata"].get("number", ""), title=r["metadata"].get("title", ""),
            score=r["score"], rrf_score=r["rrf_score"], boost=r["boost"], snippet=r["best_chunk"] or "",
            metadata=r["metadata"], breakdown=r["breakdown"],
        )
        for r in rows
    ]
