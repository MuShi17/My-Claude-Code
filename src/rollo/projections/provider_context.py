"""Provider adapters for canonical model-replay messages."""

from __future__ import annotations

import json
import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..archive_capability import ToolResultArchiveCapability
from ..archive_projection import (
    ArchiveProjectionResult,
    project_archived_tool_results_outcome,
)
from .model_replay_projection import ModelReplayProjection, ModelReplayResult
from .replay_metadata import ReplayMessageMeta
from .base import ProjectionDiagnostic
from ..provider_content import materialize_tool_result
from ..provider_capacity import ProviderCapacityError
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
    request_size_bytes: int = 0
    request_budget_bytes: int | None = None
    request_fits: bool = True
    projection_phase: str = "ordinary"
    emergency_used: bool = False
    cycle_identity: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderRequestCycleIdentity:
    """Stable identity for one Agent-owned Provider request projection."""

    session_id: str
    provider_request_id: str
    source_digest: str
    source_high_water: int
    provider: str
    active_turn_id: str | None
    system_tools_digest: str
    request_budget_bytes: int | None

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            canonical_json_bytes(
                {
                    "session_id": self.session_id,
                    "provider_request_id": self.provider_request_id,
                    "source_digest": self.source_digest,
                    "source_high_water": self.source_high_water,
                    "provider": self.provider,
                    "active_turn_id": self.active_turn_id,
                    "system_tools_digest": self.system_tools_digest,
                    "request_budget_bytes": self.request_budget_bytes,
                }
            )
        ).hexdigest()


@dataclass(slots=True)
class ProviderRequestCycle:
    """Mutable state owned by Agent, with immutable identity and pass cache."""

    identity: ProviderRequestCycleIdentity
    emergency_used: bool = False
    cached_outcome: ArchiveProjectionResult | None = None
    cached_phase: str | None = None

    @property
    def identity_digest(self) -> str:
        return self.identity.digest


def _without_runtime_id(message: dict[str, Any]) -> dict[str, Any]:
    """Project the explicit neutral-to-Provider allowlist.

    Runtime ids, context metadata, replay sidecars, and future internal keys
    are intentionally not copied through this boundary.  Tool-call fields are
    rebuilt from the neutral contract instead of forwarding arbitrary nested
    dictionaries.
    """

    value: dict[str, Any] = {}
    role = message.get("role")
    if role in {"user", "assistant", "tool", "system"}:
        value["role"] = role
    if "content" in message:
        value["content"] = message.get("content")
    if role == "tool" and "tool_call_id" in message:
        value["tool_call_id"] = message.get("tool_call_id")
    if role == "assistant" and isinstance(message.get("tool_calls"), list):
        calls: list[dict[str, Any]] = []
        for call in message["tool_calls"]:
            if not isinstance(call, Mapping):
                continue
            calls.append(
                {
                    "id": call.get("id"),
                    "name": call.get("name"),
                    "arguments": call.get("arguments", {}),
                }
            )
        if calls:
            value["tool_calls"] = calls
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


def _provider_tool_definitions(
    provider: str,
    provider_tools: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Convert the raw tool definitions to the exact Provider wire shape."""

    if provider_tools is None:
        return None
    # Tool definitions may carry runtime-only flags such as ``deferred`` or
    # test metadata.  Rebuild the provider contract from its explicit fields
    # instead of forwarding arbitrary mappings across the wire boundary.
    normalized: list[dict[str, Any]] = []
    for tool in provider_tools:
        if not isinstance(tool, Mapping):
            continue
        name = tool.get("name")
        description = tool.get("description")
        input_schema = tool.get("input_schema")
        if not (
            isinstance(name, str)
            and name
            and isinstance(description, str)
            and isinstance(input_schema, Mapping)
        ):
            continue
        normalized.append(
            {
                "name": name,
                "description": description,
                "input_schema": dict(input_schema),
            }
        )
    if provider == "anthropic":
        return normalized
    if provider == "openai":
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["input_schema"],
                },
            }
            for tool in normalized
        ]
    raise ValueError(f"unsupported provider {provider!r}")


def _provider_request_payload(
    provider: str,
    messages: Sequence[Mapping[str, Any]],
    *,
    system_prompt: str | None = None,
    provider_tools: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the bounded context envelope used by projection fit checks.

    This deliberately measures only the stable context-bearing request fields.
    Model/stream/options fields are left to the existing 30% output and
    unmodeled-overhead reserve.
    """

    if provider not in {"anthropic", "openai"}:
        raise ValueError(f"unsupported provider {provider!r}")
    payload: dict[str, Any] = {
        "messages": [dict(message) for message in messages],
    }
    if provider == "anthropic" and system_prompt is not None:
        payload["system"] = system_prompt
    wire_tools = _provider_tool_definitions(provider, provider_tools)
    if wire_tools is not None:
        payload["tools"] = wire_tools
    return payload


def provider_request_size_bytes(
    provider: str,
    messages: Sequence[Mapping[str, Any]],
    *,
    system_prompt: str | None = None,
    provider_tools: Sequence[Mapping[str, Any]] | None = None,
) -> int:
    """Return canonical UTF-8 bytes for a final Provider context envelope."""

    return len(
        canonical_json_bytes(
            _provider_request_payload(
                provider,
                messages,
                system_prompt=system_prompt,
                provider_tools=provider_tools,
            )
        )
    )


def _bounded_diagnostic(item: Any) -> ProjectionDiagnostic:
    """Copy a diagnostic without raw content or exception details."""

    code = str(getattr(item, "code", "projection_diagnostic"))[:96]
    severity = str(getattr(item, "severity", "warning"))[:16]
    messages = {
        "provider_capacity_exhausted": "final Provider context exceeds the local request budget",
        "invalid_context_transition": "canonical context transition is invalid",
        "unmatched_tool_call": "a canonical tool call has no durable result",
        "unmatched_tool_result": "a canonical tool result has no matching call",
        "call_identity_conflict": "canonical tool-call identity is conflicting",
        "missing_call_id": "canonical tool call has no identity",
        "invalid_tool_order": "canonical tool result order is invalid",
        "non_final_function_call": "a non-final canonical function call was ignored",
    }
    message = messages.get(
        code,
        "projection diagnostic",
    )[:256]
    return ProjectionDiagnostic(
        code,
        message,
        severity,
        getattr(item, "event_id", None),
        getattr(item, "run_id", None),
        getattr(item, "call_id", None),
    )


def _merge_diagnostics(
    canonical: Sequence[Any],
    archive: Sequence[Any],
    *,
    metadata: Sequence[ReplayMessageMeta],
    capacity: Any | None = None,
) -> tuple[ProjectionDiagnostic, ...]:
    """Merge stable bounded diagnostics in canonical encounter order."""

    ordinal_by_event = {
        item.runtime_event_id: item.canonical_ordinal
        for item in metadata
        if item.runtime_event_id and item.canonical_ordinal is not None
    }
    values = [*canonical, *archive]
    if capacity is not None:
        values.append(capacity)
    indexed: list[tuple[tuple[int, int], ProjectionDiagnostic]] = []
    for encounter, item in enumerate(values):
        diagnostic = _bounded_diagnostic(item)
        ordinal = ordinal_by_event.get(diagnostic.event_id)
        indexed.append(((int(ordinal) if ordinal is not None else 2**63, encounter), diagnostic))
    result: list[ProjectionDiagnostic] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for _sort_key, diagnostic in sorted(indexed, key=lambda item: item[0]):
        key = (diagnostic.code, diagnostic.event_id, diagnostic.call_id)
        if key in seen:
            continue
        seen.add(key)
        result.append(diagnostic)
        if len(result) >= 32:
            break
    return tuple(result)


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
        provider_tools: Sequence[Mapping[str, Any]] | None = None,
        archive_capability: ToolResultArchiveCapability | None = None,
        budget_bytes: int | None = None,
        session_id: str | None = None,
        request_id: str | None = None,
        active_turn_id: str | None = None,
        request_cycle: ProviderRequestCycle | None = None,
    ) -> ProviderContext:
        result: ModelReplayResult = self.projection.build(source, high_water=high_water)
        return self.build_result(
            result,
            provider=provider,
            system_prompt=system_prompt,
            provider_tools=provider_tools,
            archive_capability=archive_capability,
            budget_bytes=budget_bytes,
            session_id=session_id,
            request_id=request_id,
            active_turn_id=active_turn_id,
            request_cycle=request_cycle,
        )

    def build_result(
        self,
        result: ModelReplayResult,
        *,
        provider: str,
        system_prompt: str | None = None,
        provider_tools: Sequence[Mapping[str, Any]] | None = None,
        archive_capability: ToolResultArchiveCapability | None = None,
        budget_bytes: int | None = None,
        session_id: str | None = None,
        request_id: str | None = None,
        active_turn_id: str | None = None,
        request_cycle: ProviderRequestCycle | None = None,
    ) -> ProviderContext:
        """Adapt an already materialized neutral result without rereading it."""

        frozen_tools = (
            tuple(dict(tool) for tool in provider_tools)
            if provider_tools is not None
            else None
        )

        def convert(
            neutral_messages: tuple[dict[str, Any], ...],
        ) -> tuple[dict[str, Any], ...]:
            if provider == "anthropic":
                return _anthropic_messages(neutral_messages)
            if provider == "openai":
                return _openai_messages(neutral_messages, system_prompt=system_prompt)
            raise ValueError(f"unsupported provider {provider!r}")

        def final_message_size(
            neutral_messages: Sequence[Mapping[str, Any]],
        ) -> int:
            converted = convert(tuple(dict(message) for message in neutral_messages))
            return provider_request_size_bytes(
                provider,
                converted,
                system_prompt=system_prompt,
                provider_tools=frozen_tools,
            )

        active_turn_id = active_turn_id or (
            request_cycle.identity.active_turn_id if request_cycle is not None else None
        )
        if request_cycle is None and request_id is not None and session_id is not None:
            system_tools_digest = hashlib.sha256(
                canonical_json_bytes(
                    {"system": system_prompt, "tools": frozen_tools}
                )
            ).hexdigest()
            request_cycle = ProviderRequestCycle(
                ProviderRequestCycleIdentity(
                    session_id=str(session_id),
                    provider_request_id=str(request_id),
                    source_digest=result.source_digest,
                    source_high_water=result.high_water,
                    provider=provider,
                    active_turn_id=active_turn_id,
                    system_tools_digest=system_tools_digest,
                    request_budget_bytes=budget_bytes,
                )
            )

        def cached_or_project(include_latest_active: bool) -> ArchiveProjectionResult:
            if request_cycle is not None and request_cycle.cached_outcome is not None:
                return request_cycle.cached_outcome
            outcome = project_archived_tool_results_outcome(
                result.messages,
                archive_capability,
                message_metadata=result.message_metadata,
                active_turn_id=active_turn_id,
                include_latest_active=include_latest_active,
                budget_bytes=budget_bytes,
                size_fn=final_message_size,
            )
            if request_cycle is not None:
                request_cycle.cached_outcome = outcome
                request_cycle.cached_phase = outcome.projection_phase
            return outcome

        outcome = cached_or_project(False)
        neutral_messages = outcome.messages
        messages = convert(neutral_messages)
        request_size_bytes = provider_request_size_bytes(
            provider,
            messages,
            system_prompt=system_prompt,
            provider_tools=frozen_tools,
        )
        request_fits = budget_bytes is None or request_size_bytes <= budget_bytes

        active_metadata_available = bool(
            active_turn_id
            and len(result.message_metadata) == len(result.messages)
            and any(
                meta.has_active_identity and meta.turn_id == active_turn_id
                for meta in result.message_metadata
            )
        )
        if (
            not request_fits
            and budget_bytes is not None
            and active_metadata_available
            and request_cycle is not None
            and not request_cycle.emergency_used
        ):
            # The Agent-owned cycle permits exactly one emergency pass.  Mark
            # the state before calling the projector so a retry cannot publish
            # a second archive set after an exception or a failed write.
            request_cycle.emergency_used = True
            outcome = project_archived_tool_results_outcome(
                result.messages,
                archive_capability,
                message_metadata=result.message_metadata,
                active_turn_id=active_turn_id,
                include_latest_active=True,
                budget_bytes=budget_bytes,
                size_fn=final_message_size,
            )
            request_cycle.cached_outcome = outcome
            request_cycle.cached_phase = outcome.projection_phase
            neutral_messages = outcome.messages
            messages = convert(neutral_messages)
            request_size_bytes = provider_request_size_bytes(
                provider,
                messages,
                system_prompt=system_prompt,
                provider_tools=frozen_tools,
            )
            request_fits = request_size_bytes <= budget_bytes

        capacity_diagnostic = None
        if not request_fits and budget_bytes is not None:
            capacity_diagnostic = ProjectionDiagnostic(
                "provider_capacity_exhausted",
                "final Provider context exceeds the local request budget",
                "error",
            )
        diagnostics = _merge_diagnostics(
            result.diagnostics,
            outcome.diagnostics,
            metadata=result.message_metadata,
            capacity=capacity_diagnostic,
        )
        return ProviderContext(
            provider=provider,
            high_water=result.high_water,
            source_digest=result.source_digest,
            projection_digest=result.digest,
            messages=messages,
            diagnostics=diagnostics,
            context_epoch=result.context_epoch,
            request_size_bytes=request_size_bytes,
            request_budget_bytes=budget_bytes,
            request_fits=request_fits,
            projection_phase=outcome.projection_phase,
            emergency_used=bool(
                outcome.emergency_used
                or (request_cycle is not None and request_cycle.emergency_used)
            ),
            cycle_identity=(
                request_cycle.identity_digest if request_cycle is not None else None
            ),
        )


__all__ = [
    "CanonicalModelContextAdapter",
    "ProviderRequestCycle",
    "ProviderRequestCycleIdentity",
    "ProviderCapacityError",
    "ProviderContext",
    "provider_request_size_bytes",
]
