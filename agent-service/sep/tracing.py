"""Agent observability: every run and every step is persisted.

agent_runs / agent_steps power the live trace in the Engineer Workspace,
debugging, cost tracking (tokens) and the evaluation harness.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from . import db
from .llm import Usage
from .textutil import truncate

log = logging.getLogger("sep.trace")


class Tracer:
    def __init__(self, graph: str, case_id: str | None, trigger: str = "manual", input: dict | None = None):
        self.graph = graph
        self.case_id = case_id
        self.usage = Usage()
        self.models: set[str] = set()
        self._seq = 0
        self._lock = threading.Lock()
        self._t0 = time.monotonic()
        row = db.fetch_one(
            """INSERT INTO agent_runs (case_id, graph, trigger, status, input)
               VALUES (%s, %s, %s, 'running', %s) RETURNING id""",
            (case_id, graph, trigger, json.dumps(input or {}, default=str)),
        )
        self.run_id = str(row["id"])

    def step(self, node: str, type: str, summary: str, payload: Any = None, tool_name: str | None = None) -> None:
        with self._lock:
            self._seq += 1
            seq = self._seq
        try:
            db.execute(
                """INSERT INTO agent_steps (run_id, seq, node, type, tool_name, summary, payload)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (self.run_id, seq, node, type, tool_name, truncate(summary, 1000),
                 json.dumps(payload, default=str) if payload is not None else None),
            )
        except Exception:  # tracing must never break an agent run
            log.exception("failed to write agent step")

    def add_usage(self, usage: Usage, model: str | None = None) -> None:
        self.usage.add(usage)
        if model:
            self.models.add(model)

    def finish(self, output: Any = None, error: str | None = None) -> None:
        db.execute(
            """UPDATE agent_runs SET status = %s, output = %s, error = %s, finished_at = now(),
                      latency_ms = %s, llm_calls = %s, tokens_in = %s, tokens_out = %s, model = %s
               WHERE id = %s""",
            ("failed" if error else "succeeded", json.dumps(output, default=str) if output is not None else None,
             error, int((time.monotonic() - self._t0) * 1000), self.usage.calls, self.usage.tokens_in,
             self.usage.tokens_out, ",".join(sorted(self.models)) or None, self.run_id),
        )
