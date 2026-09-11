"""C02 输出端口验证：无终端可运行、事件身份可关联、端口失败不影响终态。

对应 spec `runtime-output-ports` 的 2 条 requirement / 5 个 Scenario。
本文件的断言针对**语义事件**，不做 ANSI/字节快照（design D6）。
"""

from __future__ import annotations

import asyncio
import io
import contextlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from rollo.runtime_ports import (
    NullOutputPort,
    OutputEvent,
    RecordingOutputPort,
    emit_safely,
)
from rollo.tests.tool_fixtures import FixtureToolbox


# ─── 端口基础语义 ─────────────────────────────────────────────


def test_event_requires_identity() -> None:
    """事件必须带 session_id 与 run_id，否则构造即失败。"""

    with pytest.raises(ValueError):
        OutputEvent(kind="info", session_id="", run_id="run-1")
    with pytest.raises(ValueError):
        OutputEvent(kind="info", session_id="sess-1", run_id="")


def test_recording_port_keeps_order_and_identity() -> None:
    """同一 run 的事件可按身份归组并保持顺序。"""

    port = RecordingOutputPort()
    for idx in range(3):
        emit_safely(
            port,
            OutputEvent(
                kind="tool_call",
                session_id="sess-1",
                run_id="run-1",
                tool_call_id=f"call-{idx}",
                payload={"tool": "read_file"},
            ),
        )

    assert port.kinds() == ["tool_call"] * 3
    assert [e.identity for e in port.events] == [("sess-1", "run-1")] * 3
    assert [e.tool_call_id for e in port.of_kind("tool_call")] == [
        "call-0",
        "call-1",
        "call-2",
    ]


def test_null_port_writes_no_business_output() -> None:
    """headless：默认端口不向 stdout/stderr 写业务输出。"""

    port = NullOutputPort()
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        for kind in ("assistant_text", "tool_result", "budget", "error"):
            emit_safely(port, OutputEvent(kind=kind, session_id="s", run_id="r"))

    assert out.getvalue() == ""
    assert err.getvalue() == ""


def test_port_failure_is_isolated() -> None:
    """端口抛错被降级为诊断，不向调用方冒泡（因此不影响 run 终态）。"""

    class _BoomPort:
        def __init__(self) -> None:
            self.failures: list[BaseException] = []

        def emit(self, event: OutputEvent) -> None:
            raise RuntimeError("renderer failed")

    port = _BoomPort()
    emit_safely(port, OutputEvent(kind="assistant_text", session_id="s", run_id="r"))

    assert len(port.failures) == 1
    assert isinstance(port.failures[0], RuntimeError)


def test_emit_without_port_does_not_touch_terminal() -> None:
    """未注入端口时不隐式回退终端打印。"""

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        emit_safely(None, OutputEvent(kind="assistant_text", session_id="s", run_id="r"))
    assert out.getvalue() == ""


def test_敏感载荷标记可被检测() -> None:
    """自检工具：命中敏感键名的载荷应被判为不安全（供断言复用）。

    同时固定一个反例：预算事件的 `input_tokens`/`output_tokens` **不得**被判为
    敏感——早期把裸 `token` 当标记会系统性误判。
    """

    from rollo.runtime_ports import payload_is_safe

    assert payload_is_safe({"tool": "read_file"}) is True
    assert payload_is_safe({"api_key": "x"}) is False
    assert payload_is_safe({"Authorization": "x"}) is False
    assert payload_is_safe({"access_token": "x"}) is False
    assert payload_is_safe({"input_tokens": 10, "output_tokens": 5}) is True


def test_port_diagnostics_record_failures_without_failures_attr() -> None:
    """不支持 failures 记录的端口，失败必须进入有界诊断环而非被静默丢弃。"""

    from rollo.runtime_ports import (
        port_diagnostics,
        reset_port_diagnostics,
    )

    class _Boom:
        name = "boom"

        def emit(self, event: OutputEvent) -> None:
            raise RuntimeError("nope")

    reset_port_diagnostics()
    emit_safely(_Boom(), OutputEvent(kind="info", session_id="s", run_id="r"))
    snapshot = port_diagnostics()
    assert len(snapshot) == 1
    assert "boom:info:RuntimeError" in snapshot[0]


def test_port_failure_does_not_change_run_terminal_state() -> None:
    """R2.1：端口抛错不得改变 run 的终态或 canonical 事件集合。

    用真实 RunStateGuard + RecordingEventSink 证明：端口失败时终态事件、
    事件序列与端口正常时完全一致（不是"端口内部自证"）。
    """

    from rollo.event_ids import RunContext
    from rollo.event_sink import RecordingEventSink, RuntimeEventEmitter
    from rollo.run_lifecycle import RunStateGuard

    class _BoomPort:
        name = "boom"

        def emit(self, event: OutputEvent) -> None:
            raise RuntimeError("renderer exploded")

    def _run(port) -> tuple[list[str], str, str]:
        sink = RecordingEventSink()
        guard = RunStateGuard(
            RunContext("s", "t", "run", "inv"), RuntimeEventEmitter(sink)
        )
        # 模拟 runtime 在关键节点发布事件：端口抛错不得影响这些状态转移。
        emit_safely(
            port, OutputEvent(kind="lifecycle", session_id="s", run_id="run", payload={"phase": "start"})
        )
        guard.start()
        emit_safely(
            port, OutputEvent(kind="info", session_id="s", run_id="run", payload={"message": "m"})
        )
        guard.awaiting_tool()
        guard.resume_running()
        terminal = guard.complete("normal")
        emit_safely(
            port,
            OutputEvent(kind="lifecycle", session_id="s", run_id="run", payload={"phase": "end"}),
        )
        order = [event.status or event.kind for event in sink.events]
        return order, terminal.status, str(guard.is_terminal)

    ok_order, ok_status, ok_terminal = _run(RecordingOutputPort())
    boom_order, boom_status, boom_terminal = _run(_BoomPort())

    assert ok_order == boom_order
    assert ok_status == boom_status == "completed"
    assert ok_terminal == boom_terminal == "True"


# ─── GAP-I02-06 夹具（真实挂起 / 真实终止 / 不阻塞事件循环）────────


def test_pausable_fixture_really_blocks_until_released() -> None:
    """可暂停夹具必须真正挂起：未放行时调用不返回。"""

    import threading

    box = FixtureToolbox()
    result: dict[str, str] = {}

    def _call() -> None:
        result["value"] = box.pausable()

    worker = threading.Thread(target=_call, daemon=True)
    worker.start()

    assert box.pausable.wait_started(timeout=5.0) is True
    worker.join(timeout=0.2)
    assert worker.is_alive(), "未放行时可暂停工具不应返回"

    box.pausable.release()
    worker.join(timeout=5.0)
    assert result["value"] == "resumed"


def test_cancellable_shell_fixture_really_terminates_process(tmp_path: Path) -> None:
    """可取消 shell 夹具必须持有真实句柄并能真正终止子进程。"""

    box = FixtureToolbox()
    box.cancellable_shell.seconds = 30
    box.cancellable_shell.start(cwd=tmp_path)
    assert box.cancellable_shell.running is True

    returncode = box.cancellable_shell.cancel(timeout=10.0)
    assert returncode is not None
    assert box.cancellable_shell.running is False


def test_late_result_fixture_does_not_block_caller() -> None:
    """晚到结果夹具在后台线程返回，不阻塞调用方。"""

    box = FixtureToolbox()
    box.late_result.delay_seconds = 0.05
    box.late_result.start_async()
    assert box.late_result() == "late-result"  # 立即返回，不 sleep
    box.late_result.join(timeout=5.0)
    assert box.late_result.returned is True


def test_controllable_provider_fixture_shapes() -> None:
    """GAP-C02-10：可控 Provider 夹具提供可暂停/抛错/长输出/晚到结果四种形态。"""

    import threading

    from rollo.tests.provider_fixtures import ControllableProvider, ProviderFixtureToolbox

    # 长输出
    long_one = ProviderFixtureToolbox.long_output(chunks=50)
    assert sum(1 for _ in long_one.stream()) == 51  # 50 text + 1 final

    # 抛错
    failing = ProviderFixtureToolbox.failing(at=1)
    with pytest.raises(RuntimeError):
        list(failing.stream())
    assert failing.failed is True

    # 可暂停：真挂起，直到 release()
    pausing = ControllableProvider.pausing(at=1)
    consumed: list[dict] = []

    def _drain() -> None:
        for chunk in pausing.stream():
            consumed.append(chunk)

    worker = threading.Thread(target=_drain, daemon=True)
    worker.start()
    assert pausing.wait_paused(timeout=5.0) is True
    worker.join(timeout=0.2)
    assert worker.is_alive(), "未放行时可暂停 Provider 不应继续"
    pausing.release()
    worker.join(timeout=5.0)
    assert len(consumed) == len(pausing.chunks)

    # 晚到结果（不阻塞调用方线程，由夹具内部等待）
    late = ControllableProvider.late(at=2, seconds=0.01)
    assert sum(1 for _ in late.stream()) == len(late.chunks)
    assert late.final_response()["provider"] == "anthropic"


def test_real_run_emits_events_through_port_and_keeps_stdout_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1.1 / N-16：由**真实 `Agent.chat()` run** 驱动端口断言。

    这是"接线类零新失败"的针对性补测：此前所有端口断言都直调 `agent._out_*`，
    因此 one-shot 注入、真实 `tool_call_id`、取消两级通知等接线缺失不会被发现。
    """

    from rollo.agent import Agent
    from rollo.project_context import ProjectContext

    monkeypatch.chdir(tmp_path)
    port = RecordingOutputPort()
    agent = Agent(project_context=ProjectContext.from_root(tmp_path), output_port=port)

    # 用 stub 替换模型调用，避免真实 Provider；run 的其他环节（输出端口、
    # 工具事件、canonical 写入）保持真实。
    async def _fake_chat_anthropic(self: Agent, user_message: str) -> None:
        self._emit_text(f"reply: {user_message}")
        self._out_tool_call("read_file", {"file_path": "x.txt"}, "real-call-id")
        self._out_tool_result("read_file", "content", tool_call_id="real-call-id")

    monkeypatch.setattr(Agent, "_chat_anthropic", _fake_chat_anthropic)

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        import asyncio

        asyncio.run(agent.chat("hello"))

    # 1) 事件在端口侧可观察，且携带身份
    kinds = port.kinds()
    assert "assistant_text" in kinds, kinds
    assert "tool_call" in kinds and "tool_result" in kinds
    assert all(e.session_id == str(agent.session_id) for e in port.events)
    assert all(e.run_id for e in port.events)

    # 2) 真实调用点传入的 tool_call_id 必须出现在事件上（N-1 的真实路径证据）
    tool_ids = [e.tool_call_id for e in port.events if e.kind in ("tool_call", "tool_result")]
    assert tool_ids == ["real-call-id", "real-call-id"]

    # 3) 端口已注入 → 业务输出不再直写进程标准流
    assert out.getvalue() == ""

    # 4) R1.3：真实 run 产出的**所有**事件载荷不得含密钥/完整配置
    from rollo.runtime_ports import payload_is_safe
    from rollo.tests.runtime_fixtures import assert_no_secrets

    for event in port.events:
        assert payload_is_safe(event.payload), event
        assert_no_secrets(dict(event.payload))


def test_agent_chat_default_port_is_silent_on_real_provider_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2.2：真实 `Agent.chat()` 路径缺省仍使用 NullOutputPort。"""

    from rollo.agent import Agent
    from rollo.project_context import ProjectContext

    async def fake_call(self: Agent):
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="headless reply")],
            usage=SimpleNamespace(
                input_tokens=2,
                output_tokens=3,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )

    monkeypatch.setattr(Agent, "_call_anthropic_stream", fake_call)
    monkeypatch.chdir(tmp_path)
    agent = Agent(
        project_context=ProjectContext.from_root(tmp_path),
        custom_system_prompt="fixture system",
        is_sub_agent=True,
    )
    agent._mcp_initialized = True

    out, err = io.StringIO(), io.StringIO()

    async def scenario() -> None:
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                await agent.chat("hello")
        finally:
            await agent.aclose()

    asyncio.run(scenario())

    assert out.getvalue() == ""
    assert err.getvalue() == ""
    assert agent.output_port.__class__.__name__ == "NullOutputPort"


def test_agent_chat_provider_error_is_emitted_to_output_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1.2：Provider 普通异常必须在 public chat 路径发布 error 事件。"""

    from rollo.agent import Agent
    from rollo.project_context import ProjectContext

    async def failing_call(self: Agent):
        raise RuntimeError("fixture provider failure")

    monkeypatch.setattr(Agent, "_call_anthropic_stream", failing_call)
    monkeypatch.chdir(tmp_path)
    port = RecordingOutputPort()
    agent = Agent(
        project_context=ProjectContext.from_root(tmp_path),
        custom_system_prompt="fixture system",
        is_sub_agent=True,
        output_port=port,
    )
    agent._mcp_initialized = True

    async def scenario() -> None:
        try:
            with pytest.raises(RuntimeError, match="fixture provider failure"):
                await agent.chat("hello")
        finally:
            await agent.aclose()

    asyncio.run(scenario())

    errors = port.of_kind("error")
    assert len(errors) == 1
    assert errors[0].payload["message"] == "fixture provider failure"
    assert errors[0].session_id == str(agent.session_id)
    assert errors[0].run_id


def test_agent_chat_port_failure_does_not_change_canonical_terminal_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2.1：public chat 中端口失败时 canonical 事件与正常端口保持等价。"""

    from rollo.agent import Agent
    from rollo.project_context import ProjectContext
    from rollo.runtime_store import SQLiteRuntimeStore

    async def fake_call(self: Agent):
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="stable reply")],
            usage=SimpleNamespace(
                input_tokens=2,
                output_tokens=3,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )

    monkeypatch.setattr(Agent, "_call_anthropic_stream", fake_call)
    monkeypatch.chdir(tmp_path)

    class _BoomPort:
        name = "boom"

        def __init__(self) -> None:
            self.failures: list[BaseException] = []

        def emit(self, event: OutputEvent) -> None:
            raise RuntimeError(f"renderer failed for {event.kind}")

    def canonical_shape(store) -> list[tuple]:
        return [
            (
                event.role,
                event.author,
                event.status,
                event.partial,
                event.content.get("kind") if event.content else None,
                event.content.get("text") if event.content else None,
                event.content.get("message") if event.content else None,
                event.metadata.get("lifecycle") if event.metadata else None,
            )
            for event in store.read_events()
        ]

    async def run() -> tuple[list[tuple], list[tuple], _BoomPort]:
        stores = [
            SQLiteRuntimeStore(tmp_path / "normal.sqlite"),
            SQLiteRuntimeStore(tmp_path / "boom.sqlite"),
        ]
        boom = _BoomPort()
        agents = [
            Agent(
                project_context=ProjectContext.from_root(tmp_path),
                custom_system_prompt="fixture system",
                is_sub_agent=True,
                runtime_store=stores[0],
                output_port=RecordingOutputPort(),
                runtime_session_id="session-port-equivalence",
                runtime_run_id="run-port-equivalence",
            ),
            Agent(
                project_context=ProjectContext.from_root(tmp_path),
                custom_system_prompt="fixture system",
                is_sub_agent=True,
                runtime_store=stores[1],
                output_port=boom,
                runtime_session_id="session-port-equivalence",
                runtime_run_id="run-port-equivalence",
            ),
        ]
        try:
            for agent in agents:
                agent._mcp_initialized = True
                await agent.chat("hello")
            return canonical_shape(stores[0]), canonical_shape(stores[1]), boom
        finally:
            for agent in agents:
                await agent.aclose()
            for store in stores:
                store.close()

    normal, failed, boom = asyncio.run(run())

    assert boom.failures
    assert normal == failed


def test_real_subagent_run_events_keep_child_run_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2.3：真实 agent-fork 运行的事件归属到子 run，而非父 run。"""

    from rollo.agent import Agent
    from rollo.project_context import ProjectContext
    from rollo.runtime_store import SQLiteRuntimeStore

    (tmp_path / "child.txt").write_text("child content", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    store = SQLiteRuntimeStore(tmp_path / "subagent-events.sqlite")
    port = RecordingOutputPort()
    parent = Agent(
        project_context=ProjectContext.from_root(tmp_path),
        custom_system_prompt="parent fixture",
        runtime_store=store,
        output_port=port,
        runtime_session_id="session-parent",
    )
    responses: dict[int, int] = {}

    monkeypatch.setattr(
        "rollo.agent.get_sub_agent_config",
        lambda *_args, **_kwargs: {
            "system_prompt": "child fixture",
            "tools": [tool for tool in parent.tools if tool["name"] == "read_file"],
        },
    )

    async def fake_call(agent: Agent):
        turn = responses.get(id(agent), 0)
        responses[id(agent)] = turn + 1
        usage = SimpleNamespace(
            input_tokens=1,
            output_tokens=1,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
        if turn == 0:
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        id="child-call-1",
                        name="read_file",
                        input={"file_path": "child.txt"},
                    )
                ],
                usage=usage,
            )
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="child done")],
            usage=usage,
        )

    monkeypatch.setattr(Agent, "_call_anthropic_stream", fake_call)

    async def scenario() -> str:
        try:
            return await parent._execute_agent_tool(
                {"type": "explore", "description": "child run", "prompt": "read child"}
            )
        finally:
            await parent.aclose()
            store.close()

    result = asyncio.run(scenario())

    assert "Sub-agent error" not in result
    child_events = [
        event for event in port.events if event.run_id.startswith("run-session-parent-explore-")
    ]
    assert child_events, "子 Agent 的真实运行没有发布可观察事件"
    assert all(event.session_id == "session-parent" for event in child_events)
    assert all(event.run_id != "session-parent" for event in child_events)
    assert any(
        event.kind == "tool_call" and event.tool_call_id == "child-call-1"
        for event in child_events
    )
    assert any(
        event.kind == "lifecycle" and event.payload.get("phase") == "turn_complete"
        for event in child_events
    )


def test_tool_fixtures_cover_four_shapes(tmp_path: Path) -> None:
    """夹具提供可暂停 / 长输出 / 可取消 shell / 晚到结果四种形态。"""

    box = FixtureToolbox()

    # 长输出（体积可控且确定性）
    text = box.long_output({"lines": 200})
    assert text.count("\n") == 199

    # 命令可被真实终止（句柄由 start() 提供，见上一条用例）
    assert "time.sleep(30)" in box.cancellable_shell.command()[-1]

    handlers = box.as_handlers()
    assert set(handlers) == {"pausable", "long_output", "late_result"}
