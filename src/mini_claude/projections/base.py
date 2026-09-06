"""Shared read-only reducer and projection metadata."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable

from ..runtime_event import RuntimeEvent

PROJECTION_VERSION = "projection-v1"


@dataclass(frozen=True, slots=True)
class EventRecord:
    ordinal: int
    event: RuntimeEvent


@dataclass(frozen=True, slots=True)
class ProjectionDiagnostic:
    code: str
    message: str
    severity: str = "warning"
    event_id: str | None = None
    run_id: str | None = None
    call_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
        }
        for key, value in (
            ("event_id", self.event_id),
            ("run_id", self.run_id),
            ("call_id", self.call_id),
        ):
            if value is not None:
                result[key] = value
        return result


def iter_event_records(source: Any, *, high_water: int | None = None) -> list[EventRecord]:
    """Normalize a store, prefix, or iterable into an ordinal-aware snapshot."""

    if hasattr(source, "read_event_records"):
        pairs = source.read_event_records(high_water=high_water)
        return [EventRecord(ordinal, event) for ordinal, event in pairs]
    if hasattr(source, "events") and not isinstance(source, (list, tuple)):
        inherited = getattr(source, "high_water", None)
        high_water = high_water if high_water is not None else inherited
        source = source.events
    records: list[EventRecord] = []
    for index, item in enumerate(source, start=1):
        if isinstance(item, EventRecord):
            record = item
        elif isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], int):
            record = EventRecord(item[0], item[1])
        else:
            record = EventRecord(index, item)
        if not isinstance(record.event, RuntimeEvent):
            record = EventRecord(record.ordinal, RuntimeEvent.from_dict(record.event))
        if high_water is None or record.ordinal <= high_water:
            records.append(record)
    return records


def source_digest(records: Iterable[EventRecord]) -> str:
    encoded = b"[" + b",".join(record.event.canonical_bytes() for record in records) + b"]"
    return hashlib.sha256(encoded).hexdigest()


def stable_digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def json_value(value: Any) -> Any:
    if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "items"):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, set)):
        return [json_value(item) for item in value]
    return str(value)


class RuntimeEventReducer:
    """Pair calls/results and collect diagnostics once for all projections."""

    def __init__(self, records: list[EventRecord]):
        self.records = records
        self.diagnostics: list[ProjectionDiagnostic] = []
        self.calls: dict[tuple[str, str], EventRecord] = {}
        self.responses: dict[tuple[str, str], list[EventRecord]] = {}
        self.partial: list[EventRecord] = []
        self.errors: list[EventRecord] = []
        self.terminals: list[EventRecord] = []
        self._reduce()

    def _reduce(self) -> None:
        for record in self.records:
            event = record.event
            if event.partial:
                self.partial.append(record)
                continue
            content = event.content or {}
            content_kind = content.get("kind")
            lifecycle = (event.metadata or {}).get("lifecycle")
            if content_kind == "function_call":
                call_id = str(content.get("id", ""))
                key = (event.run_id, call_id)
                if not call_id:
                    self.diagnostics.append(ProjectionDiagnostic("missing_call_id", "function call has no call identity", "error", event.id, event.run_id))
                elif key in self.calls:
                    self.diagnostics.append(ProjectionDiagnostic("duplicate_call", "duplicate function call identity", "error", event.id, event.run_id, call_id))
                else:
                    self.calls[key] = record
            if content_kind == "function_response" and event.kind != "tool_outcome" and lifecycle != "tool_outcome":
                call_id = str(content.get("id", ""))
                key = (event.run_id, call_id)
                self.responses.setdefault(key, []).append(record)
                if key not in self.calls:
                    self.diagnostics.append(ProjectionDiagnostic("unmatched_tool_result", "function response has no matching call", "warning", event.id, event.run_id, call_id))
            if content_kind == "error":
                self.errors.append(record)
            if event.is_terminal:
                self.terminals.append(record)
        for (run_id, call_id), record in self.calls.items():
            if (run_id, call_id) not in self.responses:
                self.diagnostics.append(ProjectionDiagnostic("unmatched_tool_call", "function call has no function response", "warning", record.event.id, run_id, call_id))
        for (run_id, call_id), responses in self.responses.items():
            call = self.calls.get((run_id, call_id))
            if call is None:
                continue
            for response in responses:
                if response.ordinal <= call.ordinal:
                    self.diagnostics.append(
                        ProjectionDiagnostic(
                            "invalid_tool_order",
                            "function response must follow its function call",
                            "error",
                            response.event.id,
                            run_id,
                            call_id,
                        )
                    )

    def response_for(self, run_id: str, call_id: str) -> EventRecord | None:
        values = self.responses.get((run_id, call_id), [])
        call = self.calls.get((run_id, call_id))
        if call is None:
            return None
        valid = [response for response in values if response.ordinal > call.ordinal]
        return valid[0] if valid else None
