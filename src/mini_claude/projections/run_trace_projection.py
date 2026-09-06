"""Diagnostic-only runtime trace projection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .base import (
    PROJECTION_VERSION,
    ProjectionDiagnostic,
    RuntimeEventReducer,
    iter_event_records,
    source_digest,
    stable_digest,
)


def _phase(event: Any) -> str:
    kind = event.kind
    if kind in {"invocation_opened", "text", "thinking", "context", "function_call", "model_final", "usage"}:
        return "model"
    if kind in {"context_transition"}:
        return "context"
    if kind in {"permission"}:
        return "permission"
    if kind in {"tool_dispatch"}:
        return "dispatch"
    if kind in {"tool_outcome", "function_response"}:
        return "tool"
    if kind in {"attempt_retry"}:
        return "retry"
    if kind in {"run_terminal"} or event.is_terminal:
        return "terminal"
    if kind in {"error"}:
        return "error"
    return "lifecycle"


@dataclass(frozen=True, slots=True)
class RunTraceResult:
    projection_version: str
    schema_version: int
    high_water: int
    source_digest: str
    digest: str
    entries: tuple[dict[str, Any], ...]
    diagnostics: tuple[ProjectionDiagnostic, ...]

    @property
    def events(self) -> tuple[dict[str, Any], ...]:
        return self.entries

    def to_dict(self) -> dict[str, Any]:
        return {
            "projection_version": self.projection_version,
            "schema_version": self.schema_version,
            "high_water": self.high_water,
            "source_digest": self.source_digest,
            "digest": self.digest,
            "entries": list(self.entries),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


class RunTraceProjection:
    projection_version = PROJECTION_VERSION

    def project(self, source: Any, *, high_water: int | None = None) -> RunTraceResult:
        records = iter_event_records(source, high_water=high_water)
        reducer = RuntimeEventReducer(records)
        entries: list[dict[str, Any]] = []
        for record in records:
            event = record.event
            content = event.content or {}
            actions = event.actions or {}
            error_type = content.get("code") if content.get("kind") == "error" else None
            entry: dict[str, Any] = {
                "ordinal": record.ordinal,
                "event_id": event.id,
                "phase": _phase(event),
                "kind": event.kind,
                "session_id": event.session_id,
                "turn_id": event.turn_id,
                "run_id": event.run_id,
                "parent_run_id": event.parent_run_id,
                "invocation_id": event.invocation_id,
                "ts": event.ts,
                "partial": event.partial,
                "status": event.status,
            }
            if event.refs and event.refs.get("tool_call_id"):
                entry["tool_call_id"] = event.refs["tool_call_id"]
            for action_name in ("model_finish", "tool_outcome", "attempt_retry", "run_terminal"):
                if action_name in actions:
                    entry[action_name] = dict(actions[action_name])
            if error_type:
                entry["error_type"] = error_type
            if content.get("kind") == "context":
                entry["context_type"] = content.get("context_type")
                entry["sources"] = list(content.get("sources", []))
                entry["content_digest"] = content.get("content_digest")
            if "context_transition" in actions:
                transition = actions["context_transition"]
                if isinstance(transition, Mapping):
                    entry["context_epoch"] = transition.get("context_epoch")
                    entry["transition_reason"] = transition.get("reason")
            entries.append(entry)
        output = {"entries": entries}
        return RunTraceResult(
            projection_version=self.projection_version,
            schema_version=1,
            high_water=high_water if high_water is not None else (records[-1].ordinal if records else 0),
            source_digest=source_digest(records),
            digest=stable_digest(output),
            entries=tuple(entries),
            diagnostics=tuple(reducer.diagnostics),
        )

    build = project


__all__ = ["RunTraceProjection", "RunTraceResult"]
