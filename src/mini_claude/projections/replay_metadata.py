"""Internal metadata carried alongside neutral replay messages.

The canonical event remains the only durable source of truth.  This sidecar is
an in-memory projection aid: it gives archive pruning the event/step identity it
needs without putting implementation metadata into neutral messages, digests,
or Provider requests.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..runtime_event import canonical_json_bytes
from ..tool_result import (
    ToolResultSerializationError,
    estimated_tool_result_tokens,
    canonical_tool_result_bytes,
)
from ..tool_call_identity import decode_tool_arguments


@dataclass(frozen=True, slots=True)
class ReplayMessageMeta:
    """The minimum identity needed by stale/active archive policies."""

    identity_state: str = "synthetic"
    runtime_event_id: str | None = None
    canonical_ordinal: int | None = None
    turn_id: str | None = None
    run_id: str | None = None
    invocation_id: str | None = None
    step_key: tuple[str, str] | None = None
    step_ordinal: int | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    arguments_digest: str | None = None
    semantic_input_complete: bool = False
    range_identity: tuple[Any, ...] | None = None
    snapshot_identity: tuple[Any, ...] | None = None
    body_sha256: str | None = None
    estimated_tokens: int | None = None
    completed: bool = False
    partial: bool = False
    terminal_status: str | None = None
    success: bool | None = None

    @property
    def has_archive_identity(self) -> bool:
        return self.identity_state == "complete" and bool(
            self.runtime_event_id
            and self.tool_call_id
            and self.tool_name
            and self.body_sha256
        )

    @property
    def has_active_identity(self) -> bool:
        return bool(
            self.identity_state == "complete"
            and self.turn_id
            and self.run_id
            and self.invocation_id
            and self.step_key
            and self.completed
            and not self.partial
        )

    @property
    def has_supersession_identity(self) -> bool:
        return bool(
            self.identity_state == "complete"
            and self.tool_name
            and self.arguments_digest
            and self.body_sha256
            and self.turn_id
            and self.run_id
            and self.invocation_id
            and self.completed
            and not self.partial
        )

    def to_dict(self) -> dict[str, Any]:
        """Return bounded diagnostic/test data, never a Provider payload."""

        result: dict[str, Any] = {
            "identity_state": self.identity_state,
            "runtime_event_id": self.runtime_event_id,
            "canonical_ordinal": self.canonical_ordinal,
            "turn_id": self.turn_id,
            "run_id": self.run_id,
            "invocation_id": self.invocation_id,
            "step_key": list(self.step_key) if self.step_key else None,
            "step_ordinal": self.step_ordinal,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "arguments_digest": self.arguments_digest,
            "semantic_input_complete": self.semantic_input_complete,
            "range_identity": list(self.range_identity) if self.range_identity else None,
            "snapshot_identity": list(self.snapshot_identity)
            if self.snapshot_identity
            else None,
            "body_sha256": self.body_sha256,
            "estimated_tokens": self.estimated_tokens,
            "completed": self.completed,
            "partial": self.partial,
            "terminal_status": self.terminal_status,
            "success": self.success,
        }
        return result


def _as_event(record: Any) -> Any:
    return getattr(record, "event", record)


def _as_ordinal(record: Any) -> int | None:
    value = getattr(record, "ordinal", None)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _event_id(message: Mapping[str, Any]) -> str | None:
    value = message.get("runtime_event_id")
    return value if isinstance(value, str) and value else None


def _call_id(message: Mapping[str, Any]) -> str | None:
    value = message.get("tool_call_id")
    return value if isinstance(value, str) and value else None


def _tool_name(message: Mapping[str, Any], event: Any, call_event: Any) -> str | None:
    for source in (message, getattr(event, "content", None), getattr(call_event, "content", None)):
        if isinstance(source, Mapping):
            value = source.get("name")
            if isinstance(value, str) and value:
                return value
    return None


def _call_arguments(message: Mapping[str, Any], call_event: Any) -> Any:
    content = getattr(call_event, "content", None)
    if isinstance(content, Mapping) and "args" in content:
        raw = content.get("args")
    else:
        raw = message.get("arguments")
    if raw is None:
        return None
    decoded, error = decode_tool_arguments(raw)
    # Keep invalid JSON/non-object values available for deterministic
    # argument digests, while _descriptor() conservatively rejects them.
    return decoded if error is None else raw


def _digest(value: Any) -> str | None:
    try:
        return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None


def _strip_leading_dot_slash(value: str) -> str:
    # Deliberately do not normalize separators, UNC prefixes, repeated slashes,
    # case, or ``..`` segments.  The descriptor contract only removes a
    # leading relative ``./`` marker.
    while value.startswith("./"):
        value = value[2:]
    return value


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


_SHELL_META = re.compile(r"[;&|$`()<>\n\r`]")
_GIT_SNAPSHOT = re.compile(
    r"^git\s+(?:--\S+\s+)*(status|diff|log|show|branch|rev-parse)(?:\s|$)",
    re.IGNORECASE,
)


def _descriptor(
    tool_name: str | None,
    arguments: Any,
) -> tuple[tuple[Any, ...] | None, tuple[Any, ...] | None, bool]:
    """Return (range, snapshot, complete-input) for the Maka mapping."""

    if not isinstance(arguments, Mapping) or not isinstance(tool_name, str):
        return None, None, False
    if tool_name == "read_file":
        path = arguments.get("file_path", arguments.get("path"))
        if not isinstance(path, str) or not path:
            return None, None, False
        offset = _non_negative_int(arguments.get("offset", 0))
        if offset is None:
            return None, None, False
        raw_limit = arguments.get("limit")
        if raw_limit is None:
            limit: int | None = None
        else:
            limit = _positive_int(raw_limit)
            if limit is None:
                return None, None, False
        normalized_path = _strip_leading_dot_slash(path)
        end = None if limit is None else offset + limit
        return (normalized_path, offset, end), None, True
    if tool_name == "list_files":
        pattern = arguments.get("pattern")
        path = arguments.get("path", ".")
        if not isinstance(pattern, str) or not isinstance(path, str):
            return None, None, False
        return None, (
            "Glob",
            pattern,
            _strip_leading_dot_slash(path),
        ), True
    if tool_name == "grep_search":
        pattern = arguments.get("pattern")
        path = arguments.get("path", ".")
        include = arguments.get("include")
        if not isinstance(pattern, str) or not isinstance(path, str):
            return None, None, False
        if include is not None and not isinstance(include, str):
            return None, None, False
        return None, (
            "Grep",
            pattern,
            _strip_leading_dot_slash(path),
            include,
        ), True
    if tool_name == "run_shell":
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            return None, None, False
        stripped = command.strip()
        if _SHELL_META.search(stripped) or "$(`" in stripped:
            # A valid but non-snapshot shell command can still participate in
            # exact duplicate detection.  It must never be treated as a
            # read-only Bash snapshot.
            return None, None, True
        if _GIT_SNAPSHOT.match(stripped):
            return None, ("Bash", stripped), True
        # Arbitrary shell commands retain only the exact-duplicate path.
        return None, None, True
    return None, None, False


def _body_identity(value: Any, tool_name: str | None) -> tuple[str | None, int | None]:
    if not isinstance(tool_name, str) or not tool_name:
        tool_name = "tool-result"
    try:
        serialized = canonical_tool_result_bytes(value, tool_name=tool_name)
        return "sha256:" + hashlib.sha256(serialized).hexdigest(), estimated_tool_result_tokens(
            value, tool_name=tool_name
        )
    except ToolResultSerializationError:
        return None, None
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None, None


def _source_for_message(
    message: Mapping[str, Any],
    *,
    records_by_event_id: Mapping[str, Any],
    calls_by_key: Mapping[tuple[str, str], Any],
) -> tuple[Any | None, Any | None, str | None, Any]:
    """Return (visible event, call event, call id, arguments)."""

    event_id = _event_id(message)
    event_record = records_by_event_id.get(event_id) if event_id else None
    event = _as_event(event_record) if event_record is not None else None
    call_id = _call_id(message)
    call_event = None
    arguments = None
    if message.get("role") == "tool" and call_id and event is not None:
        key = (str(getattr(event, "run_id", "")), call_id)
        call_record = calls_by_key.get(key)
        call_event = _as_event(call_record) if call_record is not None else None
    elif message.get("role") == "assistant":
        calls = message.get("tool_calls")
        if isinstance(calls, list) and calls:
            first = calls[0] if isinstance(calls[0], Mapping) else {}
            value = first.get("id")
            call_id = value if isinstance(value, str) and value else None
            if event is not None:
                key = (str(getattr(event, "run_id", "")), call_id or "")
                call_record = calls_by_key.get(key)
                call_event = _as_event(call_record) if call_record is not None else event
            else:
                call_event = event
    arguments = _call_arguments(message, call_event)
    return event, call_event, call_id, arguments


def build_replay_message_metadata(
    messages: Sequence[Mapping[str, Any]],
    *,
    records_by_event_id: Mapping[str, Any] | None = None,
    calls_by_key: Mapping[tuple[str, str], Any] | None = None,
) -> tuple[ReplayMessageMeta, ...]:
    """Build one immutable sidecar item for every neutral replay message."""

    records = records_by_event_id or {}
    calls = calls_by_key or {}
    result: list[ReplayMessageMeta] = []
    for message in messages:
        event, call_event, call_id, arguments = _source_for_message(
            message, records_by_event_id=records, calls_by_key=calls
        )
        source = event or call_event
        event_id = _event_id(message)
        ordinal = None
        if source is not None:
            ordinal = _as_ordinal(records.get(event_id)) if event_id else None
            if ordinal is None:
                for candidate in records.values():
                    if _as_event(candidate) is source:
                        ordinal = _as_ordinal(candidate)
                        break
        run_id = getattr(source, "run_id", None)
        turn_id = getattr(source, "turn_id", None)
        invocation_id = getattr(source, "invocation_id", None)
        if not isinstance(run_id, str):
            run_id = None
        if not isinstance(turn_id, str):
            turn_id = None
        if not isinstance(invocation_id, str):
            invocation_id = None
        tool_name = _tool_name(message, event, call_event)
        arguments_digest = _digest(arguments) if arguments is not None else None
        range_identity, snapshot_identity, semantic_input_complete = _descriptor(
            tool_name, arguments
        )
        is_tool = message.get("role") == "tool"
        body_sha, estimated = _body_identity(message.get("content"), tool_name) if is_tool else (None, None)
        partial = bool(getattr(source, "partial", False)) if source is not None else False
        terminal_status = getattr(source, "status", None) if source is not None else None
        if not isinstance(terminal_status, str):
            terminal_status = "completed" if is_tool and source is not None and not partial else None
        completed = bool(is_tool and source is not None and not partial)
        success: bool | None = None
        if is_tool:
            response_content = getattr(source, "content", None)
            if isinstance(response_content, Mapping) and "isError" in response_content:
                success = not bool(response_content.get("isError"))
            else:
                success = True if completed else None
        step_key = (run_id, invocation_id) if run_id and invocation_id else None
        step_ordinal = None
        if call_event is not None:
            call_run_id = getattr(call_event, "run_id", None)
            call_invocation_id = getattr(call_event, "invocation_id", None)
            group_ordinals = [
                ordinal
                for candidate in calls.values()
                if getattr(_as_event(candidate), "run_id", None) == call_run_id
                and getattr(_as_event(candidate), "invocation_id", None)
                == call_invocation_id
                for ordinal in (_as_ordinal(candidate),)
                if ordinal is not None
            ]
            if group_ordinals:
                # All calls in one Provider invocation share one active step.
                # The first call ordinal gives that group a stable ordering
                # without treating parallel calls as newer semantic evidence.
                step_ordinal = min(group_ordinals)
        if step_ordinal is None:
            step_ordinal = ordinal

        if is_tool:
            core_complete = bool(
                event_id
                and ordinal is not None
                and turn_id
                and run_id
                and invocation_id
                and call_id
                and tool_name
                and arguments_digest
                and body_sha
                and completed
            )
            if core_complete:
                identity_state = "complete"
            elif event_id or ordinal is not None or turn_id or run_id or invocation_id:
                identity_state = "partial"
            else:
                identity_state = "synthetic"
        elif event_id and ordinal is not None:
            identity_state = "complete"
        elif event_id or ordinal is not None:
            identity_state = "partial"
        else:
            identity_state = "synthetic"

        result.append(
            ReplayMessageMeta(
                identity_state=identity_state,
                runtime_event_id=event_id,
                canonical_ordinal=ordinal,
                turn_id=turn_id,
                run_id=run_id,
                invocation_id=invocation_id,
                step_key=step_key,
                step_ordinal=step_ordinal,
                tool_call_id=call_id,
                tool_name=tool_name,
                arguments_digest=arguments_digest,
                semantic_input_complete=semantic_input_complete,
                range_identity=range_identity,
                snapshot_identity=snapshot_identity,
                body_sha256=body_sha,
                estimated_tokens=estimated,
                completed=completed,
                partial=partial,
                terminal_status=terminal_status,
                success=success,
            )
        )
    return tuple(result)


__all__ = ["ReplayMessageMeta", "build_replay_message_metadata"]
