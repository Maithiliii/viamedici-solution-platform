"""Provider-agnostic LLM interface.

The agents only depend on this module. Concrete providers (Gemini, any
OpenAI-compatible API such as Groq) translate to their wire formats. Switching
provider is configuration, not code: an enterprise requirement (vendor
independence, on-prem models later) and a resilience feature for free tiers.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class LLMUnavailable(LLMError):
    """Transient: rate limit, overload or timeout. The router falls back to the next model."""


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON schema (object)
    fn: Callable[..., Any]


@dataclass
class ToolCall:
    name: str
    args: dict
    id: str | None = None


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    calls: int = 0

    def add(self, other: "Usage") -> None:
        self.tokens_in += other.tokens_in
        self.tokens_out += other.tokens_out
        self.calls += other.calls


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    model: str = ""


class ChatSession(Protocol):
    """A multi-turn conversation with tool calling (provider specific state)."""

    model: str

    def send_user(self, text: str) -> LLMResponse: ...

    def send_tool_results(self, results: list[tuple[ToolCall, Any]]) -> LLMResponse: ...


class LLMProvider(Protocol):
    name: str

    def complete(self, system: str, prompt: str, *, json_mode: bool = False,
                 temperature: float | None = None) -> LLMResponse: ...

    def start_chat(self, system: str, tools: list[ToolSpec]) -> ChatSession: ...


# ---------------------------------------------------------------------------
# Structured output helper (works for every provider)
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def extract_json(text: str) -> Any:
    text = _FENCE.sub("", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # tolerate prose around the object
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


def schema_hint(model: type[BaseModel]) -> str:
    return json.dumps(model.model_json_schema(), indent=None)


def generate_structured(provider: LLMProvider, system: str, prompt: str, schema: type[T],
                        usage: Usage | None = None, retries: int = 2,
                        temperature: float | None = None) -> T:
    """Ask for JSON matching `schema`, validate with Pydantic, retry with the error."""
    full_prompt = (
        f"{prompt}\n\nRespond with ONLY a JSON object that validates against this JSON schema:\n"
        f"{schema_hint(schema)}"
    )
    last_error = ""
    for _ in range(retries + 1):
        p = full_prompt if not last_error else (
            f"{full_prompt}\n\nYour previous answer was invalid: {last_error[:800]}\nReturn corrected JSON only."
        )
        resp = provider.complete(system, p, json_mode=True, temperature=temperature)
        if usage is not None:
            usage.add(resp.usage)
        try:
            return schema.model_validate(extract_json(resp.text))
        except (json.JSONDecodeError, ValidationError, ValueError) as e:
            last_error = str(e)
    raise LLMError(f"Structured output failed after {retries + 1} attempts: {last_error[:300]}")
