"""ReAct-style tool loop (reason -> act -> observe) on top of native function calling.

The loop ends when the model calls the designated `finish` tool, whose
arguments are validated with a Pydantic schema. Every tool call and result is
written to the trace so engineers can see *how* an answer was found.
"""
from __future__ import annotations

import json
import logging
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from ..llm import LLMError, ToolCall, ToolSpec, get_llm
from ..textutil import truncate
from ..tracing import Tracer

log = logging.getLogger("sep.react")
T = TypeVar("T", bound=BaseModel)


def _summarize_args(args: dict) -> str:
    return ", ".join(f"{k}={truncate(json.dumps(v, default=str), 80)}" for k, v in args.items())


def _summarize_result(result: Any) -> str:
    if isinstance(result, list):
        items = [r.get("number") or r.get("version") or r.get("case") for r in result if isinstance(r, dict)]
        items = [i for i in items if i]
        return f"{len(result)} results" + (f": {', '.join(items[:8])}" if items else "")
    if isinstance(result, dict):
        if "error" in result:
            return f"error: {result['error']}"
        return truncate(result.get("number", "") + " " + str(result.get("title", "")), 120) or "ok"
    return truncate(str(result), 120)


def run_tool_agent(*, system: str, task: str, tools: list[ToolSpec], finish_schema: type[T], tracer: Tracer,
                   node: str, max_steps: int = 6) -> T:
    finish = ToolSpec(
        name="finish",
        description="Call exactly once when you are done. Submit your final, structured answer.",
        parameters=_strip_titles(finish_schema.model_json_schema()),
        fn=lambda **kw: kw,
    )
    registry = {t.name: t for t in tools}
    chat = get_llm().start_chat(system, [*tools, finish])
    resp = chat.send_user(task)
    tracer.add_usage(resp.usage, resp.model)

    for step in range(max_steps + 2):
        if resp.text.strip() and resp.tool_calls:
            tracer.step(node, "thought", resp.text.strip())
        if not resp.tool_calls:
            if resp.text.strip():
                tracer.step(node, "thought", resp.text.strip())
            resp = chat.send_user("Continue. Use the tools if you need more evidence, otherwise call `finish`.")
            tracer.add_usage(resp.usage, resp.model)
            continue

        results: list[tuple[ToolCall, Any]] = []
        for call in resp.tool_calls:
            if call.name == "finish":
                try:
                    out = finish_schema.model_validate(call.args)
                    tracer.step(node, "output", "Agent finished", payload=out.model_dump(), tool_name="finish")
                    return out
                except ValidationError as e:
                    results.append((call, {"error": f"Invalid finish arguments: {str(e)[:600]}. Fix and call finish again."}))
                    continue
            tool = registry.get(call.name)
            tracer.step(node, "tool_call", f"{call.name}({_summarize_args(call.args)})", payload=call.args,
                        tool_name=call.name)
            if tool is None:
                result: Any = {"error": f"Unknown tool {call.name}"}
            else:
                try:
                    result = tool.fn(**call.args)
                except TypeError as e:
                    result = {"error": f"Bad arguments: {e}"}
                except Exception as e:  # tool failures are shown to the model, not raised
                    log.exception("tool %s failed", call.name)
                    result = {"error": f"{type(e).__name__}: {e}"}
            tracer.step(node, "tool_result", _summarize_result(result), payload=result, tool_name=call.name)
            results.append((call, result))

        if step >= max_steps - 1:
            results_note = "Step budget exhausted. Call `finish` NOW with your best answer."
            resp = chat.send_tool_results(results)
            tracer.add_usage(resp.usage, resp.model)
            if not any(c.name == "finish" for c in resp.tool_calls):
                resp = chat.send_user(results_note)
                tracer.add_usage(resp.usage, resp.model)
            continue
        resp = chat.send_tool_results(results)
        tracer.add_usage(resp.usage, resp.model)

    raise LLMError(f"{node}: agent did not finish within {max_steps} steps")


def _strip_titles(schema: dict) -> dict:
    """Inline $defs and drop 'title' keys so every provider accepts the schema."""
    defs = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].split("/")[-1]])
            if "anyOf" in node:  # Optional[X] -> X (nullable handled by validation)
                options = [o for o in node["anyOf"] if o.get("type") != "null"]
                merged = resolve(options[0]) if options else {"type": "string"}
                if "description" in node:
                    merged = {**merged, "description": node["description"]}
                return merged
            return {k: resolve(v) for k, v in node.items() if k not in ("title", "$defs", "default")}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)
