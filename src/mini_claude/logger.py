"""Agent 运行时日志 — 实时 JSONL 写入，crash-safe。"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

SESSION_DIR = Path.home() / ".mini-claude" / "sessions"


class AgentLogger:
    """实时日志记录器。每次 write 后立即 flush，崩溃不丢数据。"""

    def __init__(
        self,
        session_id: str,
        agent_id: str = "main",
        parent_logger: AgentLogger | None = None,
    ):
        self.session_id = session_id
        self.agent_id = agent_id
        self._parent = parent_logger  # sub-agent 复用父 logger 的 session
        self._ask_index = 0
        self._log_file: Any = None
        self._llm_file: Any = None

    @property
    def _session_dir(self) -> Path:
        sid = self._parent.session_id if self._parent else self.session_id
        return SESSION_DIR / sid

    def new_ask(self, ask_index: int) -> None:
        """开始新一轮 ask，切换到新的主日志文件。"""
        self._ask_index = ask_index
        # 关闭旧文件
        if self._log_file:
            self._log_file.close()
        log_dir = self._session_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self._log_file = open(log_dir / f"{ask_index:03d}.jsonl", "a", encoding="utf-8")

    def _ensure_llm_file(self) -> Any:
        if self._llm_file is None:
            llm_dir = self._session_dir / "llm"
            llm_dir.mkdir(parents=True, exist_ok=True)
            self._llm_file = open(llm_dir / f"{self.session_id}.jsonl", "a", encoding="utf-8")
        return self._llm_file

    def _write_event(self, event: dict) -> None:
        """写入一条主日志事件并立即 flush。"""
        if self._log_file is None:
            return
        event.setdefault("timestamp", time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()))
        event.setdefault("agent_id", self.agent_id)
        self._log_file.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._log_file.flush()

    # ── 事件方法 ─────────────────────────────────────

    def log_api_request(self, request_id: str, model: str) -> None:
        self._write_event({
            "type": "api_request",
            "request_id": request_id,
            "model": model,
        })

    def log_api_response(
        self, request_id: str, latency_ms: int,
        input_tokens: int, output_tokens: int,
        cache_read_tokens: int = 0, finish_reason: str = "stop",
    ) -> None:
        self._write_event({
            "type": "api_response",
            "request_id": request_id,
            "latency_ms": latency_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "finish_reason": finish_reason,
        })

    def log_tool_call(
        self, request_id: str, tool_name: str,
        params: dict, duration_ms: int, success: bool,
    ) -> None:
        self._write_event({
            "type": "tool_call",
            "request_id": request_id,
            "tool_name": tool_name,
            "params": params,
            "duration_ms": duration_ms,
            "success": success,
        })

    def log_sub_agent(
        self, request_id: str, sub_agent_name: str,
        sub_agent_type: str, prompt_summary: str,
    ) -> None:
        self._write_event({
            "type": "sub_agent",
            "request_id": request_id,
            "sub_agent_name": sub_agent_name,
            "sub_agent_type": sub_agent_type,
            "prompt_summary": prompt_summary,
        })

    def log_error(
        self, request_id: str, error_type: str, message: str,
    ) -> None:
        self._write_event({
            "type": "error",
            "request_id": request_id,
            "error_type": error_type,
            "message": message,
        })

    def save_llm_content(
        self, request_id: str, model: str,
        messages: list[dict], response: dict, usage: dict,
    ) -> None:
        """将完整的 LLM 请求/响应写入 llm/ JSONL 文件。"""
        f = self._ensure_llm_file()
        entry = {
            "request_id": request_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            "model": model,
            "messages": messages,
            "response": response,
            "usage": usage,
        }
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        f.flush()

    def close(self) -> None:
        """关闭所有文件句柄。"""
        if self._log_file:
            self._log_file.close()
            self._log_file = None
        if self._llm_file:
            self._llm_file.close()
            self._llm_file = None

    @staticmethod
    def generate_request_id() -> str:
        return f"req_{uuid.uuid4().hex[:12]}"
