"""Single-writer SQLite durability for canonical RuntimeEvent facts."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .event_sink import EventSink, EventSinkError
from .redaction import RedactionPolicy, bound_payload, redact_event_dict
from .runtime_event import RuntimeEvent, RuntimeEventError, canonical_json_bytes

SCHEMA_VERSION = 2


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
class AppendResult:
    event: RuntimeEvent
    ordinal: int
    digest: str
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
                    schema_version INTEGER NOT NULL,
                    session_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    invocation_id TEXT NOT NULL,
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
                    high_water INTEGER NOT NULL DEFAULT 0
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
                """
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
        if event.id != row["event_id"] or event.digest() != row["digest"]:
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
        return AppendResult(stored, int(row["ordinal"]), row["digest"], idempotent=True)

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
            existing = self._existing_result(canonical)
            if existing is not None:
                connection.rollback()
                return existing
            state = connection.execute(
                "SELECT sealed, terminal_event_id, high_water FROM runtime_run_state WHERE run_id = ?",
                (canonical.run_id,),
            ).fetchone()
            if state is not None and bool(state["sealed"]):
                connection.rollback()
                raise SealedRunError(
                    f"run {canonical.run_id} is sealed by {state['terminal_event_id']}"
                )
            ordinal = int(
                connection.execute("SELECT COALESCE(MAX(ordinal), 0) + 1 FROM runtime_events").fetchone()[0]
            )
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO runtime_events(
                    event_id, ordinal, schema_version, session_id, turn_id, run_id,
                    invocation_id, parent_run_id, ts, partial, terminal, digest,
                    event_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    canonical.id,
                    ordinal,
                    canonical.schema_version,
                    canonical.session_id,
                    canonical.turn_id,
                    canonical.run_id,
                    canonical.invocation_id,
                    canonical.parent_run_id,
                    canonical.ts,
                    int(canonical.partial),
                    int(canonical.is_terminal),
                    digest,
                    canonical.canonical_bytes(),
                    now,
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
                        sealed, terminal_event_id, terminal_ordinal, high_water
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        canonical.run_id,
                        canonical.session_id,
                        canonical.invocation_id,
                        canonical.parent_run_id,
                        canonical.status or "open",
                        int(canonical.is_terminal),
                        canonical.id if canonical.is_terminal else None,
                        ordinal if canonical.is_terminal else None,
                        ordinal,
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
                        high_water = ?
                    WHERE run_id = ?
                    """,
                    (
                        int(canonical.is_terminal),
                        canonical.status or "open",
                        int(canonical.is_terminal),
                        int(canonical.is_terminal),
                        canonical.id,
                        int(canonical.is_terminal),
                        ordinal,
                        ordinal,
                        canonical.run_id,
                    ),
                )
            self._fault("store.commit")
            connection.commit()
            return AppendResult(canonical, ordinal, digest, idempotent=False)
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

    emit = append

    def read_events(
        self,
        *,
        session_id: str | None = None,
        turn_id: str | None = None,
        run_id: str | None = None,
        invocation_id: str | None = None,
        upto_ordinal: int | None = None,
        high_water: int | None = None,
    ) -> list[RuntimeEvent]:
        try:
            return [event for _, event in self.read_event_records(
                session_id=session_id,
                turn_id=turn_id,
                run_id=run_id,
                invocation_id=invocation_id,
                upto_ordinal=upto_ordinal,
                high_water=high_water,
            )]
        except CorruptionError:
            raise
        except sqlite3.Error as error:
            self._raise_sqlite(error, operation="read")
            raise AssertionError("unreachable")

    def read_event_records(
        self,
        *,
        session_id: str | None = None,
        turn_id: str | None = None,
        run_id: str | None = None,
        invocation_id: str | None = None,
        upto_ordinal: int | None = None,
        high_water: int | None = None,
    ) -> list[tuple[int, RuntimeEvent]]:
        """Read ``(store ordinal, event)`` pairs without changing the store."""

        connection = self._ensure_open()
        self._fault("store.corrupt_read")
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
        for bound in (upto_ordinal, high_water):
            if bound is not None:
                clauses.append("ordinal <= ?")
                parameters.append(bound)
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
    "RuntimeStoreError",
]
