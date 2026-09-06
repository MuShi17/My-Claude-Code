"""Canonical-first recovery classification and conservative startup closure."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any, Mapping

from .artifact_archive import ArtifactArchive, ArtifactArchiveError
from .compaction import CheckpointSourceMismatchError, CompactionCheckpoint, CompactionCheckpointBuilder
from .event_ids import RunContext
from .projections.base import EventRecord, RuntimeEventReducer, iter_event_records, source_digest
from .runtime_event import RuntimeEvent
from .runtime_store import CorruptionError, SQLiteRuntimeStore, SealedRunError

RECOVERY_VERSION = "recovery-v1"


@dataclass(frozen=True, slots=True)
class RecoveryDiagnostic:
    code: str
    message: str
    severity: str = "warning"
    run_id: str | None = None
    event_id: str | None = None
    ref: str | None = None
    recommended_action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {"code": self.code, "message": self.message, "severity": self.severity}
        for key, value in (
            ("run_id", self.run_id),
            ("event_id", self.event_id),
            ("ref", self.ref),
            ("recommended_action", self.recommended_action),
        ):
            if value is not None:
                result[key] = value
        return result


@dataclass(frozen=True, slots=True)
class RecoveryRecord:
    run_id: str
    session_id: str | None
    status: str
    high_water: int
    source_digest: str | None
    terminal_event_id: str | None
    uncertain_call_ids: tuple[str, ...]
    diagnostics: tuple[RecoveryDiagnostic, ...]
    recommended_action: str

    @property
    def classification(self) -> str:
        return self.status

    @property
    def terminal(self) -> bool:
        return self.status == "terminal"

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "status": self.status,
            "high_water": self.high_water,
            "source_digest": self.source_digest,
            "terminal_event_id": self.terminal_event_id,
            "uncertain_call_ids": list(self.uncertain_call_ids),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "recommended_action": self.recommended_action,
        }


def _recursive_refs(value: Any):
    if isinstance(value, Mapping):
        if value.get("kind") == "bounded_ref" and value.get("ref"):
            yield value
        for item in value.values():
            yield from _recursive_refs(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _recursive_refs(item)


class RecoveryProjection:
    """Read-only classifier; optional closure is a separate explicit method."""

    version = RECOVERY_VERSION

    def __init__(
        self,
        *,
        artifact_archive: ArtifactArchive | None = None,
        checkpoint_builder: CompactionCheckpointBuilder | None = None,
    ) -> None:
        self.artifact_archive = artifact_archive
        self.checkpoint_builder = checkpoint_builder or CompactionCheckpointBuilder()

    def _raw_run_ids(self, store: SQLiteRuntimeStore) -> set[str]:
        ids = {state.run_id for state in store.list_run_states()}
        try:
            ids.update(event.run_id for event in store.read_events())
        except CorruptionError:
            rows = store.connection.execute("SELECT DISTINCT run_id FROM runtime_events").fetchall()
            ids.update(str(row[0]) for row in rows)
        return ids

    def scan(self, store: SQLiteRuntimeStore) -> tuple[RecoveryRecord, ...]:
        records_by_run: dict[str, list[EventRecord]] = {}
        corrupt_runs: set[str] = set()
        corruption_message = ""
        try:
            records = store.read_event_records()
            for ordinal, event in records:
                records_by_run.setdefault(event.run_id, []).append(EventRecord(ordinal, event))
        except CorruptionError as error:
            corruption_message = str(error)
            rows = store.connection.execute(
                "SELECT DISTINCT run_id FROM runtime_events"
            ).fetchall()
            corrupt_runs.update(str(row[0]) for row in rows)

        results: list[RecoveryRecord] = []
        for run_id in sorted(self._raw_run_ids(store) | set(records_by_run)):
            run_records = sorted(records_by_run.get(run_id, []), key=lambda item: item.ordinal)
            state = store.run_state(run_id)
            diagnostics: list[RecoveryDiagnostic] = []
            high_water = max((item.ordinal for item in run_records), default=state.high_water if state else 0)
            digest: str | None = None
            if run_id in corrupt_runs:
                diagnostics.append(RecoveryDiagnostic(
                    "corrupt_event", corruption_message or "canonical event row failed integrity validation",
                    "error", run_id=run_id, recommended_action="inspect runtime.sqlite before retrying",
                ))
                status = "corrupt"
            else:
                digest = source_digest(run_records)
                reducer = RuntimeEventReducer(run_records)
                terminal = next((item for item in reversed(run_records) if item.event.is_terminal), None)
                dispatches: dict[str, EventRecord] = {}
                outcomes: set[str] = set()
                for item in run_records:
                    event = item.event
                    call_id = str((event.refs or {}).get("tool_call_id", ""))
                    lifecycle = (event.metadata or {}).get("lifecycle")
                    if lifecycle == "tool_dispatch" or event.kind == "tool_dispatch":
                        if call_id:
                            dispatches[call_id] = item
                    if lifecycle == "tool_outcome" or event.kind == "tool_outcome":
                        if call_id:
                            outcomes.add(call_id)
                uncertain = tuple(sorted(set(dispatches) - outcomes))
                for call_id in uncertain:
                    diagnostics.append(RecoveryDiagnostic(
                        "uncertain_tool_dispatch",
                        f"durable tool dispatch {call_id} has no committed outcome",
                        "error", run_id=run_id, event_id=dispatches[call_id].event.id,
                        recommended_action="review side effect externally; do not retry automatically",
                    ))
                for item in reducer.diagnostics:
                    diagnostics.append(RecoveryDiagnostic(
                        item.code, item.message, item.severity, run_id=run_id,
                        event_id=item.event_id, recommended_action="inspect canonical prefix",
                    ))
                for item in run_records:
                    for reference in _recursive_refs(item.event.to_dict()):
                        ref = str(reference["ref"])
                        if self.artifact_archive is not None and ref.startswith("artifact:"):
                            try:
                                self.artifact_archive.inspect(reference)
                            except ArtifactArchiveError as error:
                                diagnostics.append(RecoveryDiagnostic(
                                    error.code, str(error), "error", run_id=run_id,
                                    event_id=item.event.id, ref=ref,
                                    recommended_action="repair artifact metadata/content; keep canonical event",
                                ))
                if terminal is not None or (state is not None and state.sealed):
                    status = "terminal"
                elif uncertain:
                    status = "uncertain"
                elif run_records and all(item.event.partial for item in run_records):
                    status = "partial-only"
                elif reducer.diagnostics:
                    status = "unmatched"
                else:
                    status = "open"
            terminal_id = next(
                (item.event.id for item in reversed(run_records) if item.event.is_terminal),
                state.terminal_event_id if state else None,
            )
            uncertain_ids = tuple(
                item.message.split(" ")[3]
                for item in diagnostics
                if item.code == "uncertain_tool_dispatch"
            )
            action = {
                "terminal": "resume context or start a new explicit turn",
                "open": "append one aborted recovery terminal",
                "partial-only": "preserve bounded partial evidence; append aborted terminal if process stopped",
                "unmatched": "inspect projection diagnostics before continuing",
                "uncertain": "manual side-effect review; never auto-retry",
                "corrupt": "repair/read database copy before continuing",
            }.get(status, "inspect recovery diagnostics")
            results.append(RecoveryRecord(
                run_id=run_id,
                session_id=run_records[0].event.session_id if run_records else (state.session_id if state else None),
                status=status,
                high_water=high_water,
                source_digest=digest,
                terminal_event_id=terminal_id,
                uncertain_call_ids=uncertain_ids,
                diagnostics=tuple(diagnostics),
                recommended_action=action,
            ))
        return tuple(results)

    build = scan

    def verify_checkpoint(self, store: SQLiteRuntimeStore, checkpoint: CompactionCheckpoint | Mapping[str, Any]) -> RecoveryDiagnostic | None:
        value = checkpoint if isinstance(checkpoint, CompactionCheckpoint) else CompactionCheckpoint.from_dict(checkpoint)
        try:
            self.checkpoint_builder.verify(value, store)
        except CheckpointSourceMismatchError as error:
            return RecoveryDiagnostic("checkpoint_source_mismatch", str(error), "error", recommended_action="rebuild checkpoint from canonical prefix")
        except Exception as error:
            return RecoveryDiagnostic("checkpoint_invalid", str(error), "error", recommended_action="inspect checkpoint metadata")
        return None

    def recover_startup(
        self,
        store: SQLiteRuntimeStore,
        *,
        close_open: bool = True,
        terminal_status: str = "aborted",
    ) -> tuple[RecoveryRecord, ...]:
        records = self.scan(store)
        if not close_open:
            return records
        if terminal_status not in {"failed", "cancelled", "aborted"}:
            raise ValueError("startup closure must use a conservative terminal status")
        for item in records:
            if item.status not in {"open", "partial-only"} or item.uncertain_call_ids:
                continue
            state = store.run_state(item.run_id)
            if state is None or state.sealed:
                continue
            event_id = "recovery-terminal:" + hashlib.sha256(item.run_id.encode()).hexdigest()[:24]
            first_event = next(
                iter(store.read_events(run_id=state.run_id)), None
            )
            event = RuntimeEvent.create(
                RunContext(
                    session_id=state.session_id,
                    turn_id=f"recovery-{state.high_water}",
                    run_id=state.run_id,
                    invocation_id=state.invocation_id,
                    parent_run_id=state.parent_run_id,
                    context_id=first_event.context_id if first_event else None,
                    parent_context_id=(
                        first_event.parent_context_id if first_event else None
                    ),
                ),
                role="system",
                author="system",
                event_id=event_id,
                content={"kind": "error", "code": "startup_interrupted", "message": "run closed by canonical recovery"},
                actions={"end_run": True, "recovery": {"reason": "startup_interrupted"}},
                status=terminal_status,
                metadata={"lifecycle": "recovery_closure", "recovery_version": self.version},
                ts=int(time.time() * 1000),
            )
            try:
                store.seal_run(item.run_id, event)
            except SealedRunError:
                # Another startup/recovery worker won the race; no second
                # terminal is created and the canonical row remains intact.
                continue
        return self.scan(store)


__all__ = [
    "RECOVERY_VERSION",
    "RecoveryDiagnostic",
    "RecoveryProjection",
    "RecoveryRecord",
]
