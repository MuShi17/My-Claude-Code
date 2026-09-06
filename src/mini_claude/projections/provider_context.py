"""Provider adapters for canonical model-replay messages."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .model_replay_projection import ModelReplayProjection, ModelReplayResult
from ..provider_content import materialize_tool_result
from ..runtime_event import canonical_json_bytes


@dataclass(frozen=True, slots=True)
class ProviderContext:
    provider: str
    high_water: int
    source_digest: str
    projection_digest: str
    messages: tuple[dict[str, Any], ...]
    diagnostics: tuple[Any, ...]
    context_epoch: str


def _without_runtime_id(message: dict[str, Any]) -> dict[str, Any]:
    value = dict(message)
    value.pop("runtime_event_id", None)
    value.pop("context_type", None)
    return value


def _anthropic_thinking_block(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict) or item.get("kind") != "thinking":
        return None
    signature = item.get("signature")
    # Anthropic signed thinking is provider-native state.  An unsigned or
    # foreign-provider reasoning item is deliberately omitted rather than
    # sending a custom block the API cannot validate.
    if not isinstance(signature, str) or not signature:
        return None
    return {
        "type": "thinking",
        "thinking": item.get("text", ""),
        "signature": signature,
    }


def _tool_result_content(value: Any) -> str | list[Any]:
    """Materialize tool results with one deterministic Anthropic boundary."""

    return materialize_tool_result(value, provider="anthropic")


def _openai_arguments(value: Any) -> str:
    """Return the strict Chat Completions function.arguments string."""

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            # A malformed historical argument is still represented as valid
            # JSON rather than leaking a mapping into the provider payload.
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return canonical_json_bytes(parsed).decode("utf-8")
    return canonical_json_bytes(value).decode("utf-8")


def _openai_tool_call(call: Any) -> dict[str, Any]:
    call = call if isinstance(call, dict) else {}
    return {
        "id": call.get("id"),
        "type": "function",
        "function": {
            "name": call.get("name"),
            "arguments": _openai_arguments(call.get("arguments", {})),
        },
    }


def _openai_user_content(value: Any) -> str | list[Any]:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(
        isinstance(item, dict) and item.get("kind") == "text" and isinstance(item.get("text"), str)
        for item in value
    ):
        return "\n".join(item["text"] for item in value)
    return materialize_tool_result(value, provider="openai")


def _anthropic_messages(messages: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    output: list[dict[str, Any]] = []
    pending_thinking: list[dict[str, Any]] = []

    def flush_thinking() -> None:
        if pending_thinking:
            output.append({"role": "assistant", "content": list(pending_thinking)})
            pending_thinking.clear()

    for source in messages:
        message = _without_runtime_id(source)
        role = message.get("role")
        if role == "assistant" and isinstance(message.get("content"), list):
            thinking = [
                block
                for item in message["content"]
                if (block := _anthropic_thinking_block(item)) is not None
            ]
            # The neutral projection may contain unsigned or foreign-provider
            # thinking items.  They are not valid Anthropic blocks and must
            # never pass through as the projection's ``kind`` shape.
            other_content = [
                item
                for item in message["content"]
                if not (isinstance(item, dict) and item.get("kind") == "thinking")
            ]
            if thinking and not message.get("tool_calls"):
                pending_thinking.extend(thinking)
                if not other_content:
                    continue
            elif not other_content and not message.get("tool_calls"):
                # An unsigned/foreign-provider thinking-only message has no
                # provider-valid content left after safe degradation.
                continue
            message = {**message, "content": other_content}

        if role == "assistant" and message.get("tool_calls"):
            content = list(pending_thinking)
            content.extend(
                {
                    "type": "tool_use",
                    "id": call.get("id"),
                    "name": call.get("name"),
                    "input": call.get("arguments", {}),
                }
                for call in message["tool_calls"]
            )
            output.append({"role": "assistant", "content": content})
            pending_thinking.clear()
        elif role == "assistant":
            content = message.get("content", "")
            if pending_thinking:
                blocks = list(pending_thinking)
                if isinstance(content, str) and content:
                    blocks.append({"type": "text", "text": content})
                elif isinstance(content, list):
                    blocks.extend(content)
                output.append({"role": "assistant", "content": blocks})
                pending_thinking.clear()
            else:
                output.append({"role": "assistant", "content": content})
        elif role == "user":
            flush_thinking()
            output.append({"role": "user", "content": message.get("content", "")})
        elif role == "tool":
            flush_thinking()
            tool_result = {
                "type": "tool_result",
                "tool_use_id": message.get("tool_call_id"),
                "content": _tool_result_content(message.get("content", "")),
            }
            if output and output[-1].get("role") == "user" and isinstance(
                output[-1].get("content"), list
            ) and all(
                isinstance(item, dict) and item.get("type") == "tool_result"
                for item in output[-1]["content"]
            ):
                output[-1]["content"].append(tool_result)
            else:
                output.append({"role": "user", "content": [tool_result]})
    flush_thinking()
    return tuple(output)


def _openai_messages(
    messages: tuple[dict[str, Any], ...], *, system_prompt: str | None = None
) -> tuple[dict[str, Any], ...]:
    output: list[dict[str, Any]] = []
    pending_reasoning: list[str] = []
    pending_text: list[str] = []

    def flush_pending_assistant() -> None:
        if not pending_reasoning and not pending_text:
            return
        message: dict[str, Any] = {
            "role": "assistant",
            "content": "\n".join(pending_text),
        }
        if pending_reasoning:
            message["reasoning_content"] = "".join(pending_reasoning)
        output.append(message)
        pending_reasoning.clear()
        pending_text.clear()

    def reasoning_item_text(item: Any) -> str | None:
        if not isinstance(item, dict) or item.get("kind") != "thinking":
            return None
        options = item.get("provider_options")
        if not isinstance(options, dict):
            return None
        openai = options.get("openai")
        if not isinstance(openai, dict) or openai.get("reasoning_field") != "reasoning_content":
            return None
        text = item.get("text")
        return text if isinstance(text, str) else None

    if system_prompt is not None:
        output.append({"role": "system", "content": system_prompt})
    for source in messages:
        message = _without_runtime_id(source)
        role = message.get("role")
        if role != "assistant":
            flush_pending_assistant()
            if role == "tool":
                output.append(
                    {
                        "role": "tool",
                        "tool_call_id": message.get("tool_call_id"),
                        "content": materialize_tool_result(
                            message.get("content", ""), provider="openai"
                        ),
                    }
                )
            elif role == "user":
                output.append(
                    {"role": "user", "content": _openai_user_content(message.get("content", ""))}
                )
            elif role == "system":
                output.append({"role": "system", "content": str(message.get("content", ""))})
            continue

        visible_text: list[str] = []
        reasoning_text: list[str] = []
        content = message.get("content")
        if isinstance(content, list):
            # Chat Completions has no neutral ``kind`` block format.  Only
            # provider-marked OpenAI reasoning and visible text are projected;
            # Anthropic signed/unsigned blocks are intentionally degraded.
            for item in content:
                if isinstance(item, dict) and item.get("kind") == "text":
                    text = item.get("text")
                    if isinstance(text, str):
                        visible_text.append(text)
                elif (text := reasoning_item_text(item)) is not None:
                    reasoning_text.append(text)
        elif isinstance(content, str):
            visible_text.append(content)

        has_reasoning = bool(reasoning_text)
        has_text = bool(visible_text)
        tool_calls = message.get("tool_calls")
        if tool_calls:
            combined_reasoning = [*pending_reasoning, *reasoning_text]
            combined_text = [*pending_text, *visible_text]
            projected: dict[str, Any] = {
                "role": "assistant",
                "content": "\n".join(combined_text) if combined_text else None,
                "tool_calls": [_openai_tool_call(call) for call in tool_calls],
            }
            if combined_reasoning:
                projected["reasoning_content"] = "".join(combined_reasoning)
            output.append(projected)
            pending_reasoning.clear()
            pending_text.clear()
        elif pending_reasoning:
            # A visible continuation following provider reasoning belongs to
            # the same assistant step and must be sent with that reasoning.
            pending_text.extend(visible_text)
        elif has_reasoning:
            pending_reasoning.extend(reasoning_text)
            pending_text.extend(visible_text)
        elif has_text:
            # Preserve the existing projection shape for ordinary OpenAI
            # text-only turns; only reasoning-bearing steps need deferred
            # assembly with a later tool-call message.
            output.append({**message, "content": "\n".join(visible_text)})
        else:
            flush_pending_assistant()
            # A tool-call-free assistant message with no supported content is
            # not a valid replay carrier.  Preserve ordinary empty messages
            # only when they have fields other than the neutral projection.
            if not isinstance(content, list):
                output.append(message)

    flush_pending_assistant()
    return tuple(output)


class CanonicalModelContextAdapter:
    """Build provider messages from one canonical projection boundary."""

    def __init__(self, projection: ModelReplayProjection | None = None) -> None:
        self.projection = projection or ModelReplayProjection()

    def build(
        self,
        source: Any,
        *,
        provider: str,
        high_water: int | None = None,
        system_prompt: str | None = None,
    ) -> ProviderContext:
        result: ModelReplayResult = self.projection.build(source, high_water=high_water)
        return self.build_result(result, provider=provider, system_prompt=system_prompt)

    def build_result(
        self,
        result: ModelReplayResult,
        *,
        provider: str,
        system_prompt: str | None = None,
    ) -> ProviderContext:
        """Adapt an already materialized neutral result without rereading it."""

        if provider == "anthropic":
            messages = _anthropic_messages(result.messages)
        elif provider == "openai":
            messages = _openai_messages(result.messages, system_prompt=system_prompt)
        else:
            raise ValueError(f"unsupported provider {provider!r}")
        return ProviderContext(
            provider=provider,
            high_water=result.high_water,
            source_digest=result.source_digest,
            projection_digest=result.digest,
            messages=messages,
            diagnostics=result.diagnostics,
            context_epoch=result.context_epoch,
        )


__all__ = ["CanonicalModelContextAdapter", "ProviderContext"]
