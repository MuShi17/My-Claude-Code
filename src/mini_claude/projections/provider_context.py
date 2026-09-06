"""Provider adapters for canonical model-replay messages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .model_replay_projection import ModelReplayProjection, ModelReplayResult


@dataclass(frozen=True, slots=True)
class ProviderContext:
    provider: str
    high_water: int
    source_digest: str
    projection_digest: str
    messages: tuple[dict[str, Any], ...]
    diagnostics: tuple[Any, ...]


def _without_runtime_id(message: dict[str, Any]) -> dict[str, Any]:
    value = dict(message)
    value.pop("runtime_event_id", None)
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
            output.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.get("tool_call_id"),
                            "content": message.get("content", ""),
                        }
                    ],
                }
            )
    flush_thinking()
    return tuple(output)


def _openai_messages(
    messages: tuple[dict[str, Any], ...], *, system_prompt: str | None = None
) -> tuple[dict[str, Any], ...]:
    output: list[dict[str, Any]] = []
    if system_prompt is not None:
        output.append({"role": "system", "content": system_prompt})
    for source in messages:
        message = _without_runtime_id(source)
        if message.get("role") == "assistant" and isinstance(message.get("content"), list):
            # Chat Completions has no portable signed-thinking block.  Keep
            # tool calls and visible text, but drop provider-internal thinking
            # instead of emitting the projection's neutral ``kind`` shape.
            visible_text = [
                item.get("text", "")
                for item in message["content"]
                if isinstance(item, dict) and item.get("kind") == "text"
            ]
            if message.get("tool_calls") or visible_text:
                message = {
                    **message,
                    "content": "\n".join(text for text in visible_text if text),
                }
            else:
                continue
        output.append(message)
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
        )


__all__ = ["CanonicalModelContextAdapter", "ProviderContext"]
