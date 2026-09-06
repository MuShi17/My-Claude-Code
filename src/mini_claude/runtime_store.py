"""Single-writer SQLite durability for canonical RuntimeEvent facts."""

from __future__ import annotations

import json
import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .event_sink import EventSink, EventSinkError
from .context_transition import ContextTransition, ContextTransitionError
from .redaction import RedactionPolicy, bound_payload, redact_event_dict
from .runtime_event import (
    SCHEMA_VERSION as RUNTIME_EVENT_SCHEMA_VERSION,
    RuntimeEvent,
    RuntimeEventError,
    canonical_json_bytes,
)

SCHEMA_VERSION = 4


class RuntimeStoreError(RuntimeError):
    """Base class for typed SQLite store failures."""

    code = "runtime_store_error"


class StoreValidationError(RuntimeStoreError, ValueError):
    code = "validation_error"


class StoreIOError(RuntimeStoreError):
    code = "io_error"


class StoreCommitError(StoreIOError):
    code = "commit_error"


class LockedStoreError(StoreIOError):
    code = "locked"


class SchemaVersionError(RuntimeStoreError):
    code = "schema_version_error"


class CorruptionError(RuntimeStoreError):
    code = "corruption"


class SealedRunError(RuntimeStoreError):
    code = "sealed_run"


class IdempotencyConflictError(RuntimeStoreError):
    code = "idempotency_conflict"


class StoreClosedError(RuntimeStoreError):
    code = "closed"


class StoreFaultError(StoreIOError):
    code = "fault_injected"


@dataclass(frozen=True, slots=True)
class ToolOperationRecord:
    operation_id: str
    session_id: str
    run_id: str
    invocation_id: str
    turn_id: str
    provider_tool_call_id: str
    tool_name: str
    canonical_args_hash: str
    recovery_mode: str
    state: str
    dispatch_event_id: str
    outcome_event_id: str | None
    success: bool | None
    executed: bool | None
    result: Any
    result_digest: str | None
    result_size_bytes: int | None
    error_type: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class AppendResult:
    event: RuntimeEvent
    ordinal: int
    digest: str
    event_seq: int = 0
    idempotent: bool = False


@dataclass(frozen=True, slots=True)
class ImmutableEventPrefix:
    events: tuple[RuntimeEvent, ...]
    high_water: int
    digest: str

    @property
    def from_ordinal(self) -> int | None:
        # RuntimeEvent intentionally does not carry the store ordinal.  Use
        # ``read_event_records`` when a caller needs the exact first ordinal.
        return None


@dataclass(frozen=True, slots=True)
class PartialSnapshot:
    run_id: str
    high_water: int
    from_ordinal: int
    to_ordinal: int
    payload: Any
    digest: str
    version: int
    bounded: bool
    created_at: str


@dataclass(frozen=True, slots=True)
class RunState:
    run_id: str
    session_id: str
    invocation_id: str
    parent_run_id: str | None
    status: str
    sealed: bool
    terminal_event_id: str | None
    terminal_ordinal: int | None
    high_water: int
    last_event_seq: int = 0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class SQLiteRuntimeStore:
    """A local append-only canonical store with one logical writer.

    A store instance owns one SQLite connection.  Each append uses a short
    ``BEGIN IMMEDIATE`` transaction, assigns the next ordinal inside that
    transaction, and commits before returning.  No retry is performed because
    a caller may be sitting at a durable tool boundary where repeating an
    uncertain side effect is unsafe.
    """

    canonical = True
    sink_name = "sqlite-runtime-store"

    def __init__(
        self,
        database: str | Path,
        *,
        fault_hook: Any | None = None,
        timeout: float = 2.0,
        max_snapshot_bytes: int = 64 * 1024,
        redaction_policy: RedactionPolicy | None = None,
    ) -> None:
        self.database = Path(database)
        self.fault_hook = fault_hook
        self.timeout = timeout
        self.max_snapshot_bytes = max_snapshot_bytes
        self.redaction_policy = redaction_policy or RedactionPolicy()
        self._connection: sqlite3.Connection | None = None
        self._closed = False
        self._open()

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None or self._closed:
            raise StoreClosedError("runtime store is closed")
        return self._connection

    def _fault(self, point: str) -> None:
        if self.fault_hook is None:
            return
        try:
            checker = getattr(self.fault_hook, "check", None)
            if checker is not None:
                checker(point)
            elif callable(self.fault_hook):
                self.fault_hook(point)
        except RuntimeStoreError:
            raise
        except Exception as error:
            raise StoreFaultError(str(error)) from error

    def _open(self) -> None:
        self._fault("store.open")
        try:
            if self.database != Path(":memory:"):
                self.database.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(
                ":memory:" if str(self.database) == ":memory:" else str(self.database),
                timeout=self.timeout,
                isolation_level=None,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 2000")
            self._migrate()
        except SchemaVersionError:
            self._dispose_connection()
            raise
        except sqlite3.OperationalError as error:
            self._dispose_connection()
            self._raise_sqlite(error, operation="open")
        except OSError as error:
            self._dispose_connection()
            raise StoreIOError(str(error)) from error

    def _dispose_connection(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            finally:
                self._connection = None

    def _migrate(self) -> None:
        connection = self.connection
        current = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if current > SCHEMA_VERSION:
            raise SchemaVersionError(
                f"database schema {current} is newer than supported {SCHEMA_VERSION}"
            )
        self._fault("store.migrate")
        connection.execute("BEGIN")
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runtime_events (
                    event_id TEXT PRIMARY KEY,
                    ordinal INTEGER NOT NULL UNIQUE,
                    event_seq INTEGER NOT NULL,
                    schema_version INTEGER NOT NULL,
                    session_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    invocation_id TEXT NOT NULL,
                    context_id TEXT,
                    parent_context_id TEXT,
                    parent_run_id TEXT,
                    ts INTEGER NOT NULL,
                    partial INTEGER NOT NULL,
                    terminal INTEGER NOT NULL,
                    digest TEXT NOT NULL,
                    event_json BLOB NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_events_session_ordinal
                    ON runtime_events(session_id, ordinal);
                CREATE INDEX IF NOT EXISTS idx_runtime_events_turn_ordinal
                    ON runtime_events(turn_id, ordinal);
                CREATE INDEX IF NOT EXISTS idx_runtime_events_run_ordinal
                    ON runtime_events(run_id, ordinal);
                CREATE INDEX IF NOT EXISTS idx_runtime_events_invocation_ordinal
                    ON runtime_events(invocation_id, ordinal);
                CREATE INDEX IF NOT EXISTS idx_runtime_events_context_ordinal
                    ON runtime_events(context_id, ordinal);

                CREATE TABLE IF NOT EXISTS runtime_session_event_ordinals (
                    session_id TEXT PRIMARY KEY,
                    high_water INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runtime_run_state (
                    run_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    invocation_id TEXT NOT NULL,
                    parent_run_id TEXT,
                    status TEXT NOT NULL,
                    sealed INTEGER NOT NULL DEFAULT 0,
                    terminal_event_id TEXT,
                    terminal_ordinal INTEGER,
                    high_water INTEGER NOT NULL DEFAULT 0,
                    last_event_seq INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS runtime_partial_snapshots (
                    run_id TEXT PRIMARY KEY,
                    high_water INTEGER NOT NULL,
                    from_ordinal INTEGER NOT NULL,
                    to_ordinal INTEGER NOT NULL,
                    payload_json BLOB NOT NULL,
                    digest TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    bounded INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runtime_llm_captures (
                    llm_ref TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    run_id TEXT,
                    request_id TEXT,
                    model TEXT,
                    capture_status TEXT NOT NULL,
                    metadata_json BLOB NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runtime_artifacts (
                    ref TEXT PRIMARY KEY,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    mime_type TEXT NOT NULL,
                    encoding TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    redaction_version TEXT NOT NULL,
                    metadata_json BLOB NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runtime_compaction_checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    source_high_water INTEGER NOT NULL,
                    source_digest TEXT NOT NULL,
                    canonical_schema_version INTEGER NOT NULL,
                    compaction_version TEXT NOT NULL,
                    projection_version TEXT NOT NULL,
                    coverage_json BLOB NOT NULL,
                    checkpoint_json BLOB NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runtime_tool_operations (
                    operation_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    invocation_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    provider_tool_call_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    canonical_args_hash TEXT NOT NULL,
                    recovery_mode TEXT NOT NULL,
                    state TEXT NOT NULL,
                    dispatch_event_id TEXT NOT NULL UNIQUE,
                    outcome_event_id TEXT UNIQUE,
                    success INTEGER,
                    executed INTEGER,
                    result_json BLOB,
                    result_digest TEXT,
                    result_size_bytes INTEGER,
                    error_type TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(invocation_id, provider_tool_call_id)
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_tool_operations_run_state
                    ON runtime_tool_operations(run_id, state, created_at);
                CREATE INDEX IF NOT EXISTS idx_runtime_tool_operations_invocation
                    ON runtime_tool_operations(invocation_id, created_at);

                CREATE TABLE IF NOT EXISTS runtime_tool_journal (
                    journal_id TEXT PRIMARY KEY,
                    operation_id TEXT NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    event_kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    payload_json BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(operation_id) REFERENCES runtime_tool_operations(operation_id)
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_tool_journal_operation
                    ON runtime_tool_journal(operation_id, created_at);
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(runtime_events)").fetchall()
            }
            if "event_seq" not in columns:
                connection.execute("ALTER TABLE runtime_events ADD COLUMN event_seq INTEGER")
                rows = connection.execute(
                    "SELECT event_id, invocation_id FROM runtime_events ORDER BY ordinal ASC"
                ).fetchall()
                counters: dict[str, int] = {}
                for row in rows:
                    invocation_id = str(row["invocation_id"])
                    counters[invocation_id] = counters.get(invocation_id, 0) + 1
                    connection.execute(
                        "UPDATE runtime_events SET event_seq = ? WHERE event_id = ?",
                        (counters[invocation_id], row["event_id"]),
                    )
            if "context_id" not in columns:
                connection.execute("ALTER TABLE runtime_events ADD COLUMN context_id TEXT")
            if "parent_context_id" not in columns:
                connection.execute("ALTER TABLE runtime_events ADD COLUMN parent_context_id TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_runtime_events_context_ordinal "
                "ON runtime_events(context_id, ordinal)"
            )
            run_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(runtime_run_state)").fetchall()
            }
            if "last_event_seq" not in run_columns:
                connection.execute(
                    "ALTER TABLE runtime_run_state ADD COLUMN last_event_seq INTEGER NOT NULL DEFAULT 0"
                )
                connection.execute(
                    """
                    UPDATE runtime_run_state
                    SET last_event_seq = COALESCE((
                        SELECT MAX(event_seq) FROM runtime_events
                        WHERE runtime_events.run_id = runtime_run_state.run_id
                    ), 0)
                    """
                )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_events_invocation_event_seq "
                "ON runtime_events(invocation_id, event_seq)"
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _ensure_open(self) -> sqlite3.Connection:
        return self.connection

    @staticmethod
    def _raise_sqlite(error: sqlite3.Error, *, operation: str) -> None:
        message = str(error)
        if "locked" in message.lower() or "busy" in message.lower():
            raise LockedStoreError(f"SQLite {operation} is locked: {message}") from error
        raise StoreIOError(f"SQLite {operation} failed: {message}") from error

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> RuntimeEvent:
        try:
            raw = row["event_json"]
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            data = json.loads(raw)
            event = RuntimeEvent.from_dict(data)
        except (RuntimeEventError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise CorruptionError(f"cannot decode runtime_events event_id={row['event_id']}: {error}") from error
        if (
            event.id != row["event_id"]
            or event.session_id != row["session_id"]
            or event.run_id != row["run_id"]
            or event.invocation_id != row["invocation_id"]
            or (row["context_id"] is not None and event.context_id != row["context_id"])
            or (row["parent_context_id"] is not None and event.parent_context_id != row["parent_context_id"])
            or event.digest() != row["digest"]
        ):
            raise CorruptionError(f"digest mismatch for runtime_events event_id={row['event_id']}")
        return event

    def _existing_result(self, event: RuntimeEvent) -> AppendResult | None:
        row = self.connection.execute(
            "SELECT * FROM runtime_events WHERE event_id = ?", (event.id,)
        ).fetchone()
        if row is None:
            return None
        stored = self._event_from_row(row)
        if stored.digest() != event.digest():
            raise IdempotencyConflictError(f"event id {event.id} has a different payload")
        return AppendResult(
            stored,
            int(row["ordinal"]),
            row["digest"],
            event_seq=int(row["event_seq"]),
            idempotent=True,
        )

    def append(self, event: RuntimeEvent | Mapping[str, Any]) -> AppendResult:
        try:
            canonical = event if isinstance(event, RuntimeEvent) else RuntimeEvent.from_dict(event)
            canonical.validate()
        except RuntimeEventError as error:
            raise StoreValidationError(str(error)) from error
        self._ensure_open()
        self._fault("store.append")
        # The fixture matrix can inject a read/decode fault at the append
        # boundary too, which models a caller discovering corruption before a
        # retry.  Normal operation never performs this extra check.
        self._fault("store.corrupt_read")
        digest = canonical.digest()
        connection = self.connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            result = self._append_in_transaction(canonical, connection)
            self._fault("store.commit")
            connection.commit()
            return result
        except (SealedRunError, IdempotencyConflictError, StoreFaultError, RuntimeStoreError):
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.IntegrityError as error:
            if connection.in_transaction:
                connection.rollback()
            raise StoreIOError(f"SQLite append constraint failed: {error}") from error
        except sqlite3.OperationalError as error:
            if connection.in_transaction:
                connection.rollback()
            if "commit" in str(error).lower():
                raise StoreCommitError(str(error)) from error
            self._raise_sqlite(error, operation="append")
        except Exception as error:
            if connection.in_transaction:
                connection.rollback()
            raise StoreIOError(f"SQLite append failed: {error}") from error

    def _append_in_transaction(
        self, canonical: RuntimeEvent, connection: sqlite3.Connection
    ) -> AppendResult:
        existing = self._existing_result(canonical)
        if existing is not None:
            return existing
        state = connection.execute(
            """
            SELECT session_id, invocation_id, sealed, terminal_event_id, high_water, last_event_seq
            FROM runtime_run_state WHERE run_id = ?
            """,
            (canonical.run_id,),
        ).fetchone()
        if state is not None and state["session_id"] != canonical.session_id:
            raise StoreValidationError(
                f"run {canonical.run_id} identity does not match canonical event"
            )
        if state is not None and bool(state["sealed"]):
            raise SealedRunError(
                f"run {canonical.run_id} is sealed by {state['terminal_event_id']}"
            )
        existing_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM runtime_events WHERE invocation_id = ?",
                (canonical.invocation_id,),
            ).fetchone()[0]
        )
        if existing_count == 0 and canonical.kind != "invocation_opened":
            raise StoreValidationError(
                "the first canonical event for an invocation must be invocation_opened"
            )
        if existing_count > 0 and canonical.kind == "invocation_opened":
            raise StoreValidationError("an invocation can have only one opening event")
        event_seq = int(
            connection.execute(
                "SELECT COALESCE(MAX(event_seq), 0) + 1 FROM runtime_events WHERE invocation_id = ?",
                (canonical.invocation_id,),
            ).fetchone()[0]
        )
        ordinal = int(
            connection.execute(
                "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM runtime_events"
            ).fetchone()[0]
        )
        now = _utc_now()
        connection.execute(
            """
            INSERT INTO runtime_events(
                event_id, ordinal, event_seq, schema_version, session_id, turn_id, run_id,
                invocation_id, context_id, parent_context_id, parent_run_id, ts, partial, terminal, digest,
                event_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                canonical.id, ordinal, event_seq, canonical.schema_version,
                canonical.session_id, canonical.turn_id, canonical.run_id,
                canonical.invocation_id, canonical.context_id, canonical.parent_context_id,
                canonical.parent_run_id, canonical.ts,
                int(canonical.partial), int(canonical.is_terminal), canonical.digest(),
                canonical.canonical_bytes(), now,
            ),
        )
        connection.execute(
            """
            INSERT INTO runtime_session_event_ordinals(session_id, high_water)
            VALUES (?, ?)
            ON CONFLICT(session_id) DO UPDATE SET high_water = excluded.high_water
            """,
            (canonical.session_id, ordinal),
        )
        if state is None:
            connection.execute(
                """
                INSERT INTO runtime_run_state(
                    run_id, session_id, invocation_id, parent_run_id, status,
                    sealed, terminal_event_id, terminal_ordinal, high_water, last_event_seq
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    canonical.run_id, canonical.session_id, canonical.invocation_id,
                    canonical.parent_run_id, canonical.status or "open",
                    int(canonical.is_terminal), canonical.id if canonical.is_terminal else None,
                    ordinal if canonical.is_terminal else None, ordinal, event_seq,
                ),
            )
        else:
            connection.execute(
                """
                UPDATE runtime_run_state
                SET status = CASE WHEN ? THEN ? ELSE status END,
                    sealed = CASE WHEN ? THEN 1 ELSE sealed END,
                    terminal_event_id = CASE WHEN ? THEN ? ELSE terminal_event_id END,
                    terminal_ordinal = CASE WHEN ? THEN ? ELSE terminal_ordinal END,
                    high_water = ?, last_event_seq = ?
                WHERE run_id = ?
                """,
                (
                    int(canonical.is_terminal), canonical.status or "open",
                    int(canonical.is_terminal), int(canonical.is_terminal), canonical.id,
                    int(canonical.is_terminal), ordinal if canonical.is_terminal else None,
                    ordinal, event_seq, canonical.run_id,
                ),
            )
        self._apply_tool_operation(connection, canonical, ordinal=ordinal, created_at=now)
        return AppendResult(
            canonical, ordinal, canonical.digest(), event_seq=event_seq, idempotent=False
        )

    @staticmethod
    def _apply_tool_operation(
        connection: sqlite3.Connection,
        event: RuntimeEvent,
        *,
        ordinal: int,
        created_at: str,
    ) -> None:
        """Project operation state inside the same transaction as its event."""

        actions = event.actions or {}
        dispatch = actions.get("tool_dispatch")
        outcome = actions.get("tool_outcome")
        if dispatch is not None:
            if not isinstance(dispatch, Mapping):
                raise StoreValidationError("tool_dispatch action must be an object")
            refs = event.refs or {}
            operation_id = dispatch.get("operation_id") or refs.get("operation_id")
            provider_call_id = dispatch.get("provider_tool_call_id") or refs.get("tool_call_id")
            tool_name = dispatch.get("tool_name") or dispatch.get("name")
            args_hash = dispatch.get("canonical_args_hash") or dispatch.get("arguments_digest")
            recovery_mode = dispatch.get("recovery_mode", "manual_on_unknown")
            values = {
                "operation_id": operation_id,
                "provider_tool_call_id": provider_call_id,
                "tool_name": tool_name,
                "canonical_args_hash": args_hash,
                "recovery_mode": recovery_mode,
            }
            missing = [key for key, value in values.items() if not isinstance(value, str) or not value.strip()]
            if missing:
                raise StoreValidationError(
                    "tool_dispatch requires " + ", ".join(missing)
                )
            existing = connection.execute(
                "SELECT * FROM runtime_tool_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            by_call = connection.execute(
                "SELECT * FROM runtime_tool_operations "
                "WHERE invocation_id = ? AND provider_tool_call_id = ?",
                (event.invocation_id, provider_call_id),
            ).fetchone()
            identity = (
                event.session_id,
                event.run_id,
                event.invocation_id,
                event.turn_id,
                provider_call_id,
                tool_name,
                args_hash,
                recovery_mode,
            )
            if existing is not None or by_call is not None:
                row = existing or by_call
                stored_identity = (
                    row["session_id"], row["run_id"], row["invocation_id"], row["turn_id"],
                    row["provider_tool_call_id"], row["tool_name"],
                    row["canonical_args_hash"], row["recovery_mode"],
                )
                if row["operation_id"] != operation_id or stored_identity != identity:
                    raise IdempotencyConflictError(
                        f"tool operation identity conflict for {provider_call_id}"
                    )
            else:
                connection.execute(
                    """
                    INSERT INTO runtime_tool_operations(
                        operation_id, session_id, run_id, invocation_id, turn_id,
                        provider_tool_call_id, tool_name, canonical_args_hash,
                        recovery_mode, state, dispatch_event_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'dispatched', ?, ?, ?)
                    """,
                    (
                        operation_id, event.session_id, event.run_id, event.invocation_id,
                        event.turn_id, provider_call_id, tool_name, args_hash,
                        recovery_mode, event.id, created_at, created_at,
                    ),
                )
            connection.execute(
                """
                INSERT INTO runtime_tool_journal(
                    journal_id, operation_id, event_id, event_kind, state, payload_json, created_at
                ) VALUES (?, ?, ?, 'dispatch', 'dispatched', ?, ?)
                """,
                (
                    f"tool-journal:{event.id}", operation_id, event.id,
                    canonical_json_bytes({"ordinal": ordinal, "action": dispatch}), created_at,
                ),
            )

        if outcome is None:
            return
        if not isinstance(outcome, Mapping):
            raise StoreValidationError("tool_outcome action must be an object")
        refs = event.refs or {}
        operation_id = outcome.get("operation_id") or refs.get("operation_id")
        if not isinstance(operation_id, str) or not operation_id.strip():
            return
        row = connection.execute(
            "SELECT * FROM runtime_tool_operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        if row is None:
            raise StoreValidationError(f"tool outcome has no durable dispatch: {operation_id}")
        success = outcome.get("success")
        executed = outcome.get("executed")
        if not isinstance(success, bool) or not isinstance(executed, bool):
            raise StoreValidationError("tool_outcome requires boolean success and executed")
        content = event.content or {}
        result = content.get("result") if isinstance(content, Mapping) else None
        provider_call_id = (
            outcome.get("provider_tool_call_id")
            or (content.get("id") if isinstance(content, Mapping) else None)
            or refs.get("tool_call_id")
        )
        tool_name = (
            outcome.get("tool_name")
            or outcome.get("name")
            or (content.get("name") if isinstance(content, Mapping) else None)
        )
        if not isinstance(provider_call_id, str) or not provider_call_id.strip():
            raise StoreValidationError("tool_outcome requires provider_tool_call_id")
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise StoreValidationError("tool_outcome requires tool_name")
        stored_identity = (
            row["session_id"],
            row["run_id"],
            row["invocation_id"],
            row["turn_id"],
            row["provider_tool_call_id"],
            row["tool_name"],
        )
        outcome_identity = (
            event.session_id,
            event.run_id,
            event.invocation_id,
            event.turn_id,
            provider_call_id,
            tool_name,
        )
        if stored_identity != outcome_identity:
            raise IdempotencyConflictError(
                f"tool outcome identity conflict for operation {operation_id}"
            )
        result_json = canonical_json_bytes(result)
        result_digest = "sha256:" + hashlib.sha256(result_json).hexdigest()
        state = "completed" if success else ("denied" if not executed else "failed")
        error_type = outcome.get("error_type")
        if error_type is not None and not isinstance(error_type, str):
            raise StoreValidationError("tool_outcome error_type must be a string")
        if row["state"] in {"completed", "failed", "denied", "cancelled"}:
            # A different event id is a second outcome fact, even when its
            # payload happens to match.  Exact re-submission is handled by
            # append's event-id/digest idempotency check before this method.
            raise IdempotencyConflictError(
                f"tool outcome already recorded for operation {operation_id}"
            )
        connection.execute(
            """
            UPDATE runtime_tool_operations
            SET state = ?, outcome_event_id = COALESCE(outcome_event_id, ?),
                success = ?, executed = ?, result_json = ?, result_digest = ?,
                result_size_bytes = ?, error_type = ?, updated_at = ?
            WHERE operation_id = ?
            """,
            (
                state, event.id, int(success), int(executed), result_json, result_digest,
                len(result_json), error_type, created_at, operation_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO runtime_tool_journal(
                journal_id, operation_id, event_id, event_kind, state, payload_json, created_at
            ) VALUES (?, ?, ?, 'outcome', ?, ?, ?)
            """,
            (
                f"tool-journal:{event.id}", operation_id, event.id, state,
                canonical_json_bytes({"ordinal": ordinal, "action": outcome}), created_at,
            ),
        )

    emit = append

    def read_events(
        self,
        *,
        session_id: str | None = None,
        turn_id: str | None = None,
        run_id: str | None = None,
        invocation_id: str | None = None,
        context_id: str | None = None,
        upto_ordinal: int | None = None,
        high_water: int | None = None,
        after_ordinal: int | None = None,
    ) -> list[RuntimeEvent]:
        try:
            return [event for _, event in self.read_event_records(
                session_id=session_id,
                turn_id=turn_id,
                run_id=run_id,
                invocation_id=invocation_id,
                context_id=context_id,
                upto_ordinal=upto_ordinal,
                high_water=high_water,
                after_ordinal=after_ordinal,
            )]
        except CorruptionError:
            raise
        except sqlite3.Error as error:
            self._raise_sqlite(error, operation="read")
            raise AssertionError("unreachable")

    @staticmethod
    def _tool_operation_from_row(row: sqlite3.Row) -> ToolOperationRecord:
        result = None
        if row["result_json"] is not None:
            try:
                raw = row["result_json"]
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                result = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise CorruptionError(
                    f"cannot decode tool operation {row['operation_id']} result: {error}"
                ) from error
        return ToolOperationRecord(
            operation_id=str(row["operation_id"]),
            session_id=str(row["session_id"]),
            run_id=str(row["run_id"]),
            invocation_id=str(row["invocation_id"]),
            turn_id=str(row["turn_id"]),
            provider_tool_call_id=str(row["provider_tool_call_id"]),
            tool_name=str(row["tool_name"]),
            canonical_args_hash=str(row["canonical_args_hash"]),
            recovery_mode=str(row["recovery_mode"]),
            state=("outcome_unknown" if row["state"] == "dispatched" else str(row["state"])),
            dispatch_event_id=str(row["dispatch_event_id"]),
            outcome_event_id=str(row["outcome_event_id"]) if row["outcome_event_id"] else None,
            success=None if row["success"] is None else bool(row["success"]),
            executed=None if row["executed"] is None else bool(row["executed"]),
            result=result,
            result_digest=str(row["result_digest"]) if row["result_digest"] else None,
            result_size_bytes=(
                int(row["result_size_bytes"]) if row["result_size_bytes"] is not None else None
            ),
            error_type=str(row["error_type"]) if row["error_type"] else None,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def read_tool_operation(self, operation_id: str) -> ToolOperationRecord | None:
        self._ensure_open()
        row = self.connection.execute(
            "SELECT * FROM runtime_tool_operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        return self._tool_operation_from_row(row) if row is not None else None

    def read_tool_operation_for_call(
        self, invocation_id: str, provider_tool_call_id: str
    ) -> ToolOperationRecord | None:
        self._ensure_open()
        row = self.connection.execute(
            "SELECT * FROM runtime_tool_operations "
            "WHERE invocation_id = ? AND provider_tool_call_id = ?",
            (invocation_id, provider_tool_call_id),
        ).fetchone()
        return self._tool_operation_from_row(row) if row is not None else None

    def read_tool_operations(
        self, *, run_id: str | None = None, invocation_id: str | None = None
    ) -> list[ToolOperationRecord]:
        self._ensure_open()
        clauses: list[str] = []
        params: list[str] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if invocation_id is not None:
            clauses.append("invocation_id = ?")
            params.append(invocation_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.connection.execute(
            f"SELECT * FROM runtime_tool_operations{where} ORDER BY created_at, operation_id",
            params,
        ).fetchall()
        return [self._tool_operation_from_row(row) for row in rows]

    def mark_unknown_tool_operations(self, *, run_id: str | None = None) -> int:
        """Materialize the conservative recovery state for dispatch-only operations."""

        self._ensure_open()
        where = "WHERE state = 'dispatched'"
        params: tuple[str, ...] = ()
        if run_id is not None:
            where += " AND run_id = ?"
            params = (run_id,)
        connection = self.connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                f"UPDATE runtime_tool_operations SET state = 'outcome_unknown', updated_at = ? {where}",
                (_utc_now(), *params),
            )
            connection.commit()
            return int(cursor.rowcount)
        except sqlite3.Error as error:
            if connection.in_transaction:
                connection.rollback()
            self._raise_sqlite(error, operation="tool recovery")
            raise AssertionError("unreachable")

    def read_event_records(
        self,
        *,
        session_id: str | None = None,
        turn_id: str | None = None,
        run_id: str | None = None,
        invocation_id: str | None = None,
        context_id: str | None = None,
        upto_ordinal: int | None = None,
        high_water: int | None = None,
        after_ordinal: int | None = None,
    ) -> list[tuple[int, RuntimeEvent]]:
        """Read ``(store ordinal, event)`` pairs without changing the store."""

        connection = self._ensure_open()
        self._fault("store.corrupt_read")
        # A suffix read is the normal warm-replay path.  Append transactions
        # already enforce the per-invocation sequence, so scanning the entire
        # ledger here would turn an incremental query back into O(history).
        # Cold reads and explicit audit reads retain full validation.
        if after_ordinal is None:
            self._validate_event_sequences(connection)
        clauses: list[str] = []
        parameters: list[Any] = []
        for field, value in (
            ("session_id", session_id),
            ("turn_id", turn_id),
            ("run_id", run_id),
            ("invocation_id", invocation_id),
        ):
            if value is not None:
                clauses.append(f"{field} = ?")
                parameters.append(value)
        if context_id is not None:
            # Events written before context_id was introduced are assigned to
            # their session root on read; child contexts never match NULL.
            clauses.append(
                "(context_id = ? OR (context_id IS NULL AND ? = 'context:' || session_id))"
            )
            parameters.extend((context_id, context_id))
        for bound in (upto_ordinal, high_water):
            if bound is not None:
                clauses.append("ordinal <= ?")
                parameters.append(bound)
        if after_ordinal is not None:
            clauses.append("ordinal > ?")
            parameters.append(after_ordinal)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        try:
            rows = connection.execute(
                f"SELECT * FROM runtime_events{where} ORDER BY ordinal ASC", parameters
            ).fetchall()
            return [(int(row["ordinal"]), self._event_from_row(row)) for row in rows]
        except CorruptionError:
            raise
        except sqlite3.Error as error:
            self._raise_sqlite(error, operation="read")
            raise AssertionError("unreachable")

    def read_event(self, event_id: str) -> RuntimeEvent | None:
        """Read one immutable event by identity without scanning the ledger."""

        connection = self._ensure_open()
        self._fault("store.corrupt_read")
        try:
            row = connection.execute(
                "SELECT * FROM runtime_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            return self._event_from_row(row) if row is not None else None
        except CorruptionError:
            raise
        except sqlite3.Error as error:
            self._raise_sqlite(error, operation="read")
            raise AssertionError("unreachable")

    @staticmethod
    def _validate_event_sequences(connection: sqlite3.Connection) -> None:
        """Reject a ledger with a missing or duplicated invocation sequence."""

        rows = connection.execute(
            "SELECT invocation_id, event_seq FROM runtime_events "
            "ORDER BY invocation_id ASC, event_seq ASC"
        ).fetchall()
        expected: dict[str, int] = {}
        for row in rows:
            invocation_id = str(row["invocation_id"])
            try:
                sequence = int(row["event_seq"])
            except (TypeError, ValueError) as error:
                raise CorruptionError(
                    f"invalid event_seq for invocation {invocation_id}"
                ) from error
            next_sequence = expected.get(invocation_id, 0) + 1
            if sequence != next_sequence:
                raise CorruptionError(
                    f"event sequence gap for invocation {invocation_id}: "
                    f"expected {next_sequence}, got {sequence}"
                )
            expected[invocation_id] = sequence

    def high_water(
        self, *, session_id: str | None = None, run_id: str | None = None
    ) -> int:
        connection = self._ensure_open()
        if session_id is not None and run_id is not None:
            raise StoreValidationError("choose session_id or run_id, not both")
        if run_id is not None:
            row = connection.execute(
                "SELECT high_water FROM runtime_run_state WHERE run_id = ?", (run_id,)
            ).fetchone()
        elif session_id is not None:
            row = connection.execute(
                "SELECT high_water FROM runtime_session_event_ordinals WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        else:
            row = connection.execute("SELECT COALESCE(MAX(ordinal), 0) FROM runtime_events").fetchone()
        return int(row[0]) if row is not None else 0

    @property
    def current_high_water(self) -> int:
        return self.high_water()

    def read_immutable_prefix(
        self,
        *,
        run_id: str | None = None,
        session_id: str | None = None,
        high_water: int | None = None,
    ) -> ImmutableEventPrefix:
        events = self.read_events(run_id=run_id, session_id=session_id, high_water=high_water)
        actual_high_water = high_water if high_water is not None else self.high_water(run_id=run_id, session_id=session_id)
        digest = _events_digest(events)
        return ImmutableEventPrefix(tuple(events), actual_high_water, digest)

    read_prefix = read_immutable_prefix

    def run_state(self, run_id: str) -> RunState | None:
        row = self.connection.execute(
            "SELECT * FROM runtime_run_state WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return RunState(
            run_id=row["run_id"],
            session_id=row["session_id"],
            invocation_id=row["invocation_id"],
            parent_run_id=row["parent_run_id"],
            status=row["status"],
            sealed=bool(row["sealed"]),
            terminal_event_id=row["terminal_event_id"],
            terminal_ordinal=row["terminal_ordinal"],
            high_water=int(row["high_water"]),
            last_event_seq=int(row["last_event_seq"]),
        )

    def list_run_states(self) -> list[RunState]:
        self._ensure_open()
        rows = self.connection.execute(
            "SELECT run_id FROM runtime_run_state ORDER BY high_water, run_id"
        ).fetchall()
        return [state for row in rows if (state := self.run_state(row["run_id"])) is not None]

    def list_session_ids(self) -> list[str]:
        self._ensure_open()
        rows = self.connection.execute(
            "SELECT session_id FROM runtime_session_event_ordinals ORDER BY high_water, session_id"
        ).fetchall()
        return [str(row["session_id"]) for row in rows]

    def is_sealed(self, run_id: str) -> bool:
        state = self.run_state(run_id)
        return bool(state and state.sealed)

    def seal_run(
        self,
        run_id: str | RuntimeEvent,
        event: RuntimeEvent | Mapping[str, Any] | None = None,
    ) -> AppendResult:
        if isinstance(run_id, RuntimeEvent):
            event = run_id
            run_id = event.run_id
        if event is None:
            raise StoreValidationError("seal_run requires a terminal RuntimeEvent")
        canonical = event if isinstance(event, RuntimeEvent) else RuntimeEvent.from_dict(event)
        if canonical.run_id != run_id:
            raise StoreValidationError("terminal event run_id does not match seal target")
        if not canonical.is_terminal:
            raise StoreValidationError("seal event must have a terminal status")
        return self.append(canonical)

    def write_partial_snapshot(
        self,
        run_id: str,
        payload: Any,
        *,
        high_water: int | None = None,
        from_ordinal: int | None = None,
        to_ordinal: int | None = None,
        version: int = 1,
    ) -> PartialSnapshot:
        self._ensure_open()
        if version < 1:
            raise StoreValidationError("snapshot version must be positive")
        water = self.high_water(run_id=run_id) if high_water is None else high_water
        start = water if from_ordinal is None else from_ordinal
        end = water if to_ordinal is None else to_ordinal
        if start < 0 or end < start or water < end:
            raise StoreValidationError("snapshot coverage is invalid")
        clean, _ = redact_event_dict(
            {
                "schema_version": 1,
                "id": "snapshot",
                "invocation_id": "snapshot",
                "run_id": run_id,
                "session_id": "snapshot",
                "turn_id": "snapshot",
                "ts": 0,
                "partial": True,
                "role": "system",
                "author": "system",
                "content": {"kind": "text", "text": "snapshot"},
                "metadata": {"payload": payload},
            },
            self.redaction_policy,
        )
        safe_payload = clean["metadata"]["payload"]
        encoded = canonical_json_bytes(safe_payload)
        bounded = len(encoded) > self.max_snapshot_bytes
        stored_payload = (
            bound_payload(
                safe_payload,
                ref=f"snapshot:{run_id}:{water}",
                policy=RedactionPolicy(
                    version=self.redaction_policy.version,
                    max_inline_bytes=self.max_snapshot_bytes,
                    max_string_chars=self.redaction_policy.max_string_chars,
                ),
            )
            if bounded
            else safe_payload
        )
        stored_encoded = canonical_json_bytes(stored_payload)
        digest = __import__("hashlib").sha256(encoded).hexdigest()
        now = _utc_now()
        connection = self.connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO runtime_partial_snapshots(
                    run_id, high_water, from_ordinal, to_ordinal, payload_json,
                    digest, version, bounded, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    high_water = excluded.high_water,
                    from_ordinal = excluded.from_ordinal,
                    to_ordinal = excluded.to_ordinal,
                    payload_json = excluded.payload_json,
                    digest = excluded.digest,
                    version = excluded.version,
                    bounded = excluded.bounded,
                    created_at = excluded.created_at
                """,
                (run_id, water, start, end, stored_encoded, digest, version, int(bounded), now),
            )
            connection.commit()
        except sqlite3.Error as error:
            if connection.in_transaction:
                connection.rollback()
            self._raise_sqlite(error, operation="snapshot")
        return PartialSnapshot(run_id, water, start, end, stored_payload, digest, version, bounded, now)

    def read_partial_snapshot(self, run_id: str) -> PartialSnapshot | None:
        row = self.connection.execute(
            "SELECT * FROM runtime_partial_snapshots WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        try:
            raw = row["payload_json"]
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CorruptionError(f"cannot decode snapshot run_id={run_id}: {error}") from error
        return PartialSnapshot(
            run_id=row["run_id"],
            high_water=int(row["high_water"]),
            from_ordinal=int(row["from_ordinal"]),
            to_ordinal=int(row["to_ordinal"]),
            payload=payload,
            digest=row["digest"],
            version=int(row["version"]),
            bounded=bool(row["bounded"]),
            created_at=row["created_at"],
        )

    def write_llm_capture(
        self,
        llm_ref: str,
        *,
        session_id: str,
        run_id: str | None = None,
        request_id: str | None = None,
        model: str | None = None,
        capture_status: str = "saved",
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        if not llm_ref.strip():
            raise StoreValidationError("llm_ref must not be empty")
        encoded = canonical_json_bytes(redact_event_dict({
            "schema_version": 1, "id": "llm", "invocation_id": "llm", "run_id": run_id or "llm",
            "session_id": session_id, "turn_id": "llm", "ts": 0, "partial": False,
            "role": "system", "author": "system", "content": {"kind": "text", "text": "llm"},
            "metadata": dict(metadata or {}),
        }, self.redaction_policy)[0]["metadata"])
        connection = self.connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO runtime_llm_captures(
                    llm_ref, session_id, run_id, request_id, model, capture_status,
                    metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(llm_ref) DO UPDATE SET
                    capture_status = excluded.capture_status,
                    metadata_json = excluded.metadata_json
                """,
                (llm_ref, session_id, run_id, request_id, model, capture_status, encoded, _utc_now()),
            )
            connection.commit()
        except sqlite3.Error as error:
            if connection.in_transaction:
                connection.rollback()
            self._raise_sqlite(error, operation="llm capture")

    def read_llm_capture(self, llm_ref: str) -> dict[str, Any] | None:
        self._ensure_open()
        row = self.connection.execute(
            "SELECT * FROM runtime_llm_captures WHERE llm_ref = ?", (llm_ref,)
        ).fetchone()
        if row is None:
            return None
        try:
            raw = row["metadata_json"]
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            metadata = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CorruptionError(f"cannot decode LLM capture ref={llm_ref}: {error}") from error
        return {
            "llm_ref": row["llm_ref"],
            "session_id": row["session_id"],
            "run_id": row["run_id"],
            "request_id": row["request_id"],
            "model": row["model"],
            "capture_status": row["capture_status"],
            "metadata": metadata,
            "created_at": row["created_at"],
        }

    def write_artifact_metadata(
        self,
        ref: str,
        *,
        sha256: str,
        size_bytes: int,
        mime_type: str,
        encoding: str = "binary",
        scope: str = "runtime",
        redaction_version: str = "unknown",
        metadata: Mapping[str, Any] | None = None,
        created_at: str | None = None,
    ) -> None:
        """Mirror archive metadata without making it canonical history."""

        if not ref.strip() or not sha256.strip() or size_bytes < 0:
            raise StoreValidationError("artifact metadata identity and size are invalid")
        self._ensure_open()
        self._fault("store.artifact_metadata")
        encoded = canonical_json_bytes(redact_event_dict({
            "schema_version": 1,
            "id": "artifact-metadata",
            "invocation_id": "artifact-metadata",
            "run_id": "artifact-metadata",
            "session_id": "artifact-metadata",
            "turn_id": "artifact-metadata",
            "ts": 0,
            "partial": True,
            "role": "system",
            "author": "system",
            "content": {"kind": "text", "text": "artifact metadata"},
            "metadata": dict(metadata or {}),
        }, self.redaction_policy)[0]["metadata"])
        connection = self.connection
        try:
            existing = connection.execute(
                "SELECT sha256, size_bytes, mime_type, encoding, scope, redaction_version FROM runtime_artifacts WHERE ref = ?",
                (ref,),
            ).fetchone()
            identity = (sha256, int(size_bytes), mime_type, encoding, scope, redaction_version)
            if existing is not None and tuple(existing) != identity:
                raise IdempotencyConflictError(f"artifact ref {ref} has different metadata")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO runtime_artifacts(
                    ref, sha256, size_bytes, mime_type, encoding, scope,
                    redaction_version, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ref) DO UPDATE SET
                    metadata_json = excluded.metadata_json
                """,
                (ref, sha256, int(size_bytes), mime_type, encoding, scope,
                 redaction_version, encoded, created_at or _utc_now()),
            )
            connection.commit()
        except IdempotencyConflictError:
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as error:
            if connection.in_transaction:
                connection.rollback()
            self._raise_sqlite(error, operation="artifact metadata")

    def read_artifact_metadata(self, ref: str) -> dict[str, Any] | None:
        self._ensure_open()
        row = self.connection.execute("SELECT * FROM runtime_artifacts WHERE ref = ?", (ref,)).fetchone()
        if row is None:
            return None
        try:
            raw = row["metadata_json"]
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            metadata = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CorruptionError(f"cannot decode artifact metadata ref={ref}: {error}") from error
        result = {
            "ref": row["ref"],
            "sha256": row["sha256"],
            "size_bytes": int(row["size_bytes"]),
            "mime_type": row["mime_type"],
            "encoding": row["encoding"],
            "scope": row["scope"],
            "redaction_version": row["redaction_version"],
            "created_at": row["created_at"],
            "metadata": metadata,
        }
        return result

    def list_artifact_metadata(self) -> list[dict[str, Any]]:
        self._ensure_open()
        rows = self.connection.execute("SELECT ref FROM runtime_artifacts ORDER BY created_at, ref").fetchall()
        return [item for row in rows if (item := self.read_artifact_metadata(row["ref"])) is not None]

    def write_compaction_checkpoint(self, checkpoint: Mapping[str, Any] | Any) -> None:
        """Persist a derived checkpoint keyed by its source high-water/digest."""

        value = checkpoint.to_dict() if hasattr(checkpoint, "to_dict") else dict(checkpoint)
        try:
            checkpoint_id = str(value["checkpoint_id"])
            source_high_water = int(value["source_high_water"])
            source_digest = str(value["source_digest"])
            canonical_schema_version = int(value["canonical_schema_version"])
            compaction_version = str(value["compaction_version"])
            projection_version = str(value["projection_version"])
            coverage = dict(value["coverage"])
        except (KeyError, TypeError, ValueError) as error:
            raise StoreValidationError(f"invalid compaction checkpoint: {error}") from error
        if source_high_water < 0 or not checkpoint_id.strip() or not source_digest.strip():
            raise StoreValidationError("invalid compaction checkpoint identity")
        encoded = canonical_json_bytes(value)
        coverage_encoded = canonical_json_bytes(coverage)
        self._ensure_open()
        self._fault("store.compaction_checkpoint")
        connection = self.connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO runtime_compaction_checkpoints(
                    checkpoint_id, source_high_water, source_digest,
                    canonical_schema_version, compaction_version,
                    projection_version, coverage_json, checkpoint_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(checkpoint_id) DO UPDATE SET
                    checkpoint_json = excluded.checkpoint_json,
                    coverage_json = excluded.coverage_json
                """,
                (checkpoint_id, source_high_water, source_digest,
                 canonical_schema_version, compaction_version,
                 projection_version, coverage_encoded, encoded,
                 str(value.get("created_at") or _utc_now())),
            )
            connection.commit()
        except sqlite3.Error as error:
            if connection.in_transaction:
                connection.rollback()
            self._raise_sqlite(error, operation="compaction checkpoint")

    def append_compaction_transition(
        self,
        checkpoint: Mapping[str, Any] | Any,
        event: RuntimeEvent | Mapping[str, Any],
    ) -> AppendResult:
        """Atomically persist a checkpoint and its canonical activation event."""

        value = checkpoint.to_dict() if hasattr(checkpoint, "to_dict") else dict(checkpoint)
        try:
            checkpoint_id = str(value["checkpoint_id"])
            source_high_water = int(value["source_high_water"])
            source_digest = str(value["source_digest"])
            canonical_schema_version = int(value["canonical_schema_version"])
            compaction_version = str(value["compaction_version"])
            projection_version = str(value["projection_version"])
            coverage = dict(value["coverage"])
            context_id = value.get("context_id")
            if context_id is not None:
                context_id = str(context_id)
            canonical = event if isinstance(event, RuntimeEvent) else RuntimeEvent.from_dict(event)
            canonical.validate()
        except (KeyError, TypeError, ValueError, RuntimeEventError) as error:
            raise StoreValidationError(
                f"invalid compaction transition: {error}"
            ) from error
        if source_high_water < 0 or not checkpoint_id.strip() or not source_digest.strip():
            raise StoreValidationError("invalid compaction transition identity")
        if coverage.get("to_ordinal") != source_high_water:
            raise StoreValidationError("compaction coverage does not reach source high-water")
        if canonical_schema_version != RUNTIME_EVENT_SCHEMA_VERSION:
            raise StoreValidationError("compaction canonical schema version is unsupported")
        if context_id is not None and canonical.context_id != context_id:
            raise StoreValidationError("checkpoint and event context identities differ")
        transition = (canonical.actions or {}).get("context_transition")
        if not isinstance(transition, Mapping):
            raise StoreValidationError("activation event lacks context_transition action")
        try:
            parsed_transition = ContextTransition.from_value(transition)
        except ContextTransitionError as error:
            raise StoreValidationError(
                f"invalid activation context_transition: {error}"
            ) from error
        try:
            transition_high_water = int(transition.get("source_high_water", -1))
        except (TypeError, ValueError) as error:
            raise StoreValidationError(
                "activation transition source high-water is invalid"
            ) from error
        if (
            transition_high_water != source_high_water
            or str(transition.get("source_digest", "")) != source_digest
            or str(transition.get("projection_version", "")) != projection_version
        ):
            raise StoreValidationError("checkpoint and transition source metadata differ")
        if context_id is not None and parsed_transition.context_id not in {
            None, context_id
        }:
            raise StoreValidationError("checkpoint and transition context identities differ")
        compaction = (canonical.actions or {}).get("compaction")
        if isinstance(compaction, Mapping) and str(compaction.get("checkpoint_id")) != checkpoint_id:
            raise StoreValidationError("compaction action references a different checkpoint")
        encoded = canonical_json_bytes(value)
        coverage_encoded = canonical_json_bytes(coverage)
        self._ensure_open()
        self._fault("store.compaction_checkpoint")
        self._fault("store.append")
        self._fault("store.corrupt_read")
        connection = self.connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._existing_result(canonical)
            if existing is not None:
                connection.rollback()
                return existing
            current_high_water = int(
                connection.execute(
                    "SELECT COALESCE(MAX(ordinal), 0) FROM runtime_events"
                ).fetchone()[0]
            )
            if current_high_water != source_high_water:
                raise StoreValidationError(
                    "compaction source changed before activation"
                )
            prefix = self.read_event_records(
                high_water=source_high_water,
                context_id=context_id,
            )
            if _events_digest([event for _, event in prefix]) != source_digest:
                raise StoreValidationError(
                    "compaction source digest does not match canonical prefix"
                )
            connection.execute(
                """
                INSERT INTO runtime_compaction_checkpoints(
                    checkpoint_id, source_high_water, source_digest,
                    canonical_schema_version, compaction_version,
                    projection_version, coverage_json, checkpoint_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(checkpoint_id) DO UPDATE SET
                    checkpoint_json = excluded.checkpoint_json,
                    coverage_json = excluded.coverage_json
                """,
                (
                    checkpoint_id, source_high_water, source_digest,
                    canonical_schema_version, compaction_version,
                    projection_version, coverage_encoded, encoded,
                    str(value.get("created_at") or _utc_now()),
                ),
            )
            result = self._append_in_transaction(canonical, connection)
            self._fault("store.commit")
            connection.commit()
            return result
        except (SealedRunError, IdempotencyConflictError, StoreFaultError, RuntimeStoreError):
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.IntegrityError as error:
            if connection.in_transaction:
                connection.rollback()
            raise StoreIOError(
                f"SQLite compaction transition constraint failed: {error}"
            ) from error
        except sqlite3.OperationalError as error:
            if connection.in_transaction:
                connection.rollback()
            if "commit" in str(error).lower():
                raise StoreCommitError(str(error)) from error
            self._raise_sqlite(error, operation="compaction transition")
        except Exception as error:
            if connection.in_transaction:
                connection.rollback()
            raise StoreIOError(
                f"SQLite compaction transition failed: {error}"
            ) from error

    def append_context_transition(
        self,
        event: RuntimeEvent | Mapping[str, Any],
        *,
        source_high_water: int,
        source_digest: str,
        context_id: str | None = None,
    ) -> AppendResult:
        """Compare-and-append a lightweight context transition atomically."""

        try:
            canonical = event if isinstance(event, RuntimeEvent) else RuntimeEvent.from_dict(event)
            canonical.validate()
        except (RuntimeEventError, TypeError, ValueError) as error:
            raise StoreValidationError(f"invalid context transition: {error}") from error
        if source_high_water < 0 or not isinstance(source_digest, str) or not source_digest.strip():
            raise StoreValidationError("invalid context transition source identity")
        transition = (canonical.actions or {}).get("context_transition")
        if not isinstance(transition, Mapping):
            raise StoreValidationError("context transition action is required")
        try:
            parsed_transition = ContextTransition.from_value(transition)
        except ContextTransitionError as error:
            raise StoreValidationError(f"invalid context transition: {error}") from error
        try:
            transition_high_water = int(transition.get("source_high_water", -1))
        except (TypeError, ValueError) as error:
            raise StoreValidationError("context transition source high-water is invalid") from error
        if (
            transition_high_water != source_high_water
            or str(transition.get("source_digest", "")) != source_digest
        ):
            raise StoreValidationError("transition and source metadata differ")
        transition_context_id = transition.get("context_id")
        if context_id is not None and canonical.context_id != context_id:
            raise StoreValidationError("transition event context identity differs")
        context_id = context_id or canonical.context_id
        if transition_context_id is not None and transition_context_id != context_id:
            raise StoreValidationError("transition context identity differs")
        if parsed_transition.context_id is not None and parsed_transition.context_id != context_id:
            raise StoreValidationError("transition context identity differs")

        self._ensure_open()
        self._fault("store.append")
        self._fault("store.corrupt_read")
        connection = self.connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._existing_result(canonical)
            if existing is not None:
                connection.rollback()
                return existing
            current_high_water = int(
                connection.execute(
                    "SELECT COALESCE(MAX(ordinal), 0) FROM runtime_events"
                ).fetchone()[0]
            )
            if current_high_water != source_high_water:
                raise StoreValidationError("context transition source changed before activation")
            prefix = self.read_event_records(
                high_water=source_high_water,
                context_id=context_id,
            )
            if _events_digest([item for _, item in prefix]) != source_digest:
                raise StoreValidationError(
                    "context transition source digest does not match canonical prefix"
                )
            result = self._append_in_transaction(canonical, connection)
            self._fault("store.commit")
            connection.commit()
            return result
        except (SealedRunError, IdempotencyConflictError, StoreFaultError, RuntimeStoreError):
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.IntegrityError as error:
            if connection.in_transaction:
                connection.rollback()
            raise StoreIOError(f"SQLite context transition constraint failed: {error}") from error
        except sqlite3.OperationalError as error:
            if connection.in_transaction:
                connection.rollback()
            if "commit" in str(error).lower():
                raise StoreCommitError(str(error)) from error
            self._raise_sqlite(error, operation="context transition")
        except Exception as error:
            if connection.in_transaction:
                connection.rollback()
            raise StoreIOError(f"SQLite context transition failed: {error}") from error

    def read_compaction_checkpoint(self, checkpoint_id: str) -> dict[str, Any] | None:
        self._ensure_open()
        row = self.connection.execute(
            "SELECT checkpoint_json FROM runtime_compaction_checkpoints WHERE checkpoint_id = ?",
            (checkpoint_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            raw = row["checkpoint_json"]
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            return json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CorruptionError(f"cannot decode compaction checkpoint {checkpoint_id}: {error}") from error

    def list_compaction_checkpoints(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT checkpoint_id FROM runtime_compaction_checkpoints ORDER BY created_at, checkpoint_id"
        ).fetchall()
        return [item for row in rows if (item := self.read_compaction_checkpoint(row["checkpoint_id"])) is not None]

    def flush(self) -> None:
        if self._closed:
            return
        try:
            self.connection.commit()
        except sqlite3.Error as error:
            self._raise_sqlite(error, operation="flush")

    def close(self) -> None:
        if self._closed:
            return
        try:
            if self._connection is not None:
                self._connection.commit()
                self._connection.close()
        except sqlite3.Error as error:
            self._raise_sqlite(error, operation="close")
        finally:
            self._connection = None
            self._closed = True

    def __enter__(self) -> "SQLiteRuntimeStore":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()


def _events_digest(events: list[RuntimeEvent]) -> str:
    import hashlib

    encoded = b"[" + b",".join(event.canonical_bytes() for event in events) + b"]"
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "AppendResult",
    "CorruptionError",
    "IdempotencyConflictError",
    "ImmutableEventPrefix",
    "LockedStoreError",
    "PartialSnapshot",
    "RunState",
    "SCHEMA_VERSION",
    "SQLiteRuntimeStore",
    "SchemaVersionError",
    "SealedRunError",
    "StoreClosedError",
    "StoreCommitError",
    "StoreFaultError",
    "StoreIOError",
    "StoreValidationError",
    "ToolOperationRecord",
    "RuntimeStoreError",
]
