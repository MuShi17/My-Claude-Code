"""Rebuildable runtime metrics derived only from canonical facts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .base import ProjectionDiagnostic, iter_event_records, source_digest, stable_digest


PROJECTION_VERSION = "canonical-metrics-v1"
_MAX_LABEL_LENGTH = 64


def _label(value: Any, fallback: str = "unknown") -> str:
    text = str(value) if value is not None else fallback
    return text[:_MAX_LABEL_LENGTH]


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class MetricsProjectionResult:
    projection_version: str
    schema_version: int
    high_water: int
    source_digest: str
    digest: str
    sessions: tuple[dict[str, Any], ...]
    runs: tuple[dict[str, Any], ...]
    diagnostics: tuple[ProjectionDiagnostic, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "projection_version": self.projection_version,
            "schema_version": self.schema_version,
            "high_water": self.high_water,
            "source_digest": self.source_digest,
            "digest": self.digest,
            "sessions": list(self.sessions),
            "runs": list(self.runs),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


class CanonicalMetricsProjection:
    """Materialize bounded run/turn/session metrics from RuntimeEvents."""

    projection_version = PROJECTION_VERSION

    def project(self, source: Any, *, high_water: int | None = None) -> MetricsProjectionResult:
        records = iter_event_records(source, high_water=high_water)
        runs: dict[str, dict[str, Any]] = {}
        diagnostics: list[ProjectionDiagnostic] = []
        for record in records:
            event = record.event
            run = runs.setdefault(
                event.run_id,
                {
                    "run_id": event.run_id,
                    "session_id": event.session_id,
                    "turns": {},
                    "provider": "unknown",
                    "model": "unknown",
                    "started_at_ms": None,
                    "ended_at_ms": None,
                    "first_token_at_ms": None,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_create_tokens": 0,
                    "usage_available": False,
                    "finish_reason": None,
                    "retry_count": 0,
                    "permission": {"allow": 0, "deny": 0, "unknown": 0},
                    "tool_calls": {},
                    "terminal_status": None,
                },
            )
            turn = run["turns"].setdefault(
                event.turn_id,
                {
                    "turn_id": event.turn_id,
                    "first_ordinal": record.ordinal,
                    "last_ordinal": record.ordinal,
                },
            )
            turn["last_ordinal"] = record.ordinal
            lifecycle = (event.metadata or {}).get("lifecycle")
            content = event.content or {}
            actions = event.actions or {}

            if lifecycle == "invocation_opened" or content.get("kind") == "invocation_opened":
                run["started_at_ms"] = event.ts
                route = content.get("route") if isinstance(content, Mapping) else None
                if isinstance(route, Mapping):
                    run["provider"] = _label(route.get("provider"), run["provider"])
                    run["model"] = _label(route.get("model"), run["model"])
            if (event.partial or "first_token" in actions) and run["first_token_at_ms"] is None:
                run["first_token_at_ms"] = event.ts
            usage = actions.get("usage")
            if isinstance(usage, Mapping):
                for key in (
                    "input_tokens", "output_tokens", "cache_read_tokens", "cache_create_tokens"
                ):
                    value = _int(usage.get(key))
                    if value is not None:
                        run[key] += max(value, 0)
                        run["usage_available"] = True
            finish = actions.get("model_finish")
            if isinstance(finish, Mapping):
                run["finish_reason"] = _label(finish.get("finish_reason"))
            if "attempt_retry" in actions:
                run["retry_count"] += 1
            permission = actions.get("permission")
            if isinstance(permission, Mapping):
                decision = _label(permission.get("decision"), "unknown").lower()
                run["permission"][decision if decision in {"allow", "deny"} else "unknown"] += 1
            if content.get("kind") == "function_call":
                call_id = _label(content.get("id"), "unknown-call")
                run["tool_calls"].setdefault(
                    call_id,
                    {"tool_call_id": call_id, "tool_name": _label(content.get("name")),
                     "success": None, "executed": None, "duration_ms": None, "state": "dispatched"},
                )
            outcome = actions.get("tool_outcome")
            if isinstance(outcome, Mapping):
                call_id = _label(
                    outcome.get("provider_tool_call_id")
                    or (event.refs or {}).get("tool_call_id"),
                    "unknown-call",
                )
                tool = run["tool_calls"].setdefault(
                    call_id,
                    {"tool_call_id": call_id, "tool_name": _label(outcome.get("tool_name") or outcome.get("name")),
                     "success": None, "executed": None, "duration_ms": None, "state": "dispatched"},
                )
                tool["tool_name"] = _label(outcome.get("tool_name") or outcome.get("name"), tool["tool_name"])
                tool["success"] = outcome.get("success") if isinstance(outcome.get("success"), bool) else None
                tool["executed"] = outcome.get("executed") if isinstance(outcome.get("executed"), bool) else None
                duration = _int(outcome.get("duration_ms"))
                if duration is not None:
                    tool["duration_ms"] = max(duration, 0)
                tool["state"] = "completed" if tool["success"] else (
                    "failed" if tool["executed"] else "denied"
                )
            if event.is_terminal:
                run["ended_at_ms"] = event.ts
                run["terminal_status"] = event.status or "completed"

        operation_reader = getattr(source, "read_tool_operations", None)
        if callable(operation_reader):
            try:
                operations = operation_reader()
            except Exception as error:
                diagnostics.append(
                    ProjectionDiagnostic("operation_projection_failed", str(error), "error")
                )
            else:
                for operation in operations:
                    run = runs.get(operation.run_id)
                    if run is None:
                        continue
                    call_id = _label(operation.provider_tool_call_id, "unknown-call")
                    tool = run["tool_calls"].setdefault(
                        call_id,
                        {"tool_call_id": call_id, "tool_name": _label(operation.tool_name),
                         "success": operation.success, "executed": operation.executed,
                         "duration_ms": None, "state": operation.state},
                    )
                    tool["state"] = operation.state
                    if operation.success is not None:
                        tool["success"] = operation.success
                    if operation.executed is not None:
                        tool["executed"] = operation.executed

        run_values: list[dict[str, Any]] = []
        for run in runs.values():
            turns = list(run.pop("turns").values())
            tools = list(run.pop("tool_calls").values())
            started = run["started_at_ms"]
            ended = run["ended_at_ms"]
            run["first_token_ms"] = (
                run["first_token_at_ms"] - started
                if started is not None and run["first_token_at_ms"] is not None
                else None
            )
            run["duration_ms"] = ended - started if started is not None and ended is not None else None
            run["first_token_available"] = run["first_token_at_ms"] is not None
            run.pop("first_token_at_ms")
            run["turns"] = turns
            run["tool_calls"] = len(tools)
            run["tools_succeeded"] = sum(1 for tool in tools if tool.get("success") is True)
            run["tools_failed"] = sum(1 for tool in tools if tool.get("success") is False)
            run["tools_unknown"] = sum(1 for tool in tools if tool.get("state") == "outcome_unknown")
            run["tool_duration_ms"] = sum(
                tool["duration_ms"] for tool in tools if isinstance(tool.get("duration_ms"), int)
            )
            run_values.append(run)
        run_values.sort(key=lambda item: (item["session_id"], item["run_id"]))

        sessions_by_id: dict[str, list[dict[str, Any]]] = {}
        for run in run_values:
            sessions_by_id.setdefault(run["session_id"], []).append(run)
        sessions: list[dict[str, Any]] = []
        for session_id, session_runs in sorted(sessions_by_id.items()):
            sessions.append(
                {
                    "session_id": session_id,
                    "run_count": len(session_runs),
                    "turn_count": sum(len(run["turns"]) for run in session_runs),
                    "tool_calls": sum(run["tool_calls"] for run in session_runs),
                    "tools_succeeded": sum(run["tools_succeeded"] for run in session_runs),
                    "tools_failed": sum(run["tools_failed"] for run in session_runs),
                    "terminal_runs": sum(run["terminal_status"] is not None for run in session_runs),
                }
            )
        output = {"sessions": sessions, "runs": run_values}
        actual_high_water = high_water if high_water is not None else (records[-1].ordinal if records else 0)
        return MetricsProjectionResult(
            projection_version=self.projection_version,
            schema_version=1,
            high_water=actual_high_water,
            source_digest=source_digest(records),
            digest=stable_digest(output),
            sessions=tuple(sessions),
            runs=tuple(run_values),
            diagnostics=tuple(diagnostics),
        )

    build = project


__all__ = ["CanonicalMetricsProjection", "MetricsProjectionResult"]
