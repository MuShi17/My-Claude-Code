"""Session 管理 — JSON 文件持久化会话历史记录。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SESSION_DIR = Path.home() / ".mini-claude" / "sessions"


def _ensure_dir() -> None:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)


def _session_dir(session_id: str) -> Path:
    return SESSION_DIR / session_id


def _session_path(session_id: str) -> Path:
    return _session_dir(session_id) / "session.json"


def _traces_dir(session_id: str) -> Path:
    return _session_dir(session_id) / "traces"


def _legacy_session_path(session_id: str) -> Path:
    """旧格式路径：~/.mini-claude/sessions/{session_id}.json（向后兼容）"""
    return SESSION_DIR / f"{session_id}.json"


def save_session(session_id: str, data: dict[str, Any]) -> None:
    _ensure_dir()
    sdir = _session_dir(session_id)
    sdir.mkdir(parents=True, exist_ok=True)
    _session_path(session_id).write_text(
        json.dumps(data, indent=2, default=str), encoding="utf-8"
    )


def load_session(session_id: str) -> dict[str, Any] | None:
    # 优先新格式
    path = _session_path(session_id)
    if not path.exists():
        # 回退旧格式
        path = _legacy_session_path(session_id)
        if not path.exists():
            return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_sessions() -> list[dict[str, Any]]:
    _ensure_dir()
    results = []
    seen: set[str] = set()

    # 新格式：遍历子目录
    for d in SESSION_DIR.iterdir():
        if not d.is_dir():
            continue
        sid = d.name
        sf = d / "session.json"
        if sf.is_file():
            try:
                data = json.loads(sf.read_text(encoding="utf-8"))
                if "metadata" in data:
                    results.append(data["metadata"])
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
                results.append(data["metadata"])
        except Exception:
            pass

    return results


def save_trace(session_id: str, ask_index: int, lines: list[str]) -> None:
    """将 JSONL 行列表写入 trace 文件。"""
    _ensure_dir()
    td = _traces_dir(session_id)
    td.mkdir(parents=True, exist_ok=True)
    filepath = td / f"{ask_index:03d}.jsonl"
    filepath.write_text("\n".join(lines) + "\n", encoding="utf-8")


def get_latest_session_id() -> str | None:
    sessions = list_sessions()
    if not sessions:
        return None
    sessions.sort(key=lambda s: s.get("startTime", ""), reverse=True)
    return sessions[0].get("id")
