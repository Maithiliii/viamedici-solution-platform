"""Google Gemini provider (REST, generateContent) with native function calling."""
from __future__ import annotations

import copy
from typing import Any

import httpx

from .base import ChatSession, LLMError, LLMResponse, LLMUnavailable, ToolCall, ToolSpec, Usage

_TRANSIENT = {408, 429, 500, 502, 503, 504}


def _usage(data: dict) -> Usage:
    u = data.get("usageMetadata", {}) or {}
    return Usage(
        tokens_in=u.get("promptTokenCount", 0) or 0,
        tokens_out=(u.get("candidatesTokenCount", 0) or 0) + (u.get("thoughtsTokenCount", 0) or 0),
        calls=1,
    )


def _clean_schema(schema: dict) -> dict:
    """Gemini function declarations accept an OpenAPI-like subset of JSON schema."""
    s = {k: v for k, v in schema.items() if k not in ("title", "default", "additionalProperties", "$schema")}
    if "properties" in s:
        s["properties"] = {k: _clean_schema(v) for k, v in s["properties"].items()}
    if "items" in s and isinstance(s["items"], dict):
        s["items"] = _clean_schema(s["items"])
    return s


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str, base_url: str, timeout_s: float, temperature: float):
        if not api_key:
            raise LLMError("GEMINI_API_KEY is not set")
        self.model = model
        self._url = f"{base_url}/models/{model}:generateContent"
        self._headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
        self._timeout = timeout_s
        self._temperature = temperature

    # -- low level ---------------------------------------------------------
    def _post(self, body: dict) -> dict:
        try:
            r = httpx.post(self._url, json=body, headers=self._headers, timeout=self._timeout)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise LLMUnavailable(f"{self.model}: {type(e).__name__}") from e
        if r.status_code in _TRANSIENT:
            raise LLMUnavailable(f"{self.model}: HTTP {r.status_code} {r.text[:200]}")
        if r.status_code >= 400:
            raise LLMError(f"{self.model}: HTTP {r.status_code} {r.text[:400]}")
        return r.json()

    def _parse(self, data: dict) -> tuple[LLMResponse, dict]:
        cands = data.get("candidates") or []
        if not cands:
            raise LLMUnavailable(f"{self.model}: empty response ({data.get('promptFeedback')})")
        content = cands[0].get("content") or {"role": "model", "parts": []}
        texts, calls = [], []
        for part in content.get("parts", []):
            if part.get("thought"):
                continue
            if "text" in part:
                texts.append(part["text"])
            if "functionCall" in part:
                fc = part["functionCall"]
                calls.append(ToolCall(name=fc["name"], args=fc.get("args") or {}, id=fc.get("id")))
        return LLMResponse(text="".join(texts), tool_calls=calls, usage=_usage(data), model=self.model), content

    def _config(self, json_mode: bool, temperature: float | None) -> dict:
        cfg: dict[str, Any] = {"temperature": self._temperature if temperature is None else temperature}
        if json_mode:
            cfg["responseMimeType"] = "application/json"
        return cfg

    # -- public API ----------------------------------------------------------
    def complete(self, system: str, prompt: str, *, json_mode: bool = False,
                 temperature: float | None = None) -> LLMResponse:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": self._config(json_mode, temperature),
        }
        resp, _ = self._parse(self._post(body))
        return resp

    def start_chat(self, system: str, tools: list[ToolSpec]) -> "GeminiChat":
        return GeminiChat(self, system, tools)


class GeminiChat(ChatSession):
    def __init__(self, provider: GeminiProvider, system: str, tools: list[ToolSpec]):
        self.provider = provider
        self.model = provider.model
        self.system = system
        self.tools = tools
        self.contents: list[dict] = []

    def _send(self) -> LLMResponse:
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": self.system}]},
            "contents": self.contents,
            "generationConfig": self.provider._config(False, None),
        }
        if self.tools:
            body["tools"] = [{"functionDeclarations": [
                {"name": t.name, "description": t.description, "parameters": _clean_schema(t.parameters)}
                for t in self.tools
            ]}]
        resp, content = self.provider._parse(self.provider._post(body))
        # Keep the model turn verbatim (incl. thought signatures required by Gemini 3 tool calling)
        content = copy.deepcopy(content)
        content["role"] = "model"
        self.contents.append(content)
        return resp

    def send_user(self, text: str) -> LLMResponse:
        self.contents.append({"role": "user", "parts": [{"text": text}]})
        return self._send()

    def send_tool_results(self, results: list[tuple[ToolCall, Any]]) -> LLMResponse:
        parts = []
        for call, result in results:
            fr: dict[str, Any] = {"name": call.name, "response": {"result": result}}
            if call.id:
                fr["id"] = call.id
            parts.append({"functionResponse": fr})
        self.contents.append({"role": "user", "parts": parts})
        return self._send()

    def switch_provider(self, provider: GeminiProvider) -> None:
        """Continue the conversation on another model (thought signatures are model bound)."""
        self.provider = provider
        self.model = provider.model
        for c in self.contents:
            for p in c.get("parts", []):
                p.pop("thoughtSignature", None)
                p.pop("thought_signature", None)
