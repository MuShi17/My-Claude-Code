"""Legacy performance tracing with crash-tolerant JSONL updates."""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .logger import AgentLogger


class SessionTracer:
    """Collect per-ask metrics and keep completed turn details on disk."""

    def __init__(self, ask_index: int, user_message: str, logger: AgentLogger):
        self.ask_index = ask_index
        self.user_message = user_message
        self._chat_start = time.time()
        self._turns: list[dict[str, Any]] = []
        self._current_turn: dict[str, Any] = {}
        self._turn_start: float = 0.0
        self._denied_count = 0
        self._logger = logger
        self._trace_file: Any = None
        self._trace_path = None
        self._summary_written = False

    def on_turn_start(self, payload: dict) -> None:
        self._turn_start = time.time()
        self._current_turn = {
            "turn_index": payload["turn_index"],
            "first_token_ms": 0,
            "tool_calls": [],
        }

    def on_first_token(self, payload: dict) -> None:
        del payload
        if self._current_turn.get("first_token_ms", 0) == 0 and self._turn_start:
            self._current_turn["first_token_ms"] = int(
                (time.time() - self._turn_start) * 1000
            )

    def _ensure_trace_file(self) -> Any:
        if self._trace_file is None:
            traces_dir = self._logger.session_dir / "traces"
            traces_dir.mkdir(parents=True, exist_ok=True)
            self._trace_path = traces_dir / f"{self.ask_index:03d}.jsonl"
            self._trace_file = open(self._trace_path, "a", encoding="utf-8")
        return self._trace_file

    def _write_line(self, value: dict[str, Any]) -> None:
        file = self._ensure_trace_file()
        file.write(json.dumps(value, ensure_ascii=False) + "\n")
        file.flush()

    def on_turn_end(self, payload: dict) -> None:
        current = self._current_turn or {
            "turn_index": payload.get("turn_index", len(self._turns) + 1),
            "first_token_ms": 0,
            "tool_calls": [],
        }
        current.update(
            {
                "input_tokens": payload.get("input_tokens", 0),
                "output_tokens": payload.get("output_tokens", 0),
                "cache_read_tokens": payload.get("cache_read_tokens", 0),
                "cache_create_tokens": payload.get("cache_create_tokens", 0),
                "finish_reason": payload.get("finish_reason", "stop"),
                "total_duration_ms": int(
                    (time.time() - self._turn_start) * 1000
                ),
            }
        )
        for key in ("error", "error_type"):
            if key in payload:
                current[key] = payload[key]
        self._turns.append(current)
        self._write_line({"type": "turn", **current})
        self._current_turn = {}

    def on_tool_start(self, payload: dict) -> None:
        del payload

    def on_tool_end(self, payload: dict) -> None:
        target = self._current_turn or (self._turns[-1] if self._turns else None)
        if target is None:
            return
        tool = {
            "name": payload["tool_name"],
            "input": payload.get("tool_input", {}),
            "duration_ms": payload.get("duration_ms", 0),
            "result_length": payload.get("result_length", 0),
            "success": payload.get("success", True),
        }
        for key in ("tool_call_id", "error", "error_type"):
            if key in payload:
                tool[key] = payload[key]
        target.setdefault("tool_calls", []).append(tool)
        if target in self._turns:
            self._rewrite_turn(target)

    def _rewrite_turn(self, target: dict[str, Any]) -> None:
        """Atomically replace a provisional turn after tool details arrive."""

        if self._trace_path is None or not self._trace_path.exists():
            return
        self._close_trace_file()
        lines = self._trace_path.read_text(encoding="utf-8").splitlines()
        replaced = False
        output: list[str] = []
        for line in lines:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                output.append(line)
                continue
            if (
                not replaced
                and value.get("type") == "turn"
                and value.get("turn_index") == target.get("turn_index")
            ):
                output.append(json.dumps({"type": "turn", **target}, ensure_ascii=False))
                replaced = True
            else:
                output.append(line)
        if not replaced:
            output.append(json.dumps({"type": "turn", **target}, ensure_ascii=False))
        self._atomic_write(output)

    def _atomic_write(self, lines: list[str]) -> None:
        assert self._trace_path is not None
        self._trace_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=f".{self._trace_path.name}.",
            suffix=".tmp",
            dir=self._trace_path.parent,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
                file.write("\n".join(lines))
                if lines:
                    file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self._trace_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def on_tool_deny(self, payload: dict) -> None:
        del payload
        self._denied_count += 1

    def on_compaction(self, payload: dict) -> None:
        del payload
        self._current_turn["compaction_triggered"] = True

    def on_permission(self, payload: dict) -> None:
        del payload

    def _close_trace_file(self) -> None:
        if self._trace_file is not None:
            self._trace_file.flush()
            self._trace_file.close()
            self._trace_file = None

    def flush(self) -> None:
        if self._trace_file is not None:
            self._trace_file.flush()

    def write_ask_summary(self) -> None:
        """Append one idempotent ask summary after all turn/tool updates."""

        if self._summary_written:
            return
        total_duration_ms = int((time.time() - self._chat_start) * 1000)
        total_turns = len(self._turns)
        total_input = sum(t.get("input_tokens", 0) for t in self._turns)
        total_output = sum(t.get("output_tokens", 0) for t in self._turns)
        all_tools = [tc for t in self._turns for tc in t.get("tool_calls", [])]
        total_tool_calls = len(all_tools)
        total_tool_duration_ms = sum(tc.get("duration_ms", 0) for tc in all_tools)
        successful_tools = sum(1 for tc in all_tools if tc.get("success", True))
        tool_success_rate = successful_tools / total_tool_calls if total_tool_calls else 1.0

        self._write_line(
            {
                "type": "ask",
                "ask_index": self.ask_index,
                "message": self.user_message,
                "timestamp": self._logger.timestamp(),
                "total_turns": total_turns,
                "total_duration_ms": total_duration_ms,
                "total_input_tokens": total_input,
                "total_output_tokens": total_output,
                "total_tool_calls": total_tool_calls,
                "total_tool_duration_ms": total_tool_duration_ms,
                "successful_tools": successful_tools,
                "tool_success_rate": round(tool_success_rate, 4),
                "denied_tools": self._denied_count,
            }
        )
        self.flush()
        self._close_trace_file()
        self._summary_written = True
