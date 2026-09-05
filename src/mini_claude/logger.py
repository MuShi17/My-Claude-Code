"""Legacy Agent runtime logs.

The JSONL layout is intentionally kept backward compatible.  This writer is
the legacy/diagnostic sink for the canonical event migration, so lifecycle
mistakes are reported instead of silently dropping records.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SESSION_DIR = Path.home() / ".mini-claude" / "sessions"


def format_utc_milliseconds(value: datetime) -> str:
    """Serialize a timezone-aware datetime with real UTC milliseconds."""

    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


class AgentLogger:
    """Real-time legacy JSONL writer shared by a parent and its child agents."""

    def __init__(
        self,
        session_id: str,
        agent_id: str = "main",
        parent_logger: AgentLogger | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.session_id = session_id
        self.agent_id = agent_id
        self._parent = parent_logger
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._ask_index = 0
        self._log_file: Any = None
        self._llm_file: Any = None

    @property
    def root_session_id(self) -> str:
        return self._parent.root_session_id if self._parent else self.session_id

    @property
    def _session_dir(self) -> Path:
        return SESSION_DIR / self.root_session_id

    @property
    def session_dir(self) -> Path:
        """Public read-only path for compatible observers such as SessionTracer."""

        return self._session_dir

    @property
    def current_ask_index(self) -> int:
        return self._ask_index

    def _now(self) -> datetime:
        value = self._clock()
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        raise TypeError("logger clock must return datetime or epoch seconds")

    def timestamp(self) -> str:
        return format_utc_milliseconds(self._now())

    def new_ask(self, ask_index: int | None = None) -> None:
        """Start a writable ask; child loggers share the parent's open file."""

        if ask_index is None:
            ask_index = self._parent.current_ask_index if self._parent else self._ask_index + 1
        if ask_index < 1:
            raise ValueError("ask_index must be positive")
        self._ask_index = ask_index

        if self._parent:
            if self._parent._log_file is None:
                raise RuntimeError("parent logger has no active ask")
            return

        self._close_log_file()
        log_dir = self._session_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self._log_file = open(
            log_dir / f"{ask_index:03d}.jsonl", "a", encoding="utf-8"
        )

    def _ensure_llm_file(self) -> Any:
        if self._parent:
            return self._parent._ensure_llm_file()
        if self._llm_file is None:
            llm_dir = self._session_dir / "llm"
            llm_dir.mkdir(parents=True, exist_ok=True)
            self._llm_file = open(
                llm_dir / f"{self.root_session_id}.jsonl", "a", encoding="utf-8"
            )
        return self._llm_file

    def _event_file(self) -> Any:
        if self._parent:
            if self._parent._log_file is None:
                raise RuntimeError("child logger has no active parent ask")
            return self._parent._log_file
        if self._log_file is None:
            raise RuntimeError("logger has no active ask; call new_ask() first")
        return self._log_file

    def _write_event(self, event: dict[str, Any]) -> None:
        """Write one legacy event and flush it immediately."""

        output = dict(event)
        output.setdefault("timestamp", self.timestamp())
        output.setdefault("agent_id", self.agent_id)
        output.setdefault("ask_index", self._ask_index)
        if self._parent:
            output.setdefault("parent_agent_id", self._parent.agent_id)
        file = self._event_file()
        file.write(json.dumps(output, ensure_ascii=False) + "\n")
        file.flush()

    # ── Legacy event methods ────────────────────────────────

    def log_api_request(self, request_id: str, model: str) -> None:
        self._write_event({"type": "api_request", "request_id": request_id, "model": model})

    def log_api_response(
        self,
        request_id: str,
        latency_ms: int,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        finish_reason: str = "stop",
        llm_ref: str | None = None,
        llm_capture_status: str | None = None,
    ) -> None:
        event: dict[str, Any] = {
            "type": "api_response",
            "request_id": request_id,
            "latency_ms": latency_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "finish_reason": finish_reason,
        }
        if llm_ref is not None:
            event["llm_ref"] = llm_ref
        if llm_capture_status is not None:
            event["llm_capture_status"] = llm_capture_status
        self._write_event(event)

    def log_tool_call(
        self,
        request_id: str,
        tool_name: str,
        params: dict,
        duration_ms: int,
        success: bool,
        error_type: str | None = None,
    ) -> None:
        event: dict[str, Any] = {
            "type": "tool_call",
            "request_id": request_id,
            "tool_name": tool_name,
            "params": params,
            "duration_ms": duration_ms,
            "success": success,
        }
        if error_type:
            event["error_type"] = error_type
        self._write_event(event)

    def log_sub_agent(
        self,
        request_id: str,
        sub_agent_name: str,
        sub_agent_type: str,
        prompt_summary: str,
    ) -> None:
        self._write_event(
            {
                "type": "sub_agent",
                "request_id": request_id,
                "sub_agent_name": sub_agent_name,
                "sub_agent_type": sub_agent_type,
                "prompt_summary": prompt_summary,
            }
        )

    def log_error(self, request_id: str, error_type: str, message: str) -> None:
        self._write_event(
            {
                "type": "error",
                "request_id": request_id,
                "error_type": error_type,
                "message": message,
            }
        )

    def log_runtime_event(self, event: dict[str, Any]) -> None:
        """Write a canonical-event compatibility record to legacy JSONL.

        The adapter uses this method for event kinds that have no faithful
        one-to-one legacy method.  Keeping the complete bounded payload and
        canonical identity makes the old reader useful during shadow mode
        without pretending that an unmapped lifecycle fact was a tool call.
        """

        self._write_event(dict(event))

    def save_llm_content(
        self,
        request_id: str,
        model: str,
        messages: list[dict],
        response: dict,
        usage: dict,
    ) -> str:
        """Persist an LLM record and return its resolvable legacy reference."""

        file = self._ensure_llm_file()
        llm_ref = request_id
        entry = {
            "request_id": request_id,
            "llm_ref": llm_ref,
            "timestamp": self.timestamp(),
            "agent_id": self.agent_id,
            "model": model,
            "messages": messages,
            "response": response,
            "usage": usage,
        }
        if self._parent:
            entry["parent_agent_id"] = self._parent.agent_id
        file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        file.flush()
        return llm_ref

    def flush(self) -> None:
        """Flush owned/shared handles; safe to call repeatedly."""

        if self._parent:
            self._parent.flush()
            return
        for file in (self._log_file, self._llm_file):
            if file is not None and not file.closed:
                file.flush()

    def _close_log_file(self) -> None:
        if self._log_file is not None:
            self._log_file.flush()
            self._log_file.close()
            self._log_file = None

    def close(self) -> None:
        """Close owned handles without closing a parent logger's handles."""

        if self._parent:
            return
        self._close_log_file()
        if self._llm_file is not None:
            self._llm_file.flush()
            self._llm_file.close()
            self._llm_file = None

    @staticmethod
    def generate_request_id() -> str:
        return f"req_{uuid.uuid4().hex[:12]}"
