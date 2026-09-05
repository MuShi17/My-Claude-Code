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


def _anthropic_messages(messages: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    output: list[dict[str, Any]] = []
    for source in messages:
        message = _without_runtime_id(source)
        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            output.append(
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": call.get("id"),
                            "name": call.get("name"),
                            "input": call.get("arguments", {}),
                        }
                        for call in message["tool_calls"]
                    ],
                }
            )
        elif role in {"user", "assistant"}:
            output.append({"role": role, "content": message.get("content", "")})
        elif role == "tool":
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
    return tuple(output)


def _openai_messages(
    messages: tuple[dict[str, Any], ...], *, system_prompt: str | None = None
) -> tuple[dict[str, Any], ...]:
    output: list[dict[str, Any]] = []
    if system_prompt is not None:
        output.append({"role": "system", "content": system_prompt})
    output.extend(_without_runtime_id(message) for message in messages)
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
