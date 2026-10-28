"""HTTP API of the agent service.

Called by Directus (Flows on data events, and the SEP endpoint extension on behalf
of logged-in users). Protected by a shared secret; not exposed to browsers.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from . import indexer
from .agents import knowledge, steps
from .agents.tools import RetrievalLedger
from .config import get_settings
from .graphs import case_analysis, investigation, knowledge_capture, problem_detection
from .search import SearchQuery, hybrid_search
from .tracing import Tracer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("sep.api")

app = FastAPI(title="SEP Agent Service", version="1.0.0",
              description="Agents, hybrid search and knowledge capture for the Solution Enablement Platform")

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent")
_inflight: set[tuple[str, str]] = set()
_lock = threading.Lock()


def require_secret(x_sep_secret: str | None = Header(default=None)) -> None:
    secret = get_settings().agent_service_secret
    if secret and x_sep_secret != secret:
        raise HTTPException(status_code=401, detail="invalid service secret")


def _submit(kind: str, key: str, fn: Callable[[], Any]) -> dict:
    with _lock:
        if (kind, key) in _inflight:
            return {"accepted": False, "reason": "already running"}
        _inflight.add((kind, key))

    def job() -> None:
        try:
            fn()
        except Exception:
            log.exception("%s for %s failed", kind, key)
        finally:
            with _lock:
                _inflight.discard((kind, key))

    _executor.submit(job)
    return {"accepted": True}


class CaseRequest(BaseModel):
    case_id: str
    trigger: str = "manual"
    wait: bool = False


class AskRequest(BaseModel):
    case_id: str
    question: str


class FlowEvent(BaseModel):
    event: str | None = None
    collection: str | None = None
    key: str | None = None
    keys: list[str] | None = None
    payload: dict | None = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/runs/case-analysis", dependencies=[Depends(require_secret)])
def run_case_analysis(req: CaseRequest) -> dict:
    if req.wait:
        return case_analysis.run(req.case_id, req.trigger)
    return _submit("case_analysis", req.case_id, lambda: case_analysis.run(req.case_id, req.trigger))


@app.post("/runs/investigation", dependencies=[Depends(require_secret)])
def run_investigation(req: CaseRequest) -> dict:
    if req.wait:
        return investigation.run(req.case_id, req.trigger)
    return _submit("investigation", req.case_id, lambda: investigation.run(req.case_id, req.trigger))


@app.post("/runs/knowledge-capture", dependencies=[Depends(require_secret)])
def run_capture(req: CaseRequest) -> dict:
    if req.wait:
        return knowledge_capture.run(req.case_id, req.trigger)
    return _submit("capture", req.case_id, lambda: knowledge_capture.run(req.case_id, req.trigger))


@app.post("/runs/problem-detection", dependencies=[Depends(require_secret)])
def run_problem_detection(wait: bool = False) -> dict:
    if wait:
        return problem_detection.run()
    return _submit("problems", "all", problem_detection.run)


@app.post("/ask", dependencies=[Depends(require_secret)])
def ask(req: AskRequest) -> dict:
    tracer = Tracer("ask_case", req.case_id, "manual", req.model_dump())
    try:
        ctx = knowledge.load_case(req.case_id)
        answer = steps.ask(ctx, req.question, tracer, RetrievalLedger(), {})
        tracer.finish(answer.model_dump())
        return {"run_id": tracer.run_id, **answer.model_dump()}
    except Exception as e:
        tracer.finish(error=str(e))
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")


@app.post("/flows/case-event", dependencies=[Depends(require_secret)])
def case_event(ev: FlowEvent) -> dict:
    """Single webhook for Directus Flows on the cases collection."""
    keys = ev.keys or ([ev.key] if ev.key else [])
    status = (ev.payload or {}).get("status")
    started = []
    for key in keys:
        if ev.event and ev.event.endswith("items.create"):
            started.append(_submit("case_analysis", key, lambda k=key: case_analysis.run(k, "flow")))
            _executor.submit(indexer.index_case, key)
        elif status == "resolved":
            started.append(_submit("capture", key, lambda k=key: knowledge_capture.run(k, "flow")))
            _executor.submit(indexer.index_case, key)
        elif ev.payload and set(ev.payload) & {"title", "customer_report", "symptoms", "error_messages",
                                               "resolution_summary", "status"}:
            _executor.submit(indexer.index_case, key)
    return {"handled": len(keys), "started": started}


@app.post("/index/{entity}/{entity_id}", dependencies=[Depends(require_secret)])
def reindex(entity: str, entity_id: str) -> dict:
    if entity == "case":
        return {"chunks": indexer.index_case(entity_id)}
    if entity == "solution":
        return {"chunks": indexer.index_solution(entity_id)}
    raise HTTPException(status_code=400, detail="entity must be case or solution")


@app.post("/index/rebuild", dependencies=[Depends(require_secret)])
def rebuild() -> dict:
    return indexer.rebuild_all()


@app.get("/search", dependencies=[Depends(require_secret)])
def search(q: str, scope: str = "all", module: str | None = None, version_major: int | None = None,
           limit: int = 10) -> list[dict]:
    types = {"all": ("case", "solution"), "solutions": ("solution",), "cases": ("case",)}[scope]
    hits = hybrid_search(SearchQuery(text=q, entity_types=types, module_codes=[module] if module else [],
                                     version_major=version_major, limit=limit))
    return [{**h.to_dict(), "id": h.entity_id} for h in hits]
