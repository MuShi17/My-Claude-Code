"""观测追踪模块 — 收集每次 ask 的性能指标并写入 JSONL trace 文件。"""

from __future__ import annotations

import time
import json


class SessionTracer:
    """订阅 Agent 事件，累积单次 ask 的指标，结束时写出 JSONL。"""

    def __init__(self, ask_index: int, user_message: str):
        self.ask_index = ask_index
        self.user_message = user_message
        self._chat_start = time.time()
        self._turns: list[dict] = []
        self._current_turn: dict = {}
        self._turn_start: float = 0.0

    # ── 事件处理方法 ───────────────────────────────────

    def on_turn_start(self, payload: dict) -> None:
        self._turn_start = time.time()
        self._current_turn = {
            "turn_index": payload["turn_index"],
            "first_token_ms": 0,
            "tool_calls": [],
        }

    def on_first_token(self, payload: dict) -> None:
        if self._current_turn.get("first_token_ms", 0) == 0 and self._turn_start:
            self._current_turn["first_token_ms"] = int(
                (time.time() - self._turn_start) * 1000
            )

    def on_turn_end(self, payload: dict) -> None:
        self._current_turn["input_tokens"] = payload.get("input_tokens", 0)
        self._current_turn["output_tokens"] = payload.get("output_tokens", 0)
        self._current_turn["cache_read_tokens"] = payload.get("cache_read_tokens", 0)
        self._current_turn["cache_create_tokens"] = payload.get("cache_create_tokens", 0)
        self._current_turn["finish_reason"] = payload.get("finish_reason", "stop")
        self._current_turn["total_duration_ms"] = int(
            (time.time() - self._turn_start) * 1000
        )
        self._turns.append(self._current_turn)
        self._current_turn = {}

    def on_tool_start(self, payload: dict) -> None:
        pass  # 仅标记，实际数据在 on_tool_end 中累积

    def on_tool_end(self, payload: dict) -> None:
        self._current_turn.setdefault("tool_calls", []).append({
            "name": payload["tool_name"],
            "input": payload.get("tool_input", {}),
            "duration_ms": payload.get("duration_ms", 0),
            "result_length": payload.get("result_length", 0),
        })

    def on_compaction(self, payload: dict) -> None:
        self._current_turn["compaction_triggered"] = True

    def on_permission(self, payload: dict) -> None:
        pass  # 未来可扩展

    # ── 序列化 ────────────────────────────────────────

    def finalize(self) -> list[str]:
        """返回完整 JSONL 行列表（ask 概览行 + 所有 turn 行）。"""
        total_duration_ms = int((time.time() - self._chat_start) * 1000)
        total_turns = len(self._turns)
        total_input = sum(t.get("input_tokens", 0) for t in self._turns)
        total_output = sum(t.get("output_tokens", 0) for t in self._turns)
        total_tool_calls = sum(len(t.get("tool_calls", [])) for t in self._turns)

        ask_line = json.dumps({
            "type": "ask",
            "ask_index": self.ask_index,
            "message": self.user_message,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._chat_start)),
            "total_turns": total_turns,
            "total_duration_ms": total_duration_ms,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_tool_calls": total_tool_calls,
        }, ensure_ascii=False)

        turn_lines = []
        for t in self._turns:
            turn_lines.append(json.dumps({"type": "turn", **t}, ensure_ascii=False))

        return [ask_line] + turn_lines
