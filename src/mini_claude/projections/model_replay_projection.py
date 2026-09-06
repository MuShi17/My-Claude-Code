"""Provider-neutral model-history projection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..context_transition import ContextTransition, ContextTransitionError
from .base import (
    PROJECTION_VERSION,
    ProjectionDiagnostic,
    RuntimeEventReducer,
    iter_event_records,
    json_value,
    source_digest,
    stable_digest,
)


@dataclass(frozen=True, slots=True)
class ModelReplayResult:
    projection_version: str
    schema_version: int
    high_water: int
    source_digest: str
    digest: str
    messages: tuple[dict[str, Any], ...]
    partial_count: int
    diagnostics: tuple[ProjectionDiagnostic, ...]
    context_epoch: str = "context:initial"

    def to_dict(self) -> dict[str, Any]:
        return {
            "projection_version": self.projection_version,
            "schema_version": self.schema_version,
            "high_water": self.high_water,
            "source_digest": self.source_digest,
            "digest": self.digest,
            "messages": list(self.messages),
            "partial_count": self.partial_count,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "context_epoch": self.context_epoch,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


class ModelReplayProjection:
    projection_version = PROJECTION_VERSION

    def project(self, source: Any, *, high_water: int | None = None) -> ModelReplayResult:
        records = iter_event_records(source, high_water=high_water)
        reducer = RuntimeEventReducer(records)
        messages: list[dict[str, Any]] = []
        call_records = sorted(reducer.calls.items(), key=lambda item: item[1].ordinal)
        calls_by_event_id = {record.event.id: (key, record) for key, record in call_records}
        calls_by_group: dict[tuple[str, str], list[tuple[tuple[str, str], Any]]] = {}
        for key, record in call_records:
            calls_by_group.setdefault(
                (record.event.run_id, record.event.invocation_id), []
            ).append((key, record))
        tool_call_group_indices: dict[tuple[str, str], int] = {}
        emitted_call_ids: set[str] = set()
        emitted_response_ids: set[str] = set()
        context_epoch = "context:initial"
        response_event_ids = {
            response.event.id
            for values in reducer.responses.values()
            for response in values
        }
        for record in records:
            event = record.event
            if event.partial or event.model_visibility == "hidden":
                continue
            transition_value = (event.actions or {}).get("context_transition")
            if isinstance(transition_value, Mapping):
                try:
                    transition = ContextTransition.from_value(transition_value)
                    if transition.projection_version != self.projection_version:
                        raise ContextTransitionError(
                            "transition projection version is unsupported"
                        )
                    if transition.source_high_water != record.ordinal - 1:
                        raise ContextTransitionError("transition source high-water is not the prior event")
                    source_records = [item for item in records if item.ordinal <= transition.source_high_water]
                    if source_digest(source_records) != transition.source_digest:
                        raise ContextTransitionError("transition source digest mismatch")
                    applied = 0
                    for replacement in transition.replacements:
                        target = next(
                            (
                                message
                                for message in messages
                                if message.get("runtime_event_id") == replacement.target_event_id
                            ),
                            None,
                        )
                        if target is None:
                            raise ContextTransitionError(
                                f"transition target not found: {replacement.target_event_id}"
                            )
                        if (
                            replacement.target_call_id is not None
                            and target.get("tool_call_id") != replacement.target_call_id
                        ):
                            raise ContextTransitionError(
                                f"transition target identity mismatch: {replacement.target_event_id}"
                            )
                        target["content"] = json_value(replacement.replacement)
                        applied += 1
                    if transition.result_digest != stable_digest(
                        {"replacements": [item.to_dict() for item in transition.replacements]}
                    ) and transition.replacements:
                        raise ContextTransitionError("transition result digest mismatch")
                    if not transition.replacements and isinstance(compaction := (event.actions or {}).get("compaction"), Mapping) and compaction.get("reset_model_context"):
                        if transition.result_digest != stable_digest(compaction.get("context_messages", [])):
                            raise ContextTransitionError("compaction result digest mismatch")
                    if transition.replacements and not applied:
                        raise ContextTransitionError("transition did not apply any replacement")
                    context_epoch = transition.context_epoch
                except ContextTransitionError as error:
                    reducer.diagnostics.append(
                        ProjectionDiagnostic(
                            "invalid_context_transition",
                            str(error),
                            "error",
                            event.id,
                            event.run_id,
                        )
                    )
            compaction = (event.actions or {}).get("compaction")
            if isinstance(compaction, Mapping) and compaction.get("reset_model_context"):
                messages.clear()
                tool_call_group_indices.clear()
                emitted_call_ids.clear()
                emitted_response_ids.clear()
                for message in compaction.get("context_messages", []):
                    if isinstance(message, Mapping) and message.get("role") in {
                        "user", "assistant", "tool"
                    }:
                        item = dict(message)
                        item["runtime_event_id"] = event.id
                        messages.append(item)
                continue
            content = event.content or {}
            kind = content.get("kind")
            if kind == "text" and event.role in {"user", "model"} and content.get("text"):
                messages.append(
                    {
                        "role": "user" if event.role == "user" else "assistant",
                        "content": content["text"],
                        "runtime_event_id": event.id,
                    }
                )
            elif kind == "context" and event.role in {"user", "system"} and content.get("text"):
                messages.append(
                    {
                        "role": "user",
                        "content": content["text"],
                        "context_type": content.get("context_type"),
                        "runtime_event_id": event.id,
                    }
                )
            elif kind == "thinking" and event.role == "model":
                item: dict[str, Any] = {
                    "kind": "thinking",
                    "text": content.get("text", ""),
                    "runtime_event_id": event.id,
                }
                if content.get("signature"):
                    item["signature"] = content["signature"]
                # OpenAI-compatible reasoning is unsigned provider state.  A
                # small provider option lets the OpenAI adapter retain it on
                # the same assistant step without making Anthropic replay
                # emit an invalid unsigned thinking block.
                if event.provider == "openai":
                    item["provider_options"] = {
                        "openai": {"reasoning_field": "reasoning_content"}
                    }
                messages.append({"role": "assistant", "content": [item]})
            elif kind == "function_call":
                key_record = calls_by_event_id.get(event.id)
                if key_record is None:
                    continue
                key, _ = key_record
                response = reducer.response_for(*key)
                if response is None:
                    # The call remains a diagnostic/unsettled fact and must
                    # not become an executable provider message.
                    continue
                group_key = (event.run_id, event.invocation_id)
                group_calls = calls_by_group.get(group_key, [])
                if any(reducer.response_for(*group_key_item) is None for group_key_item, _ in group_calls):
                    continue
                group_index = tool_call_group_indices.get(group_key)
                if group_index is None:
                    call_message: dict[str, Any] = {
                        "role": "assistant",
                        "tool_calls": [],
                        "runtime_event_id": event.id,
                    }
                    messages.append(call_message)
                    tool_call_group_indices[group_key] = len(messages) - 1
                    group_index = len(messages) - 1
                call_message = messages[group_index]
                for group_key_item, call_record in group_calls:
                    if call_record.event.id in emitted_call_ids:
                        continue
                    call_content = call_record.event.content or {}
                    call_message["tool_calls"].append(
                        {
                            "id": call_content.get("id"),
                            "name": call_content.get("name"),
                            "arguments": json_value(call_content.get("args")),
                        }
                    )
                    emitted_call_ids.add(call_record.event.id)
            elif (
                kind == "function_response"
                and event.id in response_event_ids
                and (event.run_id, str(content.get("id", ""))) in reducer.calls
                and record.ordinal
                > reducer.calls[(event.run_id, str(content.get("id", "")))].ordinal
                and event.kind != "tool_outcome"
                and (event.metadata or {}).get("lifecycle") != "tool_outcome"
            ):
                key = (event.run_id, str(content.get("id", "")))
                group_key = (event.run_id, reducer.calls[key].event.invocation_id)
                group_calls = calls_by_group.get(group_key, [])
                if any(reducer.response_for(*group_key_item) is None for group_key_item, _ in group_calls):
                    continue
                if group_key not in tool_call_group_indices:
                    # A malformed or unusual ledger may place the response
                    # before the visible call record.  Do not fabricate a
                    # partial group; the call branch will render it later.
                    continue
                response_items = [
                    (call_record, reducer.response_for(*group_key_item))
                    for group_key_item, call_record in group_calls
                ]
                for call_record, response in sorted(
                    (item for item in response_items if item[1] is not None),
                    key=lambda item: item[1].ordinal,  # type: ignore[union-attr]
                ):
                    assert response is not None
                    if response.event.id in emitted_response_ids:
                        continue
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": (call_record.event.content or {}).get("id"),
                            "content": json_value((response.event.content or {}).get("result")),
                            "runtime_event_id": response.event.id,
                        }
                    )
                    emitted_response_ids.add(response.event.id)
        output = {"messages": messages, "partial_count": len(reducer.partial)}
        return ModelReplayResult(
            projection_version=self.projection_version,
            schema_version=1,
            high_water=high_water if high_water is not None else (records[-1].ordinal if records else 0),
            source_digest=source_digest(records),
            digest=stable_digest(output),
            messages=tuple(messages),
            partial_count=len(reducer.partial),
            diagnostics=tuple(reducer.diagnostics),
            context_epoch=context_epoch,
        )

    build = project


__all__ = ["ModelReplayProjection", "ModelReplayResult"]
