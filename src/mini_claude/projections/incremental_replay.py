"""Run-scoped, append-only model replay state.

The cold projection remains the reference implementation.  This cursor keeps
the already projected prefix in memory and consumes only newly appended
canonical records during a run.  It deliberately retains the reducer's
pending call state so a function call is not exposed to a provider until a
matching durable result is available.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from typing import Any

from ..context_transition import ContextTransition, ContextTransitionError
from .base import (
    PROJECTION_VERSION,
    EventRecord,
    ProjectionDiagnostic,
    effective_context_id,
    json_value,
    stable_digest,
)
from .model_replay_projection import ModelReplayResult


class IncrementalReplayError(RuntimeError):
    """The append-only source violated cursor ordering or identity rules."""


class IncrementalModelReplayCursor:
    """Maintain one provider-neutral replay while reading an event suffix."""

    projection_version = PROJECTION_VERSION

    def __init__(self, *, context_id: str | None = None) -> None:
        self.context_id = context_id
        self._records: list[EventRecord] = []
        self._record_ids: set[str] = set()
        self._digest = hashlib.sha256(b"[")
        self._messages: list[dict[str, Any]] = []
        self._calls: dict[tuple[str, str], EventRecord] = {}
        self._responses: dict[tuple[str, str], list[EventRecord]] = {}
        self._calls_by_group: dict[
            tuple[str, str], list[tuple[tuple[str, str], EventRecord]]
        ] = {}
        self._response_events: list[tuple[str, str]] = []
        self._response_event_ids: dict[str, str] = {}
        self._call_group_indexes: dict[tuple[str, str], int] = {}
        self._emitted_call_ids: set[str] = set()
        self._emitted_response_ids: set[str] = set()
        self._pending_call_keys: set[tuple[str, str]] = set()
        self._closed_call_groups: set[tuple[str, str]] = set()
        self._diagnostics: list[ProjectionDiagnostic] = []
        self._partial_count = 0
        self._context_epoch = "context:initial"
        self._last_ordinal = 0
        self.last_append_had_transition = False

    @property
    def high_water(self) -> int:
        return self._last_ordinal

    @property
    def source_digest(self) -> str:
        digest = self._digest.copy()
        digest.update(b"]")
        return digest.hexdigest()

    @property
    def context_epoch(self) -> str:
        return self._context_epoch

    @property
    def records_read(self) -> int:
        return len(self._records)

    def append(self, records: Iterable[EventRecord]) -> int:
        """Consume an ordinal-ordered suffix and return its record count."""

        suffix = list(records)
        self.last_append_had_transition = False
        if not suffix:
            return 0
        suffix.sort(key=lambda item: item.ordinal)
        for record in suffix:
            if record.ordinal <= self._last_ordinal:
                if record.event.id in self._record_ids:
                    continue
                raise IncrementalReplayError(
                    f"non-append event ordinal {record.ordinal} after {self._last_ordinal}"
                )

        suffix = [record for record in suffix if record.event.id not in self._record_ids]
        if not suffix:
            return 0
        for record in suffix:
            self._append_record(record)
        return len(suffix)

    def canonical_tool_response_events(self) -> tuple[tuple[str, str], ...]:
        """Return ordered ``(event_id, call_id)`` response identities.

        Event identity is the durable scope.  The call id is retained only as
        a provider-facing label and must never be used as a global lookup key.
        """

        return tuple(self._response_events)

    def canonical_tool_response_event_ids(self) -> dict[str, str]:
        """Return the legacy call-id map for callers that only need a label."""

        return dict(self._response_event_ids)

    def state_snapshot(self) -> dict[str, Any]:
        """Return bounded cursor metadata suitable for diagnostics/checkpoints."""

        pending = sorted(f"{run_id}:{call_id}" for run_id, call_id in self._pending_call_keys)
        return {
            "projection_version": self.projection_version,
            "context_id": self.context_id,
            "source_high_water": self.high_water,
            "source_digest": self.source_digest,
            "prior_prefix_digest": self.source_digest,
            "context_epoch": self.context_epoch,
            "records_read": self.records_read,
            "message_count": len(self._messages),
            "pending_call_ids": pending,
            "diagnostics": [
                item.to_dict() for item in self._diagnostics_with_unmatched()[-32:]
            ],
        }

    def result(self) -> ModelReplayResult:
        output = {"messages": self._messages, "partial_count": self._partial_count}
        return ModelReplayResult(
            projection_version=self.projection_version,
            schema_version=1,
            high_water=self.high_water,
            source_digest=self.source_digest,
            digest=stable_digest(output),
            messages=tuple(self._messages),
            partial_count=self._partial_count,
            diagnostics=tuple(self._diagnostics_with_unmatched()),
            context_epoch=self._context_epoch,
            context_id=self.context_id,
        )

    def _append_record(self, record: EventRecord) -> None:
        event = record.event
        if self.context_id is not None and effective_context_id(event) != self.context_id:
            raise IncrementalReplayError(
                f"event context {event.context_id!r} does not match cursor {self.context_id!r}"
            )
        source_digest_before = self.source_digest
        self._record_ids.add(event.id)
        if self._records:
            self._digest.update(b",")
        self._digest.update(event.canonical_bytes())
        self._records.append(record)
        self._last_ordinal = record.ordinal

        content = event.content or {}
        kind = content.get("kind")
        lifecycle = (event.metadata or {}).get("lifecycle")
        actions = event.actions or {}
        closes_group = isinstance(actions, Mapping) and "model_finish" in actions
        group_key = (event.run_id, event.invocation_id)
        if closes_group:
            self._closed_call_groups.add(group_key)
        if event.partial:
            self._partial_count += 1
            return
        if kind == "function_call":
            call_id = str(content.get("id", ""))
            key = (event.run_id, call_id)
            if not call_id:
                self._diagnostics.append(
                    ProjectionDiagnostic(
                        "missing_call_id",
                        "function call has no call identity",
                        "error",
                        event.id,
                        event.run_id,
                    )
                )
            elif key in self._calls:
                self._diagnostics.append(
                    ProjectionDiagnostic(
                        "duplicate_call",
                        "duplicate function call identity",
                        "error",
                        event.id,
                        event.run_id,
                        call_id,
                    )
                )
            else:
                self._calls[key] = record
                self._calls_by_group.setdefault(group_key, []).append((key, record))
                self._pending_call_keys.add(key)
                for response in self._responses.get(key, []):
                    if response.ordinal > record.ordinal:
                        self._response_events.append((response.event.id, call_id))
                        self._response_event_ids[call_id] = response.event.id
                        self._pending_call_keys.discard(key)
                    else:
                        self._diagnostics.append(
                            ProjectionDiagnostic(
                                "invalid_tool_order",
                                "function response must follow its function call",
                                "error",
                                response.event.id,
                                event.run_id,
                                call_id,
                            )
                        )
        if (
            kind == "function_response"
            and event.kind != "tool_outcome"
            and lifecycle != "tool_outcome"
        ):
            call_id = str(content.get("id", ""))
            key = (event.run_id, call_id)
            self._responses.setdefault(key, []).append(record)
            if call_id:
                self._response_event_ids[call_id] = event.id
            call = self._calls.get(key)
            if call is None:
                self._diagnostics.append(
                    ProjectionDiagnostic(
                        "unmatched_tool_result",
                        "function response has no matching call",
                        "warning",
                        event.id,
                        event.run_id,
                        call_id,
                    )
                )
            elif record.ordinal <= call.ordinal:
                self._diagnostics.append(
                    ProjectionDiagnostic(
                        "invalid_tool_order",
                        "function response must follow its function call",
                        "error",
                        event.id,
                        event.run_id,
                        call_id,
                    )
                )
            else:
                self._response_events.append((event.id, call_id))
                self._pending_call_keys.discard(key)

        if closes_group and not event.partial:
            self._render_tool_group(group_key)
        self._project_record(record, source_digest_before=source_digest_before)

    def _project_record(self, record: EventRecord, *, source_digest_before: str) -> None:
        event = record.event
        if event.model_visibility == "hidden":
            return
        actions = event.actions or {}
        transition_value = actions.get("context_transition")
        if isinstance(transition_value, Mapping):
            self.last_append_had_transition = True
            transition_valid = self._apply_transition(
                record, transition_value, source_digest_before=source_digest_before
            )
            if not transition_valid:
                return

        compaction = actions.get("compaction")
        if isinstance(compaction, Mapping) and compaction.get("reset_model_context"):
            self._messages.clear()
            self._calls.clear()
            self._responses.clear()
            self._calls_by_group.clear()
            self._response_events.clear()
            self._response_event_ids.clear()
            self._pending_call_keys.clear()
            self._closed_call_groups.clear()
            self._call_group_indexes.clear()
            self._emitted_call_ids.clear()
            self._emitted_response_ids.clear()
            for message in compaction.get("context_messages", []):
                if isinstance(message, Mapping) and message.get("role") in {
                    "user",
                    "assistant",
                    "tool",
                }:
                    item = dict(message)
                    item.setdefault("runtime_event_id", event.id)
                    self._messages.append(item)
            return

        content = event.content or {}
        kind = content.get("kind")
        if kind == "text" and event.role in {"user", "model"} and content.get("text"):
            self._messages.append(
                {
                    "role": "user" if event.role == "user" else "assistant",
                    "content": content["text"],
                    "runtime_event_id": event.id,
                }
            )
        elif kind == "context" and event.role in {"user", "system"} and content.get("text"):
            self._messages.append(
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
            if event.provider == "openai":
                item["provider_options"] = {
                    "openai": {"reasoning_field": "reasoning_content"}
                }
            self._messages.append({"role": "assistant", "content": [item]})
        elif kind == "function_response":
            self._render_tool_group_for_response(record)
        elif kind == "function_call":
            # Calls are rendered at the response boundary so a pending call
            # cannot leak into an executable provider request.
            key = (event.run_id, str(content.get("id", "")))
            if self._calls.get(key) is record and self._response_for(key) is not None:
                self._render_tool_group(key)

    def _apply_transition(
        self,
        record: EventRecord,
        value: Mapping[str, Any],
        *,
        source_digest_before: str,
    ) -> bool:
        try:
            transition = ContextTransition.from_value(value)
            if self.context_id is not None and transition.context_id not in {
                None, self.context_id
            }:
                raise ContextTransitionError(
                    "transition context identity does not match cursor"
                )
            compaction = (record.event.actions or {}).get("compaction")
            is_reset = bool(
                isinstance(compaction, Mapping)
                and compaction.get("reset_model_context")
            )
            if not is_reset and transition.context_epoch != self._context_epoch:
                raise ContextTransitionError(
                    "non-reset transition cannot change context epoch"
                )
            if transition.projection_version != self.projection_version:
                raise ContextTransitionError(
                    "transition projection version is unsupported"
                )
            if transition.source_high_water != record.ordinal - 1:
                raise ContextTransitionError(
                    "transition source high-water is not the prior event"
                )
            if transition.source_digest != source_digest_before:
                raise ContextTransitionError("transition source digest mismatch")
            applied = 0
            for replacement in transition.replacements:
                targets = [
                    message
                    for message in self._messages
                    if message.get("runtime_event_id") == replacement.target_event_id
                ]
                if not targets:
                    raise ContextTransitionError(
                        f"transition target not found: {replacement.target_event_id}"
                    )
                if len(targets) != 1:
                    raise ContextTransitionError(
                        f"transition target is ambiguous: {replacement.target_event_id}"
                    )
                target = targets[0]
                if (
                    replacement.target_call_id is not None
                    and target.get("tool_call_id") != replacement.target_call_id
                ):
                    raise ContextTransitionError(
                        f"transition target identity mismatch: {replacement.target_event_id}"
                    )
                target["content"] = json_value(replacement.replacement)
                applied += 1
            if transition.replacements and not applied:
                raise ContextTransitionError("transition did not apply any replacement")
            expected = stable_digest(
                {"replacements": [item.to_dict() for item in transition.replacements]}
            )
            if transition.replacements and transition.result_digest != expected:
                raise ContextTransitionError("transition result digest mismatch")
            if (
                not transition.replacements
                and isinstance(compaction, Mapping)
                and compaction.get("reset_model_context")
                and transition.result_digest
                != stable_digest(compaction.get("context_messages", []))
            ):
                raise ContextTransitionError("compaction result digest mismatch")
            self._context_epoch = transition.context_epoch
            return True
        except ContextTransitionError as error:
            self._diagnostics.append(
                ProjectionDiagnostic(
                    "invalid_context_transition",
                    str(error),
                    "error",
                    record.event.id,
                    record.event.run_id,
                )
            )
            return False

    def _response_for(self, key: tuple[str, str]) -> EventRecord | None:
        call = self._calls.get(key)
        if call is None:
            return None
        return next(
            (item for item in self._responses.get(key, []) if item.ordinal > call.ordinal),
            None,
        )

    def _render_tool_group_for_response(self, response: EventRecord) -> None:
        event = response.event
        key = (event.run_id, str((event.content or {}).get("id", "")))
        call = self._calls.get(key)
        if call is not None:
            self._render_tool_group((call.event.run_id, call.event.invocation_id))

    def _render_tool_group(self, group_key: tuple[str, str]) -> None:
        group_calls = self._calls_by_group.get(group_key, [])
        if not group_calls:
            return
        # Parallel results can arrive in completion order rather than model
        # order. Keep the group pending until every known call has a valid
        # result, then append calls by model ordinal in one operation.
        if any(self._response_for(key) is None for key, _ in group_calls):
            return
        candidates = [
            (key, record)
            for key, record in group_calls
            if record.event.id not in self._emitted_call_ids
        ]
        if not candidates:
            return
        candidates.sort(key=lambda item: item[1].ordinal)
        group_index = self._call_group_indexes.get(group_key)
        if group_index is None:
            self._messages.append(
                {
                    "role": "assistant",
                    "tool_calls": [],
                    "runtime_event_id": candidates[0][1].event.id,
                }
            )
            group_index = len(self._messages) - 1
            self._call_group_indexes[group_key] = group_index
        call_message = self._messages[group_index]
        for key, record in candidates:
            content = record.event.content or {}
            call_message.setdefault("tool_calls", []).append(
                {
                    "id": content.get("id"),
                    "name": content.get("name"),
                    "arguments": json_value(content.get("args")),
                }
            )
            self._emitted_call_ids.add(record.event.id)
        response_items = [
            (str((record.event.content or {}).get("id", "")), self._response_for(key))
            for key, record in group_calls
            if self._response_for(key) is not None
            and self._response_for(key).event.id not in self._emitted_response_ids
        ]
        for call_id, response in sorted(
            response_items, key=lambda item: item[1].ordinal  # type: ignore[union-attr]
        ):
            assert response is not None
            self._messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json_value((response.event.content or {}).get("result")),
                    "runtime_event_id": response.event.id,
                }
            )
            self._emitted_response_ids.add(response.event.id)

    def _diagnostics_with_unmatched(self) -> list[ProjectionDiagnostic]:
        result = list(self._diagnostics)
        for run_id, call_id in sorted(self._pending_call_keys):
            record = self._calls[(run_id, call_id)]
            result.append(
                ProjectionDiagnostic(
                    "unmatched_tool_call",
                    "function call has no function response",
                    "warning",
                    record.event.id,
                    run_id,
                    call_id,
                )
            )
        return result


__all__ = ["IncrementalModelReplayCursor", "IncrementalReplayError"]
