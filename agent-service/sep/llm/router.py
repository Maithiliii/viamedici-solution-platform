"""Model router: fallback chain, retry with backoff, client-side throttling.

Free tiers return 429/503 under load. The router tries each configured model
in order and remembers which one is healthy, so a demo does not fail because
one model is overloaded.
"""
from __future__ import annotations

import logging
import threading
import time
from functools import lru_cache
from typing import Any, Callable

from ..config import get_settings
from .base import ChatSession, LLMError, LLMResponse, LLMUnavailable, ToolCall, ToolSpec
from .gemini import GeminiProvider
from .openai_compat import OpenAICompatProvider

log = logging.getLogger("sep.llm")


class _Throttle:
    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            delta = time.monotonic() - self._last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta)
            self._last = time.monotonic()


class RouterProvider:
    """Implements the LLMProvider protocol on top of an ordered list of providers."""

    name = "router"

    def __init__(self, providers: list[Any], min_interval: float, attempts_per_model: int = 2):
        if not providers:
            raise LLMError("No LLM provider configured (set GEMINI_API_KEY or GROQ_API_KEY)")
        self.providers = providers
        self.attempts = attempts_per_model
        self.throttle = _Throttle(min_interval)
        self._preferred = 0

    @property
    def model(self) -> str:
        return self.providers[self._preferred].model

    def _order(self) -> list[int]:
        n = len(self.providers)
        return [(self._preferred + i) % n for i in range(n)]

    def _run(self, fn: Callable[[Any], LLMResponse]) -> LLMResponse:
        errors = []
        for idx in self._order():
            provider = self.providers[idx]
            for attempt in range(self.attempts):
                self.throttle.wait()
                try:
                    resp = fn(provider)
                    self._preferred = idx
                    return resp
                except LLMUnavailable as e:
                    errors.append(str(e))
                    log.warning("LLM unavailable (%s), attempt %s", e, attempt + 1)
                    time.sleep(1.5 * (attempt + 1))
        raise LLMUnavailable("All models unavailable: " + " | ".join(errors[-4:]))

    def complete(self, system: str, prompt: str, *, json_mode: bool = False,
                 temperature: float | None = None) -> LLMResponse:
        return self._run(lambda p: p.complete(system, prompt, json_mode=json_mode, temperature=temperature))

    def start_chat(self, system: str, tools: list[ToolSpec]) -> "RouterChat":
        return RouterChat(self, system, tools)


class RouterChat(ChatSession):
    def __init__(self, router: RouterProvider, system: str, tools: list[ToolSpec]):
        self.router = router
        self.idx = router._preferred
        self.session = router.providers[self.idx].start_chat(system, tools)
        self.model = self.session.model

    def _run(self, fn: Callable[[Any], LLMResponse]) -> LLMResponse:
        errors = []
        tried = 0
        while tried < len(self.router.providers):
            for attempt in range(self.router.attempts):
                self.router.throttle.wait()
                # A failed request must not leave half a turn in the history
                history = (self.session.contents if hasattr(self.session, "contents")
                           else getattr(self.session, "messages", None))
                before = len(history) if history is not None else 0
                try:
                    return fn(self.session)
                except LLMUnavailable as e:
                    if history is not None:
                        del history[before:]
                    errors.append(str(e))
                    time.sleep(1.5 * (attempt + 1))
            # switch to the next model of the same family, keeping the conversation
            tried += 1
            nxt = (self.idx + 1) % len(self.router.providers)
            candidate = self.router.providers[nxt]
            if type(candidate) is not type(self.router.providers[self.idx]):
                break
            self.idx = nxt
            self.session.switch_provider(candidate)  # type: ignore[attr-defined]
            self.model = candidate.model
            log.warning("Chat switched to model %s", candidate.model)
        raise LLMUnavailable("Chat models unavailable: " + " | ".join(errors[-4:]))

    def send_user(self, text: str) -> LLMResponse:
        return self._run(lambda s: s.send_user(text))

    def send_tool_results(self, results: list[tuple[ToolCall, Any]]) -> LLMResponse:
        return self._run(lambda s: s.send_tool_results(results))


@lru_cache
def get_llm() -> RouterProvider:
    s = get_settings()
    providers: list[Any] = []
    gemini = [GeminiProvider(s.gemini_api_key, m, s.gemini_base_url, s.llm_timeout_s, s.llm_temperature)
              for m in s.gemini_model_list] if s.gemini_api_key else []
    groq = [OpenAICompatProvider(s.groq_api_key, m, s.groq_base_url, s.llm_timeout_s, s.llm_temperature)
            for m in s.groq_model_list] if s.groq_api_key else []
    providers = (gemini + groq) if s.llm_provider == "gemini" else (groq + gemini)
    return RouterProvider(providers, s.llm_min_interval_s)
