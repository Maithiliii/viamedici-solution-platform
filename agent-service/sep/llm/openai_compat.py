"""OpenAI-compatible chat completions provider (Groq, local vLLM/Ollama, OpenAI, ...)."""
from __future__ import annotations

import json
from typing import Any

import httpx

from .base import ChatSession, LLMError, LLMResponse, LLMUnavailable, ToolCall, ToolSpec, Usage

_TRANSIENT = {408, 429, 500, 502, 503, 504}


class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(self, api_key: str, model: str, base_url: str, timeout_s: float, temperature: float):
        if not api_key:
            raise LLMError("API key for OpenAI-compatible provider is not set")
        self.model = model
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._timeout = timeout_s
        self._temperature = temperature

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
        msg = data["choices"][0]["message"]
        calls = []
        for tc in msg.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(name=tc["function"]["name"], args=args, id=tc.get("id")))
        u = data.get("usage") or {}
        usage = Usage(tokens_in=u.get("prompt_tokens", 0), tokens_out=u.get("completion_tokens", 0), calls=1)
        return LLMResponse(text=msg.get("content") or "", tool_calls=calls, usage=usage, model=self.model), msg

    def complete(self, system: str, prompt: str, *, json_mode: bool = False,
                 temperature: float | None = None) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "temperature": self._temperature if temperature is None else temperature,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        resp, _ = self._parse(self._post(body))
        return resp

    def start_chat(self, system: str, tools: list[ToolSpec]) -> "OpenAICompatChat":
        return OpenAICompatChat(self, system, tools)


class OpenAICompatChat(ChatSession):
    def __init__(self, provider: OpenAICompatProvider, system: str, tools: list[ToolSpec]):
        self.provider = provider
        self.model = provider.model
        self.tools = tools
        self.messages: list[dict] = [{"role": "system", "content": system}]

    def _send(self) -> LLMResponse:
        body: dict[str, Any] = {"model": self.provider.model, "messages": self.messages,
                                "temperature": self.provider._temperature}
        if self.tools:
            body["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.parameters}} for t in self.tools]
        resp, msg = self.provider._parse(self.provider._post(body))
        self.messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
        return resp

    def send_user(self, text: str) -> LLMResponse:
        self.messages.append({"role": "user", "content": text})
        return self._send()

    def send_tool_results(self, results: list[tuple[ToolCall, Any]]) -> LLMResponse:
        for call, result in results:
            self.messages.append({"role": "tool", "tool_call_id": call.id or call.name,
                                  "content": json.dumps(result, default=str)[:12000]})
        return self._send()

    def switch_provider(self, provider: OpenAICompatProvider) -> None:
        self.provider = provider
        self.model = provider.model
