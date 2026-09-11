"""TUI 适配器：把输出端口事件渲染为终端输出。

runtime 只发布结构化事件（`runtime_ports.OutputEvent`）；本模块是这些事件的
**终端消费者**，复用既有 `ui.py` 渲染函数，不重写渲染层。

对应 OpenSpec change ``decouple-runtime-interaction-from-tui`` 的 design D1/D6：
端口由入口显式注入，适配器不改变事件语义。
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from . import ui
from .interactions import (
    InteractionKind,
    InteractionReply,
    InteractionRequest,
)
from .runtime_ports import OutputEvent

__all__ = ["TerminalOutputPort", "TerminalInteractionPort"]


class TerminalOutputPort:
    """把端口事件映射到既有终端渲染函数。"""

    name = "terminal"

    def emit(self, event: OutputEvent) -> None:
        kind = event.kind
        payload: dict[str, Any] = dict(event.payload)

        if kind == "assistant_text":
            ui.print_assistant_text(str(payload.get("text", "")))
        elif kind == "assistant_thinking":
            # 思考展示沿用文本通道（既有行为：思考与文本都走 stdout）。
            ui.print_assistant_text(str(payload.get("text", "")))
        elif kind == "tool_call":
            ui.print_tool_call(str(payload.get("tool", "")), payload.get("input") or {})
        elif kind == "tool_result":
            ui.print_tool_result(str(payload.get("tool", "")), str(payload.get("result", "")))
        elif kind == "tool_denied":
            ui.print_info(f"Denied: {payload.get('message', '')}")
        elif kind == "confirmation":
            ui.print_confirmation(str(payload.get("command", "")))
        elif kind == "divider":
            ui.print_divider()
        elif kind == "budget":
            ui.print_cost(int(payload.get("input_tokens", 0)), int(payload.get("output_tokens", 0)))
        elif kind == "retry":
            ui.print_retry(
                int(payload.get("attempt", 0)),
                int(payload.get("max_retries", 0)),
                str(payload.get("reason", "")),
            )
        elif kind == "info":
            ui.print_info(str(payload.get("message", "")))
        elif kind == "error":
            ui.print_error(str(payload.get("message", "")))
        elif kind == "sub_agent_start":
            ui.print_sub_agent_start(
                str(payload.get("agent_type", "")), str(payload.get("description", ""))
            )
        elif kind == "sub_agent_end":
            ui.print_sub_agent_end(
                str(payload.get("agent_type", "")), str(payload.get("description", ""))
            )
        elif kind == "spinner":
            if payload.get("active"):
                ui.start_spinner(str(payload.get("label", "Thinking")))
            else:
                ui.stop_spinner()
        elif kind == "diagnostic":
            # 诊断走 stderr，避免污染 stdout 上的业务/协议输出。
            message = str(payload.get("message", ""))
            if message:
                ui.print_diagnostic(message)


class TerminalInteractionPort:
    """终端交互适配器：把交互端口实现为终端提示 + 可取消的异步等待。

    - 阻塞式 `input` 一律经 `asyncio.to_thread` 卸载，**不阻塞事件循环**，
      因此等待期间 cancel/查询仍可处理（design D4）。
    - 本类是终端读取的**唯一允许位置**；runtime 模块内不得再出现 `input`。
    - `input_fn` 可注入，便于测试与手工脚本。
    """

    name = "terminal-interaction"

    def __init__(self, input_fn: Any = None, output: Any = None) -> None:
        self._input = input_fn or input
        self._output = output or ui
        # 取消时置位：等待中的读取会在下一个轮询周期返回空串，从而**解除等待**
        # 而不是让调用方永远挂在 pending（spec interactive-requests）。
        self.cancel_event = threading.Event()

    def cancel_pending(self) -> None:
        """请求解除当前等待（由 `Agent.cancel_pending_interactions()` 调用）。"""

        self.cancel_event.set()

    async def request(self, request: InteractionRequest) -> InteractionReply:
        prompt = request.prompt or ""
        if request.kind == InteractionKind.APPROVAL:
            answer = await self._read("  Allow? (y/n): ")
            approved = answer.strip().lower().startswith("y")
            return InteractionReply(
                request_id=request.request_id,
                approved=approved,
                params_digest=request.params_digest,
                source=self.name,
            )

        # 提问：回答只作为运行输入，不构成工具授权。
        answer = await self._read("  Answer: ")
        return InteractionReply(
            request_id=request.request_id,
            answer=answer,
            approved=False,
            params_digest=request.params_digest,
            source=self.name,
        )

    async def _read(self, prompt: str) -> str:
        """在独立线程中读取一行，并让等待可被取消。

        阻塞 `input` 在线程中执行；事件循环侧以短超时轮询 `cancel_event`，
        因此取消请求能在有限时间内解除等待（且不阻塞事件循环）。
        """

        self.cancel_event.clear()
        future = asyncio.get_running_loop().run_in_executor(None, self._blocking_read, prompt)
        while True:
            if self.cancel_event.is_set():
                return ""
            try:
                return await asyncio.wait_for(asyncio.shield(future), timeout=0.05)
            except asyncio.TimeoutError:
                continue
            except (EOFError, KeyboardInterrupt):
                return ""

    def _blocking_read(self, prompt: str) -> str:
        try:
            return str(self._input(prompt))
        except (EOFError, KeyboardInterrupt):
            return ""
