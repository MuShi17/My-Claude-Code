"""Session 管理 — JSON 文件持久化会话历史记录。"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

SESSION_DIR = Path.home() / ".mini-claude" / "sessions"
SESSION_SNAPSHOT_VERSION = 2


class CanonicalRecoveryError(RuntimeError):
    """A canonical session exists but cannot be safely opened or projected."""

    code = "canonical_recovery_error"
    classification = "corrupt"

    def __init__(self, message: str, *, path: Path | None = None) -> None:
        self.path = path
        suffix = f" ({path})" if path is not None else ""
        super().__init__(message + suffix)


def _ensure_dir() -> None:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)


def _session_dir(session_id: str) -> Path:
    return SESSION_DIR / session_id


def runtime_store_path(session_id: str) -> Path:
    """Return the canonical store path isolated to one session directory."""

    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session_id must be a non-empty string")
    if Path(session_id).name != session_id or session_id in {".", ".."}:
        raise ValueError("session_id must be a single safe path component")
    return _session_dir(session_id) / "runtime.sqlite"


def list_runtime_store_paths() -> list[Path]:
    """List canonical stores without opening or mutating them.

    The root-level database is retained as a read-only discovery fallback for
    stores created before session isolation was introduced.  New writes always
    use :func:`runtime_store_path`.
    """

    paths = [
        path for path in SESSION_DIR.glob("*/runtime.sqlite") if path.is_file()
    ] if SESSION_DIR.exists() else []
    legacy_path = SESSION_DIR.parent / "runtime.sqlite"
    if legacy_path.is_file():
        paths.append(legacy_path)
    return sorted(
        paths,
        key=lambda path: (path.stat().st_mtime_ns, str(path)),
        reverse=True,
    )


def _session_path(session_id: str) -> Path:
    return _session_dir(session_id) / "session.json"


def _session_v2_path(session_id: str) -> Path:
    return _session_dir(session_id) / "session.v2.json"


def _traces_dir(session_id: str) -> Path:
    return _session_dir(session_id) / "traces"


def _legacy_session_path(session_id: str) -> Path:
    """旧格式路径：~/.mini-claude/sessions/{session_id}.json（向后兼容）"""
    return SESSION_DIR / f"{session_id}.json"


def save_session(session_id: str, data: dict[str, Any]) -> None:
    _ensure_dir()
    sdir = _session_dir(session_id)
    sdir.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(_session_path(session_id), data)


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write a derived snapshot without damaging the prior snapshot."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    encoded = json.dumps(data, indent=2, ensure_ascii=False, default=str).encode("utf-8")
    try:
        with open(temporary, "xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def build_session_v2(
    session_id: str,
    runtime_store: Any,
    *,
    high_water: int | None = None,
) -> dict[str, Any] | None:
    """Build a canonical-derived v2 snapshot from one immutable prefix."""

    from .projections.session_projection import SessionProjection

    projection = SessionProjection().build(
        runtime_store,
        session_id=session_id,
        high_water=high_water,
    )
    if projection.high_water == 0 and not projection.messages and not projection.runs:
        return None
    records = runtime_store.read_event_records(session_id=session_id, high_water=projection.high_water)
    ordinals = [ordinal for ordinal, _ in records]
    turns = sorted({event.turn_id for _, event in records})
    return {
        "schemaVersion": SESSION_SNAPSHOT_VERSION,
        "metadata": {
            "id": session_id,
            "source": "canonical",
            "snapshotVersion": SESSION_SNAPSHOT_VERSION,
            "projectionVersion": projection.projection_version,
            "highWater": projection.high_water,
            "sourceDigest": projection.source_digest,
            "projectionDigest": projection.digest,
            "messageCount": len(projection.messages),
        },
        "coverage": {
            "sessionId": session_id,
            "fromOrdinal": min(ordinals) if ordinals else 0,
            "toOrdinal": projection.high_water,
            "turnIds": turns,
        },
        "canonicalMessages": list(projection.messages),
        "runs": list(projection.runs),
        "errors": list(projection.errors),
        "terminals": list(projection.terminals),
        "partialCount": projection.partial_count,
        "diagnostics": [item.to_dict() for item in projection.diagnostics],
    }


def save_session_v2(
    session_id: str,
    runtime_store: Any,
    *,
    high_water: int | None = None,
) -> dict[str, Any] | None:
    snapshot = build_session_v2(session_id, runtime_store, high_water=high_water)
    if snapshot is not None:
        _ensure_dir()
        _atomic_write_json(_session_v2_path(session_id), snapshot)
    return snapshot


def load_canonical_session(session_id: str, runtime_store: Any) -> dict[str, Any] | None:
    """Read a session projection from canonical events without touching JSONL."""

    try:
        from .projections.session_projection import SessionProjection

        return build_session_v2(session_id, runtime_store)
    except Exception as error:
        # Canonical-first callers must distinguish a genuinely empty store from
        # a damaged canonical source.  Legacy fallback is decided by the CLI,
        # never by this reader.
        raise CanonicalRecoveryError(
            f"canonical session projection failed: {error}",
            path=getattr(runtime_store, "database", None),
        ) from error


def load_session(
    session_id: str,
    *,
    runtime_store: Any | None = None,
    canonical_first: bool = False,
) -> dict[str, Any] | None:
    if canonical_first and runtime_store is not None:
        canonical = load_canonical_session(session_id, runtime_store)
        if canonical is not None:
            return canonical
        return None
    # Prefer the canonical-derived v2 cache, then the old snapshot.
    path = _session_v2_path(session_id)
    if not path.exists():
        path = _session_path(session_id)
    if not path.exists():
        # 回退旧格式
        path = _legacy_session_path(session_id)
        if not path.exists():
            return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if path.name != "session.v2.json":
            metadata = dict(data.get("metadata") or {})
            metadata.setdefault("source", "legacy-readonly")
            metadata.setdefault("readonly", True)
            data["metadata"] = metadata
            data.setdefault("source", "legacy-readonly")
            data.setdefault("readonly", True)
        return data
    except Exception:
        return None


def list_sessions(runtime_store: Any | None = None) -> list[dict[str, Any]]:
    if runtime_store is not None:
        canonical = list_canonical_sessions(runtime_store)
        if canonical:
            return canonical
    if not SESSION_DIR.exists():
        return []
    results = []
    seen: set[str] = set()

    # 新格式：遍历子目录
    for d in SESSION_DIR.iterdir():
        if not d.is_dir():
            continue
        sid = d.name
        sf = d / "session.v2.json"
        if not sf.is_file():
            sf = d / "session.json"
        if sf.is_file():
            try:
                data = json.loads(sf.read_text(encoding="utf-8"))
                if "metadata" in data:
                    metadata = dict(data["metadata"])
                    if sf.name != "session.v2.json":
                        metadata.setdefault("source", "legacy-readonly")
                        metadata.setdefault("readonly", True)
                    results.append(metadata)
                    seen.add(sid)
            except Exception:
                pass

    # 旧格式兼容：扁平 .json 文件
    for f in SESSION_DIR.glob("*.json"):
        sid = f.stem
        if sid in seen:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if "metadata" in data:
                metadata = dict(data["metadata"])
                metadata.setdefault("source", "legacy-readonly")
                metadata.setdefault("readonly", True)
                results.append(metadata)
        except Exception:
            pass

    return results


def list_canonical_sessions(runtime_store: Any) -> list[dict[str, Any]]:
    """List sessions represented by canonical events, with no legacy reads."""

    result: list[dict[str, Any]] = []
    for session_id in getattr(runtime_store, "list_session_ids", lambda: [])():
        snapshot = load_canonical_session(session_id, runtime_store)
        if snapshot is not None:
            result.append(dict(snapshot.get("metadata") or {}))
    return result


def list_canonical_runtime_sessions() -> list[dict[str, Any]]:
    """Inspect all session stores for CLI list/latest without mutating them."""

    from .runtime_store import SQLiteRuntimeStore

    result: list[dict[str, Any]] = []
    for database in list_runtime_store_paths():
        store: Any | None = None
        try:
            store = SQLiteRuntimeStore(database)
            result.extend(list_canonical_sessions(store))
        except Exception as error:
            raise CanonicalRecoveryError(
                f"canonical session listing failed: {error}", path=database
            ) from error
        finally:
            if store is not None:
                store.close()
    return result


def save_trace(session_id: str, ask_index: int, lines: list[str]) -> None:
    """将 JSONL 行列表写入 trace 文件。"""
    _ensure_dir()
    td = _traces_dir(session_id)
    td.mkdir(parents=True, exist_ok=True)
    filepath = td / f"{ask_index:03d}.jsonl"
    filepath.write_text("\n".join(lines) + "\n", encoding="utf-8")


def get_latest_session_id(runtime_store: Any | None = None) -> str | None:
    sessions = list_canonical_sessions(runtime_store) if runtime_store is not None else list_sessions()
    if not sessions:
        return None
    sessions.sort(
        key=lambda s: (int(s.get("highWater", 0) or 0), s.get("startTime", ""), s.get("id", "")),
        reverse=True,
    )
    return sessions[0].get("id")


__all__ = [
    "SESSION_DIR",
    "SESSION_SNAPSHOT_VERSION",
    "CanonicalRecoveryError",
    "build_session_v2",
    "get_latest_session_id",
    "list_canonical_sessions",
    "list_canonical_runtime_sessions",
    "list_sessions",
    "list_runtime_store_paths",
    "load_canonical_session",
    "load_session",
    "save_session",
    "save_session_v2",
    "save_trace",
    "runtime_store_path",
]
