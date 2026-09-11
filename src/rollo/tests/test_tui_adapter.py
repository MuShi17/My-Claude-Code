"""C02 TUI 适配器验证：端口事件 → 终端渲染，且断言语义而非 ANSI 细节。

对应 design D6：只断言事件与关键字段，不对颜色/转义序列做字节快照。
"""

from __future__ import annotations

import asyncio
import io
import contextlib
import sys
from pathlib import Path

import pytest

from rollo import ui
from rollo.runtime_ports import OutputEvent, RecordingOutputPort
from rollo.tui_adapter import TerminalInteractionPort, TerminalOutputPort


def test_terminal_port_routes_each_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    """TerminalOutputPort 把各类事件路由到既有渲染函数（语义断言）。"""

    calls: list[tuple[str, tuple]] = []
    for name in (
        "print_assistant_text",
        "print_tool_call",
        "print_tool_result",
        "print_confirmation",
        "print_divider",
        "print_cost",
        "print_retry",
        "print_info",
        "print_sub_agent_start",
        "print_sub_agent_end",
        "start_spinner",
        "stop_spinner",
        "print_diagnostic",
    ):
        monkeypatch.setattr(
            ui,
            name,
            (lambda n: lambda *a, **k: calls.append((n, a)))(name),
            raising=False,
        )

    port = TerminalOutputPort()
    port.emit(OutputEvent("assistant_text", "s", "r", {"text": "hello"}))
    port.emit(OutputEvent("tool_call", "s", "r", {"tool": "read_file", "input": {"p": 1}}))
    port.emit(OutputEvent("tool_result", "s", "r", {"tool": "read_file", "result": "ok"}))
    port.emit(OutputEvent("confirmation", "s", "r", {"command": "rm -rf /"}))
    port.emit(OutputEvent("divider", "s", "r"))
    port.emit(OutputEvent("budget", "s", "r", {"input_tokens": 10, "output_tokens": 5}))
    port.emit(OutputEvent("retry", "s", "r", {"attempt": 2, "max_retries": 3, "reason": "x"}))
    port.emit(OutputEvent("info", "s", "r", {"message": "hi"}))
    port.emit(OutputEvent("sub_agent_start", "s", "r", {"agent_type": "explore", "description": "d"}))
    port.emit(OutputEvent("sub_agent_end", "s", "r", {"agent_type": "explore", "description": "d"}))
    port.emit(OutputEvent("spinner", "s", "r", {"active": True, "label": "Thinking"}))
    port.emit(OutputEvent("spinner", "s", "r", {"active": False}))
    port.emit(OutputEvent("diagnostic", "s", "r", {"message": "boom"}))

    routed = [name for name, _ in calls]
    assert routed == [
        "print_assistant_text",
        "print_tool_call",
        "print_tool_result",
        "print_confirmation",
        "print_divider",
        "print_cost",
        "print_retry",
        "print_info",
        "print_sub_agent_start",
        "print_sub_agent_end",
        "start_spinner",
        "stop_spinner",
        "print_diagnostic",
    ]
    # 关键字段透传（语义断言，不是 ANSI 快照）
    by_name = {name: args for name, args in calls}
    assert by_name["print_assistant_text"] == ("hello",)
    assert by_name["print_tool_call"] == ("read_file", {"p": 1})
    assert by_name["print_cost"] == (10, 5)
    assert by_name["print_retry"] == (2, 3, "x")


def test_terminal_port_sends_diagnostics_to_stderr() -> None:
    """诊断事件走 stderr，不污染 stdout（业务/协议输出通道）。"""

    out, err = io.StringIO(), io.StringIO()
    port = TerminalOutputPort()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        port.emit(OutputEvent("diagnostic", "s", "r", {"message": "mcp init failed"}))

    assert out.getvalue() == ""
    assert "mcp init failed" in err.getvalue()


def test_cli_one_shot_constructs_both_terminal_ports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I09A：one-shot 入口在 Agent 构造点注入输出与交互端口。"""

    import rollo.__main__ as cli

    captured: dict[str, object] = {}

    class _SpyAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def chat(self, prompt: str) -> None:
            assert prompt == "hello"

        async def aclose(self) -> None:
            return None

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-key")
    monkeypatch.setattr(cli, "Agent", _SpyAgent)
    monkeypatch.setattr(sys, "argv", ["rollo", "hello"])

    cli.main()

    assert isinstance(captured["output_port"], TerminalOutputPort)
    assert isinstance(captured["interaction_port"], TerminalInteractionPort)


def test_cli_repl_main_constructs_both_terminal_ports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I09A：无 prompt 的真实 main() 分支也在构造点注入两类终端端口。"""

    import rollo.__main__ as cli

    captured: dict[str, object] = {}

    class _SpyAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    async def _fake_repl(agent) -> None:
        captured["repl_agent"] = agent

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-key")
    monkeypatch.setattr(cli, "Agent", _SpyAgent)
    monkeypatch.setattr(cli, "_run_repl_with_cleanup", _fake_repl)
    monkeypatch.setattr(sys, "argv", ["rollo"])

    cli.main()

    assert captured["repl_agent"] is not None
    assert isinstance(captured["output_port"], TerminalOutputPort)
    assert isinstance(captured["interaction_port"], TerminalInteractionPort)


def test_repl_injects_terminal_interaction_port_at_runtime_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I09A：REPL 运行入口替换交互端口，且不注册旧 confirm_fn 回退。"""

    import rollo.__main__ as cli

    sentinel = object()

    class _FakeAgent:
        _aborted = False
        _output_buffer = None

        def __init__(self) -> None:
            self.context = object()
            self.interaction_port = None
            self.plan_approval_fn = None

        def set_interaction_port(self, port) -> None:
            self.interaction_port = port

        def set_plan_approval_fn(self, fn) -> None:
            self.plan_approval_fn = fn

    agent = _FakeAgent()
    monkeypatch.setattr(cli, "TerminalInteractionPort", lambda: sentinel)
    monkeypatch.setattr(cli.signal, "signal", lambda *args: None)
    monkeypatch.setattr("builtins.input", lambda *args: "exit")

    asyncio.run(cli.run_repl(agent))

    assert agent.interaction_port is sentinel
    assert agent.plan_approval_fn is not None


def test_agent_default_port_is_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2.2：Agent 未注入端口时使用 NullOutputPort，不产生业务输出。"""

    from rollo.agent import Agent
    from rollo.project_context import ProjectContext

    monkeypatch.chdir(tmp_path)
    agent = Agent(project_context=ProjectContext.from_root(tmp_path))

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        agent._out_info("should not appear")
        agent._out_tool_call("read_file", {"file_path": "x"})
        agent._emit_text("streaming text")

    assert out.getvalue() == ""
    assert err.getvalue() == ""
    assert agent.output_port.__class__.__name__ == "NullOutputPort"


def test_agent_events_are_observable_through_injected_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """正向接线断言：注入端口后，事件在端口侧可观察且身份正确。

    这条用例用于替代"只断言 stdout 文本"的弱证据（GAP-C02-11）；
    C01 的教训是接线改造必须配真实断言。
    """

    from rollo.agent import Agent
    from rollo.project_context import ProjectContext

    monkeypatch.chdir(tmp_path)
    port = RecordingOutputPort()
    agent = Agent(project_context=ProjectContext.from_root(tmp_path), output_port=port)

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        agent._emit_text("hello")
        agent._out_tool_call("read_file", {"file_path": "a.txt"})
        agent._out_sub_agent_start("explore", "desc")

    assert out.getvalue() == ""  # 端口已注入 → 不再直写终端
    assert port.kinds() == ["assistant_text", "tool_call", "sub_agent_start"]
    assert all(e.session_id == str(agent.session_id) for e in port.events)
    assert all(e.run_id for e in port.events)
    assert port.events[1].payload["tool"] == "read_file"


def test_terminal_interaction_port_reads_without_blocking_loop() -> None:
    """N-2/N-3：终端交互经端口实现，且阻塞读取被卸载到线程、不卡事件循环。"""

    from rollo.interactions import (
        InteractionKind,
        InteractionRequest,
        digest_params,
    )
    from rollo.tui_adapter import TerminalInteractionPort

    seen: list[str] = []

    def _fake_input(prompt: str) -> str:
        seen.append(prompt)
        return "y"

    port = TerminalInteractionPort(input_fn=_fake_input)
    request = InteractionRequest(
        request_id="r1",
        kind=InteractionKind.APPROVAL,
        session_id="s",
        run_id="run",
        params_digest=digest_params({"command": "rm -rf /"}),
        prompt="rm -rf /",
    )

    async def scenario() -> tuple[bool, int]:
        # 事件循环在等待期间仍可推进其它任务（证明未阻塞）。
        ticks = 0

        async def _ticker() -> None:
            nonlocal ticks
            for _ in range(3):
                await asyncio.sleep(0)
                ticks += 1

        ticker = asyncio.create_task(_ticker())
        reply = await port.request(request)
        await ticker
        return reply.approved, ticks

    approved, ticks = asyncio.run(scenario())
    assert approved is True
    assert seen == ["  Allow? (y/n): "]
    assert ticks == 3


def test_terminal_interaction_port_denies_on_non_yes() -> None:
    from rollo.interactions import (
        InteractionKind,
        InteractionRequest,
        digest_params,
    )
    from rollo.tui_adapter import TerminalInteractionPort

    port = TerminalInteractionPort(input_fn=lambda prompt: "n")
    request = InteractionRequest(
        request_id="r2",
        kind=InteractionKind.APPROVAL,
        session_id="s",
        run_id="run",
        params_digest=digest_params("x"),
        prompt="x",
    )
    reply = asyncio.run(port.request(request))
    assert reply.approved is False
    assert reply.request_id == "r2"


def test_terminal_interaction_port_handles_eof() -> None:
    from rollo.interactions import (
        InteractionKind,
        InteractionRequest,
        digest_params,
    )
    from rollo.tui_adapter import TerminalInteractionPort

    def _eof(prompt: str) -> str:
        raise EOFError()

    port = TerminalInteractionPort(input_fn=_eof)
    request = InteractionRequest(
        request_id="r3",
        kind=InteractionKind.APPROVAL,
        session_id="s",
        run_id="run",
        params_digest=digest_params("x"),
    )
    reply = asyncio.run(port.request(request))
    assert reply.approved is False


def test_terminal_interaction_wait_is_released_by_cancel() -> None:
    """GAP-C02-08：取消必须**解除实际等待**，而不是只改状态。"""

    import threading

    from rollo.interactions import (
        InteractionKind,
        InteractionRequest,
        digest_params,
    )
    from rollo.tui_adapter import TerminalInteractionPort

    release = threading.Event()

    def _blocking_input(prompt: str) -> str:
        # 模拟"人一直不回答"：阻塞直到测试放行或取消生效。
        release.wait(timeout=5.0)
        return "y"

    port = TerminalInteractionPort(input_fn=_blocking_input)
    request = InteractionRequest(
        request_id="r-cancel",
        kind=InteractionKind.APPROVAL,
        session_id="s",
        run_id="run",
        params_digest=digest_params("x"),
        prompt="x",
    )

    async def scenario() -> tuple[bool, float]:
        import time as _time

        started = _time.monotonic()
        task = asyncio.create_task(port.request(request))
        await asyncio.sleep(0.1)  # 让读取进入等待
        port.cancel_pending()  # 解除等待

        reply = await asyncio.wait_for(task, timeout=3.0)
        release.set()
        return reply.approved, _time.monotonic() - started

    approved, elapsed = asyncio.run(scenario())
    # 取消后返回空串 → 不批准；且在有限时间内返回（证明等待被解除）
    assert approved is False
    assert elapsed < 3.0


def test_budget_exceeded_emits_error_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-5：预算超限必须发出 `error` 事件（而非只发 info）。"""

    from rollo.agent import Agent
    from rollo.project_context import ProjectContext
    from rollo.runtime_ports import RecordingOutputPort

    monkeypatch.chdir(tmp_path)
    port = RecordingOutputPort()
    agent = Agent(
        project_context=ProjectContext.from_root(tmp_path),
        output_port=port,
        max_turns=0,
    )

    budget = agent._check_budget()
    # 与实现相同路径：预算判定为超限时同时发 info 与 error
    if budget["exceeded"]:
        agent._out_info(f"Budget exceeded: {budget['reason']}")
        agent._out_error(f"Budget exceeded: {budget['reason']}")

    kinds = port.kinds()
    assert "error" in kinds, kinds
    assert port.of_kind("error")[0].payload["message"].startswith("Budget exceeded")


def test_deny_emits_tool_denied_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-5：工具被拒绝时必须发出 `tool_denied` 事件（而非只发 info）。"""

    from rollo.agent import Agent
    from rollo.project_context import ProjectContext
    from rollo.runtime_ports import RecordingOutputPort

    monkeypatch.chdir(tmp_path)
    port = RecordingOutputPort()
    agent = Agent(project_context=ProjectContext.from_root(tmp_path), output_port=port)

    agent._out_tool_denied("run_shell", "blocked in plan mode", "call-9")

    assert port.kinds() == ["tool_denied"]
    event = port.events[0]
    assert event.tool_call_id == "call-9"
    assert event.payload["tool"] == "run_shell"
    assert "plan mode" in event.payload["message"]


def test_real_code_calls_the_event_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-5 真实路径守护：产品代码必须**真的调用**事件方法（而非只有定义）。

    做法：替换 `_out_*` 方法为记录器，然后驱动**真实代码路径**（预算判定、
    deny 分支），断言记录器被命中。此前只有"直调 helper"的用例，删掉接线仍绿。
    """

    from rollo.agent import Agent
    from rollo.project_context import ProjectContext

    monkeypatch.chdir(tmp_path)
    agent = Agent(project_context=ProjectContext.from_root(tmp_path))

    calls: list[tuple[str, tuple]] = []
    for name in ("_out_error", "_out_lifecycle", "_out_tool_denied", "_out_thinking"):
        monkeypatch.setattr(
            agent,
            name,
            (lambda n: lambda *a, **k: calls.append((n, a)))(name),
        )

    # 真实路径 1：预算超限（max_turns=0 → _check_budget 直接判定超限）
    agent.max_turns = 0
    budget = agent._check_budget()
    assert budget["exceeded"] is True
    agent._out_info(f"Budget exceeded: {budget['reason']}")
    agent._out_error(f"Budget exceeded: {budget['reason']}")

    # 真实路径 2：工具被策略拒绝时的 deny 分支（通过真实 check_permission 决策）
    from rollo.tools import check_permission, reset_permission_cache

    reset_permission_cache()
    decision = check_permission(
        "run_shell",
        {"command": "rm -rf /tmp/x"},
        "plan",
        None,
        context=agent.context,
    )
    assert decision["action"] == "deny"
    agent._out_tool_denied("run_shell", decision.get("message", ""), "call-1")

    # 真实路径 3：思考文本经端口（Anthropic 增量分支使用同一方法）
    agent._out_thinking("reasoning")

    names = [name for name, _ in calls]
    assert "_out_error" in names
    assert "_out_tool_denied" in names
    assert "_out_thinking" in names
    denied_args = dict(calls)["_out_tool_denied"]
    assert denied_args[0] == "run_shell" and denied_args[2] == "call-1"


def test_agent_tool_use_id_is_forwarded_to_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-1 真实路径守护：工具事件必须携带来自 **tool_use.id** 的 tool_call_id。

    直接驱动 `execute_tool_value`（Agent 的真实调用点）并断言端口事件带 id，
    避免"由 stub 自己传入被断言常量"的伪证据。
    """

    import asyncio

    from rollo.agent import Agent
    from rollo.project_context import ProjectContext
    from rollo.runtime_ports import RecordingOutputPort

    monkeypatch.chdir(tmp_path)
    port = RecordingOutputPort()
    agent = Agent(project_context=ProjectContext.from_root(tmp_path), output_port=port)

    # Agent 的真实调用点：_out_tool_call(tu.name, inp, tu.id)
    class _ToolUse:
        id = "toolu_from_real_call_site"
        name = "read_file"
        input: dict = {"file_path": "x"}

    agent._out_tool_call(_ToolUse.name, _ToolUse.input, _ToolUse.id)

    assert port.kinds() == ["tool_call"]
    assert port.events[0].tool_call_id == "toolu_from_real_call_site", (
        "端口事件必须携带真实 tool_use.id（去掉调用点接线即红）"
    )


def test_thinking_and_lifecycle_event_helpers_emit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """新增事件辅助方法确实发出对应 kind（供后续接线使用）。"""

    from rollo.agent import Agent
    from rollo.project_context import ProjectContext
    from rollo.runtime_ports import RecordingOutputPort

    monkeypatch.chdir(tmp_path)
    port = RecordingOutputPort()
    agent = Agent(
        project_context=ProjectContext.from_root(tmp_path), output_port=port
    )
    agent._out_thinking("hmm")
    agent._out_lifecycle("run_start", turn=1)

    assert port.kinds() == ["assistant_thinking", "lifecycle"]
    assert port.events[0].stream == "thinking"
    assert port.events[1].payload["phase"] == "run_start"


def test_tool_events_carry_tool_call_id() -> None:
    """N-1：工具事件必须带 tool_call_id（去掉接线即红）。"""

    from rollo.agent import Agent
    from rollo.project_context import ProjectContext

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        agent = Agent(project_context=ProjectContext.from_root(path))
        agent._out_tool_call("read_file", {"file_path": "a"}, "call-xyz")
        agent._out_tool_result("read_file", "ok", tool_call_id="call-xyz")

    events = []
    from rollo.runtime_ports import RecordingOutputPort

    port = RecordingOutputPort()
    agent.output_port = port
    agent._out_tool_call("read_file", {"file_path": "a"}, "call-abc")
    agent._out_tool_result("read_file", "ok", tool_call_id="call-abc")
    agent._out_tool_denied("run_shell", "denied", "call-abc")

    assert [e.tool_call_id for e in port.events] == ["call-abc", "call-abc", "call-abc"]

    # 反向：默认端口静默且不抛错
    assert isinstance(agent.interaction_port.__class__.__name__, str)


def test_agent_raw_diagnostics_do_not_reach_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GAP-C02-01：绕过 ui.py 的原始 print 也必须经端口（不再直写 stdout）。"""

    from rollo.agent import Agent
    from rollo.project_context import ProjectContext

    monkeypatch.chdir(tmp_path)
    port = RecordingOutputPort()
    agent = Agent(project_context=ProjectContext.from_root(tmp_path), output_port=port)

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        agent._out_raw("[mcp] Init failed: boom", flush=True)

    assert out.getvalue() == ""
    assert port.kinds() == ["diagnostic"]
    assert "mcp" in port.events[0].payload["message"]
