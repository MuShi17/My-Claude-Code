"""Runtime 结构化输出端口。

把 runtime 的观察面从终端渲染中解耦：runtime 只发布结构化事件，具体渲染由
端口实现决定。端口是**观察接口**——canonical 事实仍由既有 emitter/store 承担，
端口实现抛错不得改变 run 终态或改写 provider 消息。

对应 OpenSpec change ``decouple-runtime-interaction-from-tui`` 的
``runtime-output-ports`` 能力与 design D1/D2。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

__all__ = [
    "OutputKind",
    "OutputEvent",
    "OutputPort",
    "NullOutputPort",
    "RecordingOutputPort",
    "emit_safely",
    "payload_is_safe",
    "SENSITIVE_KEY_MARKERS",
]

# 事件种类：覆盖文本/思考、工具状态、预算、错误、子 Agent 归属与生命周期。
OutputKind = str


class _Kinds:
    ASSISTANT_TEXT = "assistant_text"
    ASSISTANT_THINKING = "assistant_thinking"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TOOL_DENIED = "tool_denied"
    BUDGET = "budget"
    ERROR = "error"
    INFO = "info"
    RETRY = "retry"
    DIVIDER = "divider"
    SPINNER = "spinner"
    SUB_AGENT_START = "sub_agent_start"
    SUB_AGENT_END = "sub_agent_end"
    LIFECYCLE = "lifecycle"
    DIAGNOSTIC = "diagnostic"


# 事件载荷中不得出现的敏感字段名标记（大小写不敏感的子串匹配）。
# 注意：不使用裸 "token" —— 它会误判预算事件的 input_tokens/output_tokens。
SENSITIVE_KEY_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "credential",
    "access_token",
    "refresh_token",
    "auth_token",
)


@dataclass(frozen=True)
class OutputEvent:
    """一个结构化输出事件。

    身份字段足以把同一 run 的事件归组；``attempt_id``/``tool_call_id``/``stream``
    在适用时填写。载荷放在 ``payload``，不得包含密钥或完整配置对象。
    """

    kind: OutputKind
    session_id: str
    run_id: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    attempt_id: str | None = None
    tool_call_id: str | None = None
    stream: str | None = None

    def __post_init__(self) -> None:
        if not self.session_id:
            raise ValueError("OutputEvent 需要 session_id")
        if not self.run_id:
            raise ValueError("OutputEvent 需要 run_id")

    @property
    def identity(self) -> tuple[str, str]:
        return (self.session_id, self.run_id)


@runtime_checkable
class OutputPort(Protocol):
    """输出端口协议：实现方只需处理事件，不返回决策。"""

    def emit(self, event: OutputEvent) -> None:  # pragma: no cover - 协议
        ...


class NullOutputPort:
    """安全默认实现：不写业务输出，只记录"未注入端口"诊断。

    用于无终端调用方（headless、未来的 host/GUI）。它**不**回退到隐式终端打印。
    """

    name = "null"

    def __init__(self) -> None:
        self.diagnostics: list[str] = []

    def emit(self, event: OutputEvent) -> None:
        if event.kind == _Kinds.DIAGNOSTIC:
            self.diagnostics.append(str(event.payload.get("message", "")))


class RecordingOutputPort:
    """测试与诊断用：把事件按顺序记录下来，便于断言语义序列。"""

    name = "recording"

    def __init__(self) -> None:
        self.events: list[OutputEvent] = []
        self.failures: list[BaseException] = []

    def emit(self, event: OutputEvent) -> None:
        self.events.append(event)

    def kinds(self) -> list[str]:
        return [e.kind for e in self.events]

    def of_kind(self, kind: OutputKind) -> list[OutputEvent]:
        return [e for e in self.events if e.kind == kind]


def payload_is_safe(payload: Mapping[str, Any]) -> bool:
    """粗粒度守卫：载荷键名命中敏感标记即视为不安全（供断言与自检使用）。"""

    for key in payload:
        lowered = str(key).lower()
        if any(marker in lowered for marker in SENSITIVE_KEY_MARKERS):
            return False
    return True


def emit_safely(port: OutputPort | None, event: OutputEvent) -> None:
    """向端口发布事件，并把端口失败降级为诊断，绝不影响 run。

    - 端口为 None 时不做任何输出（调用方应显式注入；此处不隐式回退终端）。
    - 端口抛错被吞掉：优先记入端口自身的 ``failures``；端口不支持记录时，
      记入本模块的诊断环（有界），保证"降级为诊断"确实发生而不是静默丢弃。
    """

    if port is None:
        return
    try:
        port.emit(event)
    except Exception as exc:  # noqa: BLE001 - 端口失败必须被隔离
        failures = getattr(port, "failures", None)
        if isinstance(failures, list):
            failures.append(exc)
        else:
            _record_port_diagnostic(port, event, exc)


_PORT_DIAGNOSTICS: list[str] = []
_PORT_DIAGNOSTIC_LIMIT = 50


def _record_port_diagnostic(port: OutputPort, event: OutputEvent, exc: BaseException) -> None:
    """记录端口失败诊断（有界），供 headless/宿主诊断使用。"""

    if len(_PORT_DIAGNOSTICS) >= _PORT_DIAGNOSTIC_LIMIT:
        return
    name = getattr(port, "name", type(port).__name__)
    _PORT_DIAGNOSTICS.append(f"{name}:{event.kind}:{type(exc).__name__}: {exc}")


def port_diagnostics() -> tuple[str, ...]:
    """返回（进程内）端口失败诊断快照。"""

    return tuple(_PORT_DIAGNOSTICS)


def reset_port_diagnostics() -> None:
    """清空端口诊断（测试用）。"""

    _PORT_DIAGNOSTICS.clear()


# ─── 模块级诊断通道 ───────────────────────────────────────────


@dataclass(frozen=True)
class _DiagnosticEvent:
    message: str


_DIAGNOSTIC_SINK: Callable[[str], None] | None = None


def set_diagnostic_sink(sink: Callable[[str], None] | None) -> None:
    """设置模块级诊断接收器（入口注入终端实现；测试可替换或清空）。

    未设置时诊断默认**不输出**，从而保证"runtime 不依赖终端"。
    """

    global _DIAGNOSTIC_SINK
    _DIAGNOSTIC_SINK = sink


def emit_diagnostic(message: str) -> None:
    """发布一条诊断消息（MCP 初始化、记忆召回失败等后台事件）。

    与 `emit_safely` 的端口隔离语义一致：接收器抛错被吞掉并记入诊断环，
    绝不因为日志/诊断失败而影响 runtime 行为。
    """

    if not message:
        return
    sink = _DIAGNOSTIC_SINK
    if sink is None:
        # 未注入接收器：只留在进程内有界诊断环，不写任何标准流。
        if len(_PORT_DIAGNOSTICS) < _PORT_DIAGNOSTIC_LIMIT:
            _PORT_DIAGNOSTICS.append(f"diagnostic: {message}")
        return
    try:
        sink(message)
    except Exception as exc:  # noqa: BLE001 - 诊断失败必须被隔离
        if len(_PORT_DIAGNOSTICS) < _PORT_DIAGNOSTIC_LIMIT:
            _PORT_DIAGNOSTICS.append(f"diagnostic-sink:{type(exc).__name__}: {exc}")
