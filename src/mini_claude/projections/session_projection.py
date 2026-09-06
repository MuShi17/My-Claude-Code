"""Deterministic session/transcript projection from canonical events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
class SessionProjectionResult:
    projection_version: str
    schema_version: int
    session_id: str | None
    high_water: int
    source_digest: str
    digest: str
    messages: tuple[dict[str, Any], ...]
    runs: tuple[dict[str, Any], ...]
    errors: tuple[dict[str, Any], ...]
    terminals: tuple[dict[str, Any], ...]
    partial_count: int
    diagnostics: tuple[ProjectionDiagnostic, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "projection_version": self.projection_version,
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "high_water": self.high_water,
            "source_digest": self.source_digest,
            "digest": self.digest,
            "messages": list(self.messages),
            "runs": list(self.runs),
            "errors": list(self.errors),
            "terminals": list(self.terminals),
            "partial_count": self.partial_count,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


class SessionProjection:
    """Rebuild conversation state from canonical events."""

    projection_version = PROJECTION_VERSION

    def project(
        self,
        source: Any,
        *,
        session_id: str | None = None,
        high_water: int | None = None,
    ) -> SessionProjectionResult:
        records = iter_event_records(source, high_water=high_water)
        if session_id is not None:
            records = [record for record in records if record.event.session_id == session_id]
        reducer = RuntimeEventReducer(records)
        messages: list[dict[str, Any]] = []
        runs: dict[str, dict[str, Any]] = {}
        errors: list[dict[str, Any]] = []
        terminals: list[dict[str, Any]] = []
        for record in records:
            event = record.event
            runs.setdefault(
                event.run_id,
                {
                    "run_id": event.run_id,
                    "session_id": event.session_id,
                    "invocation_id": event.invocation_id,
                    "parent_run_id": event.parent_run_id,
                    "first_ordinal": record.ordinal,
                    "last_ordinal": record.ordinal,
                    "status": "open",
                },
            )["last_ordinal"] = record.ordinal
            run = runs[event.run_id]
            if event.is_terminal:
                run["status"] = event.status
                terminal = {
                    "run_id": event.run_id,
                    "parent_run_id": event.parent_run_id,
                    "event_id": event.id,
                    "ordinal": record.ordinal,
                    "status": event.status,
                }
                terminals.append(terminal)
            content = event.content or {}
            kind = content.get("kind")
            if event.partial or event.model_visibility == "hidden":
                continue
            identity = {
                "event_id": event.id,
                "ordinal": record.ordinal,
                "run_id": event.run_id,
                "turn_id": event.turn_id,
            }
            if kind == "text" and content.get("text"):
                role = {"model": "assistant", "user": "user", "tool": "tool"}.get(event.role, event.role)
                messages.append({"role": role, "content": content["text"], "runtime": identity})
            elif kind == "context" and content.get("text"):
                messages.append({
                    "role": "context",
                    "content": content["text"],
                    "context_type": content.get("context_type"),
                    "sources": list(content.get("sources", [])),
                    "runtime": identity,
                })
            elif kind == "function_call":
                messages.append(
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": content.get("id"),
                                "name": content.get("name"),
                                "arguments": json_value(content.get("args")),
                            }
                        ],
                        "runtime": identity,
                    }
                )
            elif kind == "function_response" and event.kind != "tool_outcome" and (event.metadata or {}).get("lifecycle") != "tool_outcome":
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": content.get("id"),
                        "content": json_value(content.get("result")),
                        "runtime": identity,
                    }
                )
            elif kind == "error":
                error = {
                    "role": "system",
                    "content": content.get("message", "runtime error"),
                    "runtime": identity,
                    "error_type": content.get("code"),
                }
                messages.append(error)
                errors.append(error)
        session = session_id or (records[0].event.session_id if records else None)
        output = {
            "messages": messages,
            "runs": list(runs.values()),
            "errors": errors,
            "terminals": terminals,
            "partial_count": len(reducer.partial),
        }
        return SessionProjectionResult(
            projection_version=self.projection_version,
            schema_version=1,
            session_id=session,
            high_water=high_water if high_water is not None else (records[-1].ordinal if records else 0),
            source_digest=source_digest(records),
            digest=stable_digest(output),
            messages=tuple(messages),
            runs=tuple(runs.values()),
            errors=tuple(errors),
            terminals=tuple(terminals),
            partial_count=len(reducer.partial),
            diagnostics=tuple(reducer.diagnostics),
        )

    build = project


__all__ = ["SessionProjection", "SessionProjectionResult"]
