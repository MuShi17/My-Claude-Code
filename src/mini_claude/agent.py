"""
Agent 核心循环 — 双后端（Anthropic + OpenAI 兼容）、流式输出、
4 层上下文压缩、Plan Mode、Sub-Agent、预算控制。
对应 Claude Code 的 agent 架构。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable, Awaitable

import anthropic
import openai

from .tools import (
    tool_definitions,
    execute_tool,
    check_permission,
    CONCURRENCY_SAFE_TOOLS,
    get_active_tool_definitions,
    ToolDef,
    PermissionMode,
)
from .memory import (
    start_memory_prefetch,
    format_memories_for_injection,
    MemoryPrefetch,
)
from .ui import (
    print_assistant_text,
    print_tool_call,
    print_tool_result,
    print_error,
    print_confirmation,
    print_divider,
    print_cost,
    print_retry,
    print_info,
    print_sub_agent_start,
    print_sub_agent_end,
    start_spinner,
    stop_spinner,
)
from .session import runtime_data_dir, runtime_store_path, save_session_v2
from .prompt import build_system_prompt
from .subagent import get_sub_agent_config
from .mcp_client import McpManager
from .event_ids import IdentityFactory, RunContext
from .event_sink import EventSink, RuntimeEventEmitter
from .runtime_event import RuntimeEvent
from .redaction import redact_payload
from .runtime_lifecycle import DurableToolBoundary, ModelCallRecorder
from .context_transition import (
    ContextReplacement,
    ContextTransition,
    build_context_transition,
    validate_transition_candidate,
)
from .provider_content import (
    display_tool_result,
    materialize_tool_result,
    materialized_content_bytes,
)
from .runtime_store import SQLiteRuntimeStore
from .run_lifecycle import RunStateGuard
from .compaction import CompactionCheckpoint, CompactionCheckpointBuilder, CompactionError
from .projections.base import EventRecord
from .projections.model_replay_projection import ModelReplayProjection, ModelReplayResult
from .projections.incremental_replay import IncrementalModelReplayCursor, IncrementalReplayError
from .projections.provider_context import CanonicalModelContextAdapter
from .artifact_archive import ArtifactArchive
from .llm_capture import LLMCaptureManager, LLMCapturePolicy

# ─── 指数退避重试 ──────────────────────────────────────────
# 对 429（限流）、503/529（过载）、网络错误进行最多 3 次重试，
# 延迟 = 1s/2s/4s（上限 30s）+ 随机抖动，避免惊群效应。


def _is_retryable(error: Exception) -> bool:
    """判断是否为可重试的 API 错误（限流/过载/网络错误）。"""
    status = getattr(error, "status_code", None) or getattr(error, "status", None)
    if status in (429, 503, 529):
        return True
    msg = str(error)
    if "overloaded" in msg or "ECONNRESET" in msg or "ETIMEDOUT" in msg:
        return True
    return False


async def _with_retry(fn, max_retries: int = 3, on_retry: Callable[[int, Exception], Any] | None = None):
    """对异步函数 fn 执行指数退避重试。"""
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except Exception as error:
            if attempt >= max_retries or not _is_retryable(error):
                raise
            delay = min(1000 * (2 ** attempt), 30000) / 1000 + (hash(str(time.time())) % 1000) / 1000
            status = getattr(error, "status_code", None) or getattr(error, "status", None)
            reason = f"HTTP {status}" if status else (getattr(error, "code", None) or "network error")
            if on_retry:
                on_retry(attempt + 2, error)
            print_retry(attempt + 1, max_retries, reason)
            await asyncio.sleep(delay)


# ─── 模型上下文窗口大小 ────────────────────────────────────
# 用于判断何时需要触发会话压缩（auto-compact）。

MODEL_CONTEXT = {
    "claude-opus-4-6": 200000,
    "claude-sonnet-4-6": 200000,
    "claude-sonnet-4-20250514": 200000,
    "claude-haiku-4-5-20251001": 200000,
    "claude-opus-4-20250514": 200000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
}


def _get_context_window(model: str) -> int:
    return MODEL_CONTEXT.get(model, 200000)


# ─── Thinking（扩展思考）支持检测 ────────────────────────────
# Claude Opus/Sonnet/Haiku 4.x 支持 extended thinking，
# Claude 3.x 系列不支持；Opus 4.6 / Sonnet 4.6 额外支持 adaptive 模式。

THINKING_EFFORTS = ("none", "low", "high", "max")
DEFAULT_THINKING_EFFORT = "max"


def _normalize_thinking_effort(effort: str | None) -> str:
    """规范化思考强度；none 用于显式关闭思考模式。"""
    value = (effort or DEFAULT_THINKING_EFFORT).strip().lower()
    value = {"off": "none", "disabled": "none"}.get(value, value)
    if value not in THINKING_EFFORTS:
        allowed = ", ".join(THINKING_EFFORTS)
        raise ValueError(
            f"Invalid thinking effort {effort!r}; expected one of: {allowed}"
        )
    return value


def _model_supports_thinking(model: str) -> bool:
    """判断模型是否支持扩展思考功能。"""
    m = model.lower()
    if "deepseek" in m or "reasoner" in m:
        return True
    if "claude-3-" in m or "3-5-" in m or "3-7-" in m:
        return False
    if "claude" in m and any(x in m for x in ("opus", "sonnet", "haiku")):
        return True
    if "reasoner" in m or any(name in m for name in ("gpt-5", "o1", "o3", "o4")):
        return True
    return False


class CanonicalFinalizationError(RuntimeError):
    """The run could not durably publish its canonical terminal state."""

    code = "canonical_finalization_failed"


class ProviderContentNormalizationError(ValueError):
    """A provider returned a text-bearing block with an unsafe value shape."""

    code = "provider_content_normalization_failed"

    def __init__(
        self,
        *,
        provider: str,
        block_kind: str,
        block_index: int,
        value: Any,
    ) -> None:
        self.provider = provider
        self.block_kind = block_kind
        self.block_index = block_index
        self.value_type = _provider_value_type(value)
        # Keep the message useful for diagnosis while deliberately excluding
        # the value itself: compatible providers may return secrets or huge
        # structured payloads in malformed text fields.
        super().__init__(
            "provider content rejected: "
            f"provider={provider} block_kind={block_kind} "
            f"block_index={block_index} value_type={self.value_type}"
        )


def _provider_value_type(value: Any) -> str:
    """Return a stable, payload-free type label for diagnostics."""

    if value is None:
        return "null"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "mapping"
    if isinstance(value, (list, tuple)):
        return "sequence"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return type(value).__name__
    return "object"


def _normalize_provider_text(
    value: Any,
    *,
    provider: str,
    block_kind: str,
    block_index: int,
) -> str:
    """Normalize one provider text field without coercing unknown objects."""

    if isinstance(value, str):
        return value
    raise ProviderContentNormalizationError(
        provider=provider,
        block_kind=block_kind,
        block_index=block_index,
        value=value,
    )


def _model_supports_adaptive_thinking(model: str) -> bool:
    """判断模型是否支持 adaptive thinking（动态调整思考预算）。"""
    m = model.lower()
    return "opus-4-6" in m or "sonnet-4-6" in m


def _get_anthropic_request_max_tokens(model: str) -> int:
    """返回 Anthropic 请求所需的 token envelope，而非模型输出上限。

    Anthropic Messages API 要求请求携带 max_tokens；这里使用模型上下文窗口
    作为协议层预算，不再根据模型名称把可见输出硬编码为 16K/32K/64K。
    模型和服务端仍会执行其自身的上下文及输出能力限制。
    """
    return max(_get_context_window(model), 1)


def _model_supports_openai_reasoning_effort(model: str) -> bool:
    """判断 OpenAI Chat Completions 后端是否应发送 reasoning_effort。"""
    m = model.lower()
    return (
        "deepseek" in m
        or "reasoner" in m
        or any(name in m for name in ("gpt-5", "o1", "o3", "o4"))
    )


def _is_deepseek_model(model: str) -> bool:
    return "deepseek" in model.lower()


def _get_thinking_budget_tokens(effort: str) -> int:
    """为旧版 Anthropic thinking 参数将 effort 映射成 token 预算。

    该预算只控制旧版 thinking 块，不限制最终可见输出长度；新式 adaptive
    和 DeepSeek output_config 模式不使用此映射。
    """
    return {"low": 8192, "high": 16384, "max": 32768}[effort]


def _thinking_request_params(
    model: str,
    effort: str,
    *,
    use_openai: bool,
) -> dict[str, Any]:
    """构造后端对应的思考参数，不为 none 或不支持的模型发送参数。"""
    normalized = _normalize_thinking_effort(effort)
    if normalized == "none":
        if _is_deepseek_model(model):
            return {"thinking": {"type": "disabled"}}
        return {}

    if use_openai:
        if not _model_supports_openai_reasoning_effort(model):
            return {}
        params = {"reasoning_effort": normalized}
        if _is_deepseek_model(model):
            params["thinking"] = {"type": "enabled"}
        return params

    if _model_supports_adaptive_thinking(model):
        return {
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": normalized},
        }
    if _is_deepseek_model(model):
        return {
            "thinking": {"type": "enabled"},
            "output_config": {"effort": normalized},
        }
    if _model_supports_thinking(model):
        return {
            "thinking": {
                "type": "enabled",
                "budget_tokens": _get_thinking_budget_tokens(normalized),
            }
        }
    return {}


# ─── 工具转换为 OpenAI 格式 ────────────────────────────────
# Anthropic 和 OpenAI 的工具 schema 格式不同，此函数将 Anthropic 格式
# 的 tool_definitions 转换为 OpenAI function calling 格式。


def _to_openai_tools(tools: list[ToolDef]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


# ─── 多层压缩常量 ──────────────────────────────────────────
# SNIPPABLE_TOOLS：可被裁剪的工具类型（结果为文本，模型可重读）
# SNIP_THRESHOLD：利用率超过 60% 时触发 stale snip
# MICROCOMPACT_IDLE_S：空闲 5 分钟后触发 microcompact（清除旧工具结果）
# KEEP_RECENT_RESULTS：压缩时保留最近 3 条工具结果

SNIPPABLE_TOOLS = {"read_file", "grep_search", "list_files", "run_shell"}
SNIP_PLACEHOLDER = "[Content snipped - re-read if needed]"
SNIP_THRESHOLD = 0.60
MICROCOMPACT_IDLE_S = 5 * 60  # 5 分钟
KEEP_RECENT_RESULTS = 3


# ─── Agent 主类 ────────────────────────────────────────────
# 核心编排类，负责：双后端流式调用、工具执行调度、权限检查、
# 上下文压缩、Plan Mode、Sub-Agent 管理、记忆召回、MCP 集成。


class Agent:
    def __init__(
        self,
        *,
        permission_mode: str = "default",
        model: str = "claude-opus-4-6",
        api_base: str | None = None,
        anthropic_base_url: str | None = None,
        api_key: str | None = None,
        thinking: bool | None = None,
        thinking_effort: str = DEFAULT_THINKING_EFFORT,
        max_cost_usd: float | None = None,
        max_turns: int | None = None,
        confirm_fn: Callable[[str], Awaitable[bool]] | None = None,
        custom_system_prompt: str | None = None,
        custom_tools: list[ToolDef] | None = None,
        is_sub_agent: bool = False,
        runtime_store: SQLiteRuntimeStore | None = None,
        runtime_sink: EventSink | None = None,
        runtime_parent_run_id: str | None = None,
        runtime_run_id: str | None = None,
        runtime_session_id: str | None = None,
        runtime_context_id: str | None = None,
        runtime_parent_context_id: str | None = None,
        artifact_archive: ArtifactArchive | None = None,
        llm_capture_policy: LLMCapturePolicy | None = None,
    ):
        self.permission_mode = permission_mode
        self.thinking_effort = _normalize_thinking_effort(thinking_effort)
        # 保留旧版 thinking bool 参数：False 显式关闭；新的调用方应优先使用
        # thinking_effort，因此不让旧参数覆盖显式的 effort=none。
        if thinking is False:
            self.thinking_effort = "none"
        self.thinking = self.thinking_effort != "none"
        self.model = model
        self.use_openai = bool(api_base)
        self.is_sub_agent = is_sub_agent
        self._runtime_store = runtime_store
        self._runtime_sink = runtime_sink
        self._runtime_emitter: RuntimeEventEmitter | None = None
        self._runtime_context: RunContext | None = None
        self._runtime_recorder: ModelCallRecorder | None = None
        self._runtime_boundary: DurableToolBoundary | None = None
        self._runtime_store_owned = False
        self._runtime_parent_run_id = runtime_parent_run_id
        self._runtime_run_id = runtime_run_id
        self._runtime_context_id = runtime_context_id
        self._runtime_parent_context_id = runtime_parent_context_id
        self._identity_factory = IdentityFactory(prefix="agent")
        self._runtime_guard: RunStateGuard | None = None
        self._runtime_exit_status: str | None = None
        self._runtime_exit_reason: str | None = None
        self._artifact_archive = artifact_archive
        self._llm_capture_policy = llm_capture_policy or LLMCapturePolicy()
        self._llm_capture_manager: LLMCaptureManager | None = None
        self.tools = custom_tools or tool_definitions
        self.max_cost_usd = max_cost_usd
        self.max_turns = max_turns
        self.confirm_fn = confirm_fn
        # 有效上下文窗口 = 模型窗口 - 20K 留白（给 system prompt + output）
        self.effective_window = _get_context_window(model) - 20000
        self.session_id = runtime_session_id or uuid.uuid4().hex[:8]
        if self._runtime_context_id is None:
            self._runtime_context_id = f"context:{self.session_id}"
        self.session_start_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # Token 累计统计
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.last_input_token_count = 0
        self.current_turns = 0
        self.last_api_call_time = 0.0
        self._context_epoch = "context:initial"
        self._pending_compaction_tail: list[dict[str, Any]] | None = None
        self._pending_compaction_summary_source: list[dict[str, Any]] | None = None
        self._replay_cursor: IncrementalModelReplayCursor | None = None
        self._replay_events_read = 0
        self._replay_refresh_count = 0
        self._replay_last_read_count = 0
        self._replay_last_duration_ms = 0
        self._replay_last_mode = "cold"
        self._replay_last_rebuild_reason = "not_initialized"
        self._replay_last_source_digest = ""
        self._replay_last_projection_digest = ""

        # Ctrl+C 中断支持
        self._aborted = False
        self._current_task: asyncio.Task | None = None

        # 权限白名单：本次会话已确认过的路径
        self._confirmed_paths: set[str] = set()

        # Plan Mode 状态
        self._pre_plan_mode: str | None = None
        self._plan_file_path: str | None = None
        self._plan_approval_fn: Callable[[str], Awaitable[dict]] | None = None
        self._context_cleared: bool = False  # plan 审批通过后清空上下文

        # Thinking（扩展思考）模式：disabled / adaptive / enabled
        self._thinking_mode = self._resolve_thinking_mode()

        # Sub-Agent 输出缓冲区（子 Agent 通过 _output_buffer 捕获输出）
        self._output_buffer: list[str] | None = None

        # 先读后改保护：记录每个文件上次读取时的 mtime（绝对路径 → mtime）
        self._read_file_state: dict[str, float] = {}

        # MCP 集成（主 Agent 首次聊天时惰性初始化）
        self._mcp_manager = McpManager()
        self._mcp_initialized = False

        # 记忆召回状态 — 每个用户轮次的语义预取
        self._already_surfaced_memories: set[str] = set()
        self._session_memory_bytes = 0

        # 事件钩子系统（观测/Observer模式）
        self._event_hooks: dict[str, list] = {}

        # Ask 计数（每次 chat() 入口自增，用于 trace 文件编号）
        self._ask_count: int = 0

        # 双后端消息历史（分开存储，避免格式转换）
        self._anthropic_messages: list[dict] = []
        self._openai_messages: list[dict] = []

        # Build system prompt
        self._base_system_prompt = custom_system_prompt or build_system_prompt()
        if self.permission_mode == "plan":
            self._plan_file_path = self._generate_plan_file_path()
            self._system_prompt = self._base_system_prompt + self._build_plan_mode_prompt()
        else:
            self._system_prompt = self._base_system_prompt

        # 初始化 API 客户端（Anthropic 或 OpenAI 兼容）
        if self.use_openai:
            self._openai_client = openai.AsyncOpenAI(base_url=api_base, api_key=api_key)
            self._anthropic_client = None
            self._openai_messages.append({"role": "system", "content": self._system_prompt})
        else:
            kwargs: dict[str, Any] = {}
            if api_key:
                kwargs["api_key"] = api_key
            if anthropic_base_url:
                kwargs["base_url"] = anthropic_base_url
            self._anthropic_client = anthropic.AsyncAnthropic(**kwargs)
            self._openai_client = None

    def _resolve_thinking_mode(self) -> str:
        if self.thinking_effort == "none":
            return "disabled"
        if not _model_supports_thinking(self.model):
            return "disabled"
        if _model_supports_adaptive_thinking(self.model):
            return "adaptive"
        return "enabled"

    # ─── 事件发射器 ─────────────────────────────────────────

    def on(self, event: str, callback) -> None:
        """订阅事件。callback 接收 payload dict 参数。"""
        self._event_hooks.setdefault(event, []).append(callback)

    def off(self, event: str, callback) -> None:
        """取消订阅。"""
        hooks = self._event_hooks.get(event)
        if hooks and callback in hooks:
            hooks.remove(callback)

    async def _emit(self, event: str, payload: Any = None) -> None:
        """发射事件。同步和异步回调均支持。"""
        import asyncio as _asyncio
        self._emit_runtime_observation(event, payload)
        for cb in self._event_hooks.get(event, []):
            try:
                res = cb(payload)
                if _asyncio.iscoroutine(res):
                    await res
            except Exception:
                pass  # 观测错误不影响主流程

    def _setup_runtime_facade(self) -> None:
        """Create the canonical facade for one canonical invocation."""

        if self._runtime_emitter is not None and not self._runtime_store_owned:
            # A caller-owned sink/store remains available across asks.
            pass
        else:
            if self._runtime_store is None and self._runtime_sink is None:
                self._runtime_store = SQLiteRuntimeStore(
                    runtime_store_path(self.session_id)
                )
                self._runtime_store_owned = True
            canonical: EventSink = self._runtime_store or self._runtime_sink  # type: ignore[assignment]
            if canonical is None:
                raise RuntimeError("runtime facade requires a canonical sink")
            if self._artifact_archive is None:
                self._artifact_archive = ArtifactArchive(
                    runtime_data_dir() / "artifacts",
                    metadata_store=self._runtime_store,
                )
            self._llm_capture_manager = LLMCaptureManager(
                policy=self._llm_capture_policy,
                archive=self._artifact_archive,
                runtime_store=self._runtime_store,
            )
            self._runtime_emitter = RuntimeEventEmitter(canonical)

        self._runtime_context = RunContext(
            session_id=self.session_id,
            turn_id=f"turn-{self._ask_count:04d}",
            run_id=self._runtime_run_id or self._identity_factory.run_id(),
            invocation_id=self._identity_factory.invocation_id(),
            parent_run_id=self._runtime_parent_run_id,
            context_id=self._runtime_context_id,
            parent_context_id=self._runtime_parent_context_id,
        )
        self._runtime_guard = RunStateGuard(self._runtime_context, self._runtime_emitter)
        self._runtime_guard.start()
        self._runtime_recorder = None
        self._runtime_boundary = None
        self._runtime_exit_status = None
        self._runtime_exit_reason = None

    def _start_runtime_model_call(self, request_id: str, provider: str, request: Any) -> None:
        if self._runtime_emitter is None or self._runtime_context is None:
            return
        context = RunContext(
            session_id=self._runtime_context.session_id,
            turn_id=self._runtime_context.turn_id,
            run_id=self._runtime_context.run_id,
            invocation_id=request_id,
            parent_run_id=self._runtime_context.parent_run_id,
            branch=self._runtime_context.branch,
            context_id=self._runtime_context.context_id,
            parent_context_id=self._runtime_context.parent_context_id,
        )
        self._runtime_recorder = ModelCallRecorder(
            self._runtime_emitter,
            context,
            provider=provider,
            model=self.model,
        )
        self._runtime_recorder.start(request_id, request=request if isinstance(request, dict) else None)
        self._runtime_boundary = DurableToolBoundary(
            self._runtime_emitter,
            context,
            artifact_archive=self._artifact_archive,
        )

    def _record_runtime_model_error(self, error: BaseException) -> None:
        """Seal the current model call as failed before propagating its error."""

        if self._runtime_recorder:
            self._runtime_recorder.error(error)
            if self._runtime_guard and self._runtime_recorder.events:
                self._runtime_guard.adopt_terminal_event(self._runtime_recorder.events[-1])

    def _record_budget_exceeded(self, reason: str) -> None:
        """Finalize the run budget without re-finishing the model call.

        A provider response can finish its model recorder before the agent
        notices that the resulting tool turn exhausted the run budget.  The
        budget decision is therefore a run-level concern and must use the
        ``RunStateGuard`` rather than ``ModelCallRecorder.budget_exceeded``.
        """

        terminal = None
        if self._runtime_guard is not None:
            terminal = self._runtime_guard.budget_exceeded(reason)
        if terminal is not None:
            self._runtime_exit_status = "budget_exceeded"
            self._runtime_exit_reason = reason

    async def _run_durable_tool(
        self,
        *,
        request_id: str,
        call_id: str,
        name: str,
        inp: Any,
        permission: dict[str, Any] | str,
        arguments: Any | None = None,
    ) -> tuple[Any, bool, bool]:
        """Run one tool only after the canonical dispatch barrier succeeds."""

        del request_id
        if self._runtime_boundary is None:
            raise RuntimeError("canonical durable tool boundary is not initialized")
        result = await self._runtime_boundary.execute(
            call_id=call_id,
            name=name,
            arguments=inp if arguments is None else arguments,
            permission=permission,
            executor=lambda: self._execute_tool_call(name, inp),
            on_started=lambda: self._emit(
                "tool_start", {"tool_name": name, "tool_input": inp, "tool_call_id": call_id}
            ),
        )
        return result.result, result.success, result.executed

    def _emit_runtime_observation(self, event: str, payload: Any) -> None:
        """Persist non-provider lifecycle observations through the emitter."""

        if self._runtime_emitter is None or self._runtime_context is None:
            return
        if event not in {"chat_start", "chat_error", "first_token", "turn_start", "turn_end", "compaction"}:
            return
        details = dict(payload or {})
        if event == "chat_error":
            runtime_event = RuntimeEvent.create(
                self._runtime_context,
                role="system",
                author="system",
                content={
                    "kind": "error",
                    "code": "chat_error",
                    "message": str(details.get("error", "chat failed")),
                },
                ts=int(time.time() * 1000),
                metadata={"lifecycle": event},
            )
        else:
            if event == "chat_start":
                details = {"started": True}
            runtime_event = RuntimeEvent.create(
                self._runtime_context,
                role="system",
                author="system",
                actions={event: details},
                ts=int(time.time() * 1000),
                metadata={"lifecycle": event},
            )
        self._runtime_emitter.emit(runtime_event)

    def _emit_canonical_user_event(self, user_message: str) -> None:
        """Record the original user input before provider context mutation."""

        if self._runtime_emitter is None or self._runtime_context is None:
            return
        event = RuntimeEvent.create(
            self._runtime_context,
            role="user",
            author="user",
            content={"kind": "text", "text": user_message},
            ts=int(time.time() * 1000),
            metadata={
                "lifecycle": "user_input",
                "source": "user",
                "injected": False,
            },
        )
        self._runtime_emitter.emit(event)

    def _persist_memory_context_event(self, memories: list[Any]) -> RuntimeEvent:
        """Persist the exact memory context before rebuilding the request."""

        if self._runtime_emitter is None or self._runtime_context is None:
            raise RuntimeError("canonical runtime facade is not initialized")
        source_values = [str(memory.path) for memory in memories]
        raw_content = {
            "kind": "context",
            "context_type": "memory",
            "text": format_memories_for_injection(memories),
            "sources": source_values,
            "sequence": 0,
        }
        # Put the payload under ``content`` so the redaction policy recognizes
        # ``content.text`` as replay state and never replaces long memory text
        # with a non-string bounded reference.
        redacted_wrapper = redact_payload(
            {"content": raw_content}, self._runtime_emitter.redaction_policy
        )
        safe_content = dict(redacted_wrapper["content"])
        safe_text = str(safe_content["text"])
        content_digest = hashlib.sha256(safe_text.encode("utf-8")).hexdigest()
        context_id = self._runtime_context.context_id
        idempotency_key = (
            f"memory:{context_id}:{self._runtime_context.turn_id}:{content_digest}"
        )
        safe_content["content_digest"] = content_digest
        safe_content["idempotency_key"] = idempotency_key
        event_id = "memory-event:" + hashlib.sha256(
            idempotency_key.encode("utf-8")
        ).hexdigest()[:32]
        if self._runtime_store is not None:
            read_event = getattr(self._runtime_store, "read_event", None)
            if callable(read_event):
                existing = read_event(event_id)
                if existing is not None:
                    return existing
            else:
                for _, existing in self._runtime_store.read_event_records():
                    if existing.id == event_id:
                        return existing
        elif self._runtime_emitter is not None:
            sink_events = getattr(self._runtime_emitter.sink, "events", ())
            for existing in sink_events:
                if existing.id == event_id:
                    return existing
        event = RuntimeEvent.create(
            self._runtime_context,
            role="user",
            author="system",
            origin="code_mode",
            model_visibility="visible",
            content=safe_content,
            ts=int(time.time() * 1000),
            event_id=event_id,
            metadata={
                "lifecycle": "memory_injection",
                "context_type": "memory",
                "idempotency_key": idempotency_key,
            },
        )
        self._runtime_emitter.emit(event)
        return event

    def _record_sub_agent_event(self, *, name: str, agent_type: str, prompt: str) -> None:
        if self._runtime_emitter is None or self._runtime_context is None:
            return
        event = RuntimeEvent.create(
            self._runtime_context,
            role="system",
            author="agent",
            actions={"sub_agent": {"name": name, "agent_type": agent_type, "prompt_summary": prompt[:200]}},
            ts=int(time.time() * 1000),
            metadata={"lifecycle": "child_run_opened"},
        )
        self._runtime_emitter.emit(event)

    def _capture_llm(
        self,
        *,
        request_id: str,
        messages: list[dict],
        response: dict,
        usage: dict,
        latency_ms: int,
        input_tokens: int | None,
        output_tokens: int | None,
        cache_read_tokens: int | None,
        finish_reason: str,
    ) -> None:
        """Capture according to the explicit privacy policy only."""

        capture = None
        if self._llm_capture_manager is not None and self._runtime_context is not None:
            capture = self._llm_capture_manager.capture(
                request_id=request_id,
                session_id=self._runtime_context.session_id,
                run_id=self._runtime_context.run_id,
                invocation_id=request_id,
                attempt=self._runtime_recorder.attempt if self._runtime_recorder else 1,
                attempt_id=self._runtime_recorder.attempt_id if self._runtime_recorder else None,
                provider="openai" if self.use_openai else "anthropic",
                model=self.model,
                request=messages,
                response=response,
                usage=usage,
                latency_ms=latency_ms,
            )
            self._emit_llm_capture_observation(request_id, capture)

        del input_tokens, output_tokens, cache_read_tokens, finish_reason, latency_ms

    def _emit_llm_capture_observation(self, request_id: str, capture: Any) -> None:
        if self._runtime_emitter is None or self._runtime_context is None:
            return
        refs = {"llm_ref": capture.llm_ref} if capture.llm_ref else None
        event = RuntimeEvent.create(
            self._runtime_context,
            role="system",
            author="agent",
            actions={
                "llm_capture": {
                    "request_id": request_id,
                    "capture_status": capture.capture_status,
                    "error": capture.error,
                }
            },
            refs=refs,
            ts=int(time.time() * 1000),
            metadata={"lifecycle": "llm_capture", "capture_mode": self._llm_capture_policy.mode},
        )
        try:
            self._runtime_emitter.emit(event)
        except Exception:
            # Capture is auxiliary; a provider response must not be turned into
            # a different model result because a diagnostic row failed.
            pass

    def _msg_char_count(self) -> int:
        """计算当前消息列表的字符总数（用于压缩前后对比）。"""
        msgs = self._openai_messages if self.use_openai else self._anthropic_messages
        return sum(len(str(m)) for m in msgs)

    @property
    def is_processing(self) -> bool:
        return self._current_task is not None and not self._current_task.done()

    def _build_side_query(self):
        """构建 sideQuery 调用函数 — 用于记忆语义召回。
        向模型发送小 prompt，从记忆列表中选出相关者。
        双后端各有一套实现，返回 awaitable callable。"""
        if self._anthropic_client:
            client = self._anthropic_client
            model = self.model
            async def _sq(system: str, user_message: str) -> str:
                resp = await client.messages.create(
                    model=model, max_tokens=256, system=system,
                    messages=[{"role": "user", "content": user_message}],
                )
                return "".join(b.text for b in resp.content if b.type == "text")
            return _sq
        if self._openai_client:
            client = self._openai_client
            model = self.model
            async def _sq_oai(system: str, user_message: str) -> str:
                resp = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_message},
                    ],
                )
                return resp.choices[0].message.content or "" if resp.choices else ""
            return _sq_oai
        return None

    def abort(self) -> None:
        """中断当前 Agent 循环（Ctrl+C 时调用）。"""
        self._aborted = True
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()

    def set_confirm_fn(self, fn: Callable[[str], Awaitable[bool]]) -> None:
        self.confirm_fn = fn

    def set_plan_approval_fn(self, fn: Callable[[str], Awaitable[dict]]) -> None:
        self._plan_approval_fn = fn

    # ─── Plan Mode 切换 ──────────────────────────────────────
    # 仅在交互式 REPL 中使用（/plan 命令），
    # 手动在普通模式与只读计划模式之间切换。

    def toggle_plan_mode(self) -> str:
        # 退出plan模式
        if self.permission_mode == "plan":
            self.permission_mode = self._pre_plan_mode or "default"
            self._pre_plan_mode = None
            self._plan_file_path = None
            self._system_prompt = self._base_system_prompt
            if self.use_openai and self._openai_messages:
                self._openai_messages[0]["content"] = self._system_prompt
            print_info(f"Exited plan mode → {self.permission_mode} mode")
            return self.permission_mode
        # 进入plan模式
        else:
            self._pre_plan_mode = self.permission_mode
            self.permission_mode = "plan"
            self._plan_file_path = self._generate_plan_file_path()
            self._system_prompt = self._base_system_prompt + self._build_plan_mode_prompt()
            if self.use_openai and self._openai_messages:
                self._openai_messages[0]["content"] = self._system_prompt
            print_info(f"Entered plan mode. Plan file: {self._plan_file_path}")
            return "plan"

    def get_token_usage(self) -> dict:
        return {"input": self.total_input_tokens, "output": self.total_output_tokens}

    # ─── 主入口 ──────────────────────────────────────────────

    async def chat(self, user_message: str) -> None:
        """Agent 主循环入口。路由到对应后端（Anthropic / OpenAI）。"""
        # 首次聊天时惰性连接 MCP 服务器（仅主 Agent）
        if not self._mcp_initialized and not self.is_sub_agent:
            self._mcp_initialized = True
            try:
                await self._mcp_manager.load_and_connect()
                mcp_defs = self._mcp_manager.get_tool_definitions()
                if mcp_defs:
                    self.tools = self.tools + mcp_defs
            except Exception as e:
                print(f"[mcp] Init failed: {e}", flush=True)

        self._aborted = False
        self._ask_count += 1
        # The cursor is run-scoped.  A new user turn starts from a cold
        # canonical prefix, then consumes only suffix ordinals for its loop.
        self._replay_cursor = None
        self._replay_last_rebuild_reason = "new_run"

        self._setup_runtime_facade()

        self._emit_canonical_user_event(user_message)
        await self._emit("chat_start", {"message": user_message, "timestamp": time.time()})

        coro = self._chat_openai(user_message) if self.use_openai else self._chat_anthropic(user_message)
        self._current_task = asyncio.current_task()
        primary_error: BaseException | None = None
        canonical_failure: Exception | None = None
        snapshot_saved = False
        try:
            await coro
        except asyncio.CancelledError as error:
            primary_error = error
            self._aborted = True
            self._runtime_exit_status = "cancelled"
            self._runtime_exit_reason = "asyncio cancellation"
        except Exception as error:
            primary_error = error
            try:
                await self._emit("chat_error", {"error": str(error)})
            except Exception as diagnostic_error:
                canonical_failure = diagnostic_error
            self._runtime_exit_status = "failed"
            self._runtime_exit_reason = str(error)
        finally:
            self._current_task = None
            if self._runtime_guard and not self._runtime_guard.is_terminal:
                final_status = self._runtime_exit_status or (
                    "aborted" if self._aborted else "completed"
                )
                try:
                    self._runtime_guard.finalize(
                        final_status,
                        reason=self._runtime_exit_reason,
                    )
                except Exception as error:
                    canonical_failure = canonical_failure or error
                    self._runtime_exit_status = "failed"
                    self._runtime_exit_reason = f"canonical terminal finalize failed: {error}"
                    print(f"[runtime] terminal finalize failed: {error}", flush=True)
            if self._runtime_emitter:
                try:
                    self._runtime_emitter.flush()
                except Exception as error:
                    canonical_failure = canonical_failure or error
                    self._runtime_exit_status = "failed"
                    self._runtime_exit_reason = f"canonical flush failed: {error}"
                    print(f"[runtime] flush failed: {error}", flush=True)
                finally:
                    if self._runtime_store_owned:
                        try:
                            # The owned SQLite connection is closed below;
                            # materialize the derived snapshot while the
                            # canonical source is still readable.
                            self._auto_save()
                            snapshot_saved = True
                        except Exception as error:
                            canonical_failure = canonical_failure or error
                            self._runtime_exit_status = "failed"
                            self._runtime_exit_reason = f"canonical snapshot failed: {error}"
                        try:
                            self._runtime_emitter.close()
                        except Exception as error:
                            canonical_failure = canonical_failure or error
                            self._runtime_exit_status = "failed"
                            self._runtime_exit_reason = f"canonical close failed: {error}"
                            print(f"[runtime] close failed: {error}", flush=True)
                        self._runtime_emitter = None
                        self._runtime_store = None
                        self._runtime_store_owned = False

        if canonical_failure is not None:
            diagnostic = CanonicalFinalizationError(
                f"canonical finalization failed: {canonical_failure}"
            )
            if primary_error is not None:
                primary_error.add_note(str(diagnostic))
            else:
                raise diagnostic from canonical_failure
        if primary_error is not None and not isinstance(primary_error, asyncio.CancelledError):
            raise primary_error

        if not self.is_sub_agent:
            print_divider()
            if not snapshot_saved:
                self._auto_save()

    # ─── Sub-Agent 入口 ──────────────────────────────────────
    # 子 Agent 通过 run_once 执行单次任务并返回结果，
    # 输出被 _output_buffer 捕获，token 消耗回计到父 Agent。

    async def run_once(self, prompt: str) -> dict:
        self._output_buffer = []
        prev_in = self.total_input_tokens
        prev_out = self.total_output_tokens
        await self.chat(prompt)
        text = "".join(self._output_buffer)
        self._output_buffer = None
        return {
            "text": text,
            "tokens": {
                "input": self.total_input_tokens - prev_in,
                "output": self.total_output_tokens - prev_out,
            },
        }

    # ─── Output helper ────────────────────────────────────────

    def _emit_text(self, text: str) -> None:
        if self._output_buffer is not None:
            self._output_buffer.append(text)
        else:
            print_assistant_text(text)

    # ─── REPL 命令 ───────────────────────────────────────────

    def clear_history(self) -> None:
        """清空对话历史（/clear 命令）。"""
        self._anthropic_messages = []
        self._openai_messages = []
        if self.use_openai:
            self._openai_messages.append({"role": "system", "content": self._system_prompt})
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.last_input_token_count = 0
        print_info("Conversation cleared.")

    def show_cost(self) -> None:
        total = self._get_current_cost_usd()
        budget_info = f" / ${self.max_cost_usd} budget" if self.max_cost_usd else ""
        turn_info = f" | Turns: {self.current_turns}/{self.max_turns}" if self.max_turns else ""
        print_info(f"Tokens: {self.total_input_tokens} in / {self.total_output_tokens} out\n  Estimated cost: ${total:.4f}{budget_info}{turn_info}")

    def _get_current_cost_usd(self) -> float:
        return (self.total_input_tokens / 1_000_000) * 3 + (self.total_output_tokens / 1_000_000) * 15

    def _check_budget(self) -> dict:
        if self.max_cost_usd is not None and self._get_current_cost_usd() >= self.max_cost_usd:
            return {"exceeded": True, "reason": f"Cost limit reached (${self._get_current_cost_usd():.4f} >= ${self.max_cost_usd})"}
        if self.max_turns is not None and self.current_turns >= self.max_turns:
            return {"exceeded": True, "reason": f"Turn limit reached ({self.current_turns} >= {self.max_turns})"}
        return {"exceeded": False}

    async def compact(self) -> None:
        await self._compact_conversation()

    # ─── 会话持久化 ──────────────────────────────────────────
    # 每次 chat 结束自动保存到 ~/.mini-claude/sessions/，
    # --resume 启动时恢复消息历史。

    def restore_session(self, data: dict) -> None:
        meta = data.get("metadata")
        if meta and meta.get("id"):
            # Continuations stay in the same canonical session namespace.
            self.session_id = meta["id"]
            restored_ask_count = meta.get("askCount")
            if restored_ask_count is None:
                # Canonical snapshots use coverage.turnIds.  Recover the
                # largest completed turn so the next chat gets a fresh run.
                turn_numbers = []
                coverage = data.get("coverage") or {}
                for turn_id in coverage.get("turnIds") or []:
                    try:
                        turn_numbers.append(int(str(turn_id).rsplit("-", 1)[-1]))
                    except (TypeError, ValueError):
                        continue
                restored_ask_count = max(turn_numbers, default=len(data.get("runs") or []))
            self._ask_count = int(restored_ask_count or 0)
            # A restored session may contain a sealed terminal run.  Resume
            # the session namespace, but always allocate a fresh run for the
            # next turn instead of reusing that sealed run identity.
            self._runtime_run_id = None
            if self._runtime_parent_context_id is None:
                self._runtime_context_id = f"context:{self.session_id}"
        if data.get("source") != "canonical" and data.get("metadata", {}).get("source") != "canonical":
            raise ValueError("session snapshot is not canonical-derived")
        self.restore_canonical_context(data.get("canonicalMessages", []))

    def restore_canonical_context(self, messages: list[dict[str, Any]]) -> None:
        """Restore provider context from a canonical model projection only."""

        if self.use_openai:
            self._openai_messages = [{"role": "system", "content": self._system_prompt}]
            for message in messages:
                item = dict(message)
                item.pop("runtime_event_id", None)
                self._openai_messages.append(item)
        else:
            self._anthropic_messages = []
            for message in messages:
                role = message.get("role")
                if role == "assistant" and message.get("tool_calls"):
                    blocks = [
                        {
                            "type": "tool_use",
                            "id": call.get("id"),
                            "name": call.get("name"),
                            "input": call.get("arguments", {}),
                        }
                        for call in message["tool_calls"]
                    ]
                    self._anthropic_messages.append({"role": "assistant", "content": blocks})
                elif role in {"user", "assistant"}:
                    self._anthropic_messages.append(
                        {"role": role, "content": message.get("content", "")}
                    )
                elif role == "tool":
                    self._anthropic_messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": message.get("tool_call_id"),
                                    "content": message.get("content", ""),
                                }
                            ],
                        }
                    )
        print_info(f"Canonical context restored ({self._get_message_count()} messages).")

    def project_canonical_model_context(self, *, high_water: int | None = None):
        """Build a read-only replay from the active canonical store."""

        if self._runtime_store is None:
            return None
        result = ModelReplayProjection().build(
            self._runtime_store,
            high_water=high_water,
            context_id=self._runtime_context.context_id if self._runtime_context else self._runtime_context_id,
        )
        return result

    def _refresh_provider_context_from_canonical(self):
        """Refresh provider context from a cold prefix or an event suffix."""

        if self._runtime_store is None or not hasattr(self._runtime_store, "read_event_records"):
            raise RuntimeError("canonical runtime store is not initialized")
        started = time.perf_counter()
        context_id = self._runtime_context.context_id if self._runtime_context else self._runtime_context_id
        cold = self._replay_cursor is None
        reason = "cold_start" if cold else "warm_suffix"
        if cold:
            cursor = IncrementalModelReplayCursor(context_id=context_id)
            pairs = self._runtime_store.read_event_records(context_id=context_id)
            cursor.append(
                EventRecord(ordinal, event) for ordinal, event in pairs
            )
        else:
            cursor = self._replay_cursor
            current_high_water = self._runtime_store.current_high_water
            if current_high_water < cursor.high_water:
                cold = True
                reason = "source_high_water_regressed"
                cursor = IncrementalModelReplayCursor(context_id=context_id)
                pairs = self._runtime_store.read_event_records(context_id=context_id)
                cursor.append(
                    EventRecord(ordinal, event) for ordinal, event in pairs
                )
            elif current_high_water > cursor.high_water:
                try:
                    pairs = self._runtime_store.read_event_records(
                        after_ordinal=cursor.high_water,
                        context_id=context_id,
                    )
                except TypeError:
                    # Keep compatibility with caller-owned test stores that
                    # expose the pre-incremental read signature.
                    reason = "warm_suffix_fallback_full_read"
                    all_pairs = self._runtime_store.read_event_records(context_id=context_id)
                    pairs = [
                        (ordinal, event)
                        for ordinal, event in all_pairs
                        if ordinal > cursor.high_water
                    ]
                try:
                    cursor.append(
                        EventRecord(ordinal, event) for ordinal, event in pairs
                    )
                except IncrementalReplayError:
                    reason = "cursor_invalid"
                    cold = True
                    cursor = IncrementalModelReplayCursor(context_id=context_id)
                    pairs = self._runtime_store.read_event_records(context_id=context_id)
                    cursor.append(
                        EventRecord(ordinal, event) for ordinal, event in pairs
                    )
            else:
                pairs = []

        read_count = len(pairs)
        self._replay_cursor = cursor
        self._replay_events_read += read_count
        self._replay_refresh_count += 1
        self._replay_last_read_count = read_count
        self._replay_last_mode = "cold" if cold else "warm"

        # A committed transition changes the effective prefix.  Reinitialize
        # from the canonical source once at that boundary so stale call-group
        # indexes cannot resurrect pre-transition messages.
        if not cold and cursor.last_append_had_transition:
            reason = "context_transition"
            cursor = IncrementalModelReplayCursor(context_id=context_id)
            all_pairs = self._runtime_store.read_event_records(context_id=context_id)
            cursor.append(
                EventRecord(ordinal, event) for ordinal, event in all_pairs
            )
            self._replay_cursor = cursor
            self._replay_events_read += len(all_pairs)
            self._replay_last_read_count += len(all_pairs)
            self._replay_last_mode = "cold"

        result = cursor.result()
        context = CanonicalModelContextAdapter().build_result(
            result,
            provider="openai" if self.use_openai else "anthropic",
            system_prompt=self._system_prompt if self.use_openai else None,
        )
        errors = [
            diagnostic for diagnostic in context.diagnostics
            if getattr(diagnostic, "severity", None) == "error"
            and getattr(diagnostic, "code", "") == "invalid_context_transition"
        ]
        if errors:
            raise CompactionError(
                "canonical context transition is not verifiable: "
                + "; ".join(str(item.message) for item in errors[:3])
            )
        self._replay_last_duration_ms = int(
            (time.perf_counter() - started) * 1000
        )
        self._replay_last_rebuild_reason = reason
        self._replay_last_source_digest = result.source_digest
        self._replay_last_projection_digest = result.digest
        self._context_epoch = context.context_epoch
        messages = [dict(message) for message in context.messages]
        if self.use_openai:
            self._openai_messages = messages
        else:
            self._anthropic_messages = messages
        return context

    def replay_diagnostics(self) -> dict[str, Any]:
        """Return bounded, content-free replay instrumentation for this run."""

        return {
            "source_high_water": self._replay_cursor.high_water if self._replay_cursor else 0,
            "context_epoch": self._context_epoch,
            "source_digest": self._replay_last_source_digest,
            "projection_digest": self._replay_last_projection_digest,
            "events_read_total": self._replay_events_read,
            "events_read_last_refresh": self._replay_last_read_count,
            "refresh_count": self._replay_refresh_count,
            "projection_duration_ms": self._replay_last_duration_ms,
            "mode": self._replay_last_mode,
            "rebuild_reason": self._replay_last_rebuild_reason,
        }

    def _get_message_count(self) -> int:
        return len(self._openai_messages) if self.use_openai else len(self._anthropic_messages)

    def _auto_save(self) -> None:
        if self._runtime_store is None:
            raise RuntimeError("canonical runtime store is not initialized")
        save_session_v2(self.session_id, self._runtime_store)

    # ─── 自动压缩 ────────────────────────────────────────────
    # 当上下文利用率超过 85% 时自动触发完整压缩（compact）。
    # 压缩用模型生成摘要替代历史消息，保留关键决策和文件路径。

    async def _check_and_compact(self) -> None:
        if self.last_input_token_count > self.effective_window * 0.85:
            print_info("Context window filling up, compacting conversation...")
            await self._emit("compaction", {"tier": 4})
            await self._compact_conversation()

    async def _compact_conversation(self) -> None:
        summary_text = await (
            self._compact_openai() if self.use_openai else self._compact_anthropic()
        )
        if summary_text is not None:
            checkpoint = self._write_compaction_checkpoint(summary_text)
            if checkpoint is not None and self._runtime_store is not None:
                self._refresh_provider_context_from_canonical()
        else:
            return
        print_info("Conversation compacted.")

    def _compaction_context_messages(self) -> list[dict[str, Any]]:
        """Return a complete, source-preserving neutral compaction tail."""

        if self._pending_compaction_tail is not None:
            return [dict(message) for message in self._pending_compaction_tail]

        projection = self.project_canonical_model_context()
        if projection is not None and projection.messages:
            source = [dict(message) for message in projection.messages]
        else:
            source = self._neutralize_working_messages_for_compaction()

        groups: list[list[dict[str, Any]]] = []
        index = 0
        while index < len(source):
            message = source[index]
            role = message.get("role")
            if role == "tool":
                raise CompactionError("cannot compact an orphaned tool result")
            group = [message]
            if role == "assistant" and message.get("tool_calls"):
                expected = {
                    str(call.get("id"))
                    for call in message.get("tool_calls", [])
                    if isinstance(call, Mapping) and call.get("id")
                }
                index += 1
                while index < len(source) and source[index].get("role") == "tool":
                    result = source[index]
                    if result.get("tool_call_id") not in expected:
                        raise CompactionError("tool result does not belong to its call group")
                    group.append(result)
                    index += 1
                actual = {
                    str(item.get("tool_call_id"))
                    for item in group[1:]
                    if item.get("tool_call_id")
                }
                if actual != expected:
                    raise CompactionError("cannot compact an incomplete tool-call group")
                groups.append(group)
                continue
            groups.append(group)
            index += 1

        if len(groups) <= 1:
            return [dict(message) for message in source]
        tail_count = min(8, len(groups) - 1)
        tail = [message for group in groups[-tail_count:] for message in group]
        self._pending_compaction_tail = [dict(message) for message in tail]
        self._pending_compaction_summary_source = [
            dict(message) for group in groups[:-tail_count] for message in group
        ]
        return [dict(message) for message in tail]

    def _neutralize_working_messages_for_compaction(self) -> list[dict[str, Any]]:
        """Best-effort fallback for callers that have no canonical store."""

        source = self._openai_messages if self.use_openai else self._anthropic_messages
        result: list[dict[str, Any]] = []
        for message in source:
            role = message.get("role")
            if role == "system":
                continue
            if role == "assistant" and message.get("tool_calls"):
                calls = []
                for call in message["tool_calls"]:
                    function = call.get("function", call)
                    arguments = function.get("arguments", {})
                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except json.JSONDecodeError:
                            pass
                    calls.append({
                        "id": call.get("id"),
                        "name": function.get("name"),
                        "arguments": arguments,
                    })
                result.append({"role": "assistant", "tool_calls": calls})
            elif role in {"user", "assistant", "tool"}:
                result.append({
                    key: value
                    for key, value in message.items()
                    if key in {"role", "content", "tool_call_id", "runtime_event_id"}
                })
        return result

    def _compaction_summary_messages(self) -> list[dict[str, Any]]:
        if self._pending_compaction_summary_source is None:
            self._compaction_context_messages()
        return [dict(message) for message in (self._pending_compaction_summary_source or [])]

    def _provider_messages_for_neutral(
        self, messages: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], ...]:
        result = ModelReplayResult(
            projection_version="projection-v1",
            schema_version=1,
            high_water=0,
            source_digest="compaction-source",
            digest="compaction-context",
            messages=tuple(messages),
            partial_count=0,
            diagnostics=(),
            context_epoch=self._context_epoch,
            context_id=self._runtime_context.context_id if self._runtime_context else self._runtime_context_id,
        )
        return CanonicalModelContextAdapter().build_result(
            result,
            provider="openai" if self.use_openai else "anthropic",
            system_prompt=None,
        ).messages

    def _write_compaction_checkpoint(self, summary_text: str) -> CompactionCheckpoint | None:
        """Persist a checkpoint and a reset marker after summarization succeeds."""

        if self._runtime_store is None or self._runtime_context is None:
            return None
        bounded_summary = str(summary_text)[:8192]
        retained_tail = self._compaction_context_messages()
        context_messages = [
            {"role": "user", "content": f"[Previous conversation summary]\n{bounded_summary}"},
            {
                "role": "assistant",
                "content": "Understood. I have the context from our previous conversation. How can I continue helping?",
            },
            *retained_tail,
        ]
        try:
            high_water = self._runtime_store.current_high_water
            context_id = self._runtime_context.context_id
            active_projection = ModelReplayProjection().build(
                self._runtime_store,
                high_water=high_water,
                context_id=context_id,
            )
            checkpoint = CompactionCheckpointBuilder().build(
                self._runtime_store,
                high_water=high_water,
                context_id=context_id,
                summary={
                    "text": bounded_summary,
                    "provider": "openai" if self.use_openai else "anthropic",
                    "context_message_count": len(context_messages),
                },
            )
            next_epoch = f"context:{checkpoint.checkpoint_id}"
            transition = build_context_transition(
                source_high_water=checkpoint.source_high_water,
                source_digest=checkpoint.source_digest,
                projection_version=checkpoint.projection_version,
                policy_version="compression-policy-v1",
                context_epoch=next_epoch,
                reason="full_compaction",
                replacements=[],
                effective_context=context_messages,
                context_id=context_id,
            )
            if self._runtime_emitter is None:
                return None
            transition_event = RuntimeEvent.create(
                self._runtime_context,
                role="system",
                author="system",
                actions={
                    "compaction": {
                        "checkpoint_id": checkpoint.checkpoint_id,
                        "source_high_water": checkpoint.source_high_water,
                        "source_digest": checkpoint.source_digest,
                        "reset_model_context": True,
                        "summary": bounded_summary,
                        "context_messages": context_messages,
                        "context_epoch": next_epoch,
                    },
                    "context_transition": transition.to_dict(),
                },
                refs={"checkpoint_id": checkpoint.checkpoint_id},
                ts=int(time.time() * 1000),
                metadata={
                    "lifecycle": "compaction_checkpoint",
                    "checkpoint_id": checkpoint.checkpoint_id,
                    "context_epoch": next_epoch,
                },
            )
            prepared = self._runtime_emitter.prepare(transition_event)
            prepared_actions = prepared.actions or {}
            validate_transition_candidate(
                active_projection.messages,
                ContextTransition.from_value(
                    prepared_actions["context_transition"]
                ),
                source_high_water=checkpoint.source_high_water,
                source_digest=checkpoint.source_digest,
                expected_projection_version=checkpoint.projection_version,
                expected_policy_version="compression-policy-v1",
                current_context_epoch=active_projection.context_epoch,
                context_id=context_id,
                reset_context=(prepared_actions.get("compaction") or {}).get(
                    "context_messages", []
                ),
            )
            if hasattr(self._runtime_store, "append_compaction_transition"):
                self._runtime_store.append_compaction_transition(checkpoint, prepared)
            else:
                self._runtime_store.write_compaction_checkpoint(checkpoint)
                self._runtime_emitter.emit(prepared)
            self._context_epoch = next_epoch
            self._pending_compaction_tail = None
            self._pending_compaction_summary_source = None
            return checkpoint
        except Exception as error:
            self._pending_compaction_tail = None
            self._pending_compaction_summary_source = None
            self._runtime_exit_status = "failed"
            self._runtime_exit_reason = f"compaction checkpoint failed: {error}"
            if isinstance(error, CompactionError):
                raise
            raise CompactionError(f"compaction checkpoint failed: {error}") from error

    async def _compact_anthropic(self) -> str | None:
        self._compaction_context_messages()
        summary_source = self._compaction_summary_messages()
        if not summary_source:
            return None
        summary_resp = await self._anthropic_client.messages.create(
            model=self.model,
            max_tokens=2048,
            system="You are a conversation summarizer. Be concise but preserve important details.",
            messages=[
                *self._provider_messages_for_neutral(summary_source),
                {"role": "user", "content": "Summarize the conversation so far in a concise paragraph, preserving key decisions, file paths, and context needed to continue the work."},
            ],
        )
        summary_text = summary_resp.content[0].text if summary_resp.content and summary_resp.content[0].type == "text" else "No summary available."
        self.last_input_token_count = 0
        return summary_text

    async def _compact_openai(self) -> str | None:
        self._compaction_context_messages()
        summary_source = self._compaction_summary_messages()
        if not summary_source:
            return None
        summary_resp = await self._openai_client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "You are a conversation summarizer. Be concise but preserve important details."},
                *self._provider_messages_for_neutral(summary_source),
                {"role": "user", "content": "Summarize the conversation so far in a concise paragraph, preserving key decisions, file paths, and context needed to continue the work."},
            ],
        )
        summary_text = summary_resp.choices[0].message.content or "No summary available."
        self.last_input_token_count = 0
        return summary_text

    # ─── 多层压缩管线 ────────────────────────────────────────
    # 每轮 API 调用前执行以下 3 层（Tier 1-3）：
    #   Tier 1: budget 截断 — 超出预算的大工具结果被头尾截断
    #   Tier 2: stale snip — 利用率 > 60% 时裁剪旧工具结果
    #   Tier 3: microcompact — 空闲 > 5 分钟时清除旧结果
    # Tier 4 (auto-compact) 在每轮 API 调用后检查触发。

    def _run_compression_pipeline(self) -> None:
        import copy

        previous_messages = copy.deepcopy(
            self._openai_messages if self.use_openai else self._anthropic_messages
        )
        before = self._capture_compression_tool_results()
        try:
            if self.use_openai:
                self._budget_tool_results_openai()
                self._snip_stale_results_openai()
                self._microcompact_openai()
            else:
                self._budget_tool_results_anthropic()
                self._snip_stale_results_anthropic()
                self._microcompact_anthropic()
            self._persist_compression_replacements(before)
        except Exception:
            if self.use_openai:
                self._openai_messages = previous_messages
            else:
                self._anthropic_messages = previous_messages
            raise

    def _compression_tool_result_entries(
        self,
    ) -> list[tuple[str, str, str | list[Any]]]:
        """Pair current provider-visible results with neutral source IDs."""

        if self._runtime_store is None and self._replay_cursor is None:
            return []
        replay = (
            self._replay_cursor.result()
            if self._replay_cursor is not None
            else self.project_canonical_model_context()
        )
        if replay is None:
            return []
        source_keys = [
            (message.get("runtime_event_id"), message.get("tool_call_id"))
            for message in replay.messages
            if message.get("role") == "tool"
        ]
        working: list[tuple[Any, Any]] = []
        if self.use_openai:
            working = [
                (message.get("tool_call_id"), message.get("content"))
                for message in self._openai_messages
                if message.get("role") == "tool"
            ]
        else:
            for message in self._anthropic_messages:
                if message.get("role") != "user" or not isinstance(message.get("content"), list):
                    continue
                for block in message["content"]:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        working.append((block.get("tool_use_id"), block.get("content")))
        entries: list[tuple[str, str, str | list[Any]]] = []
        for (event_id, source_call_id), (working_call_id, content) in zip(source_keys, working):
            if (
                isinstance(event_id, str)
                and event_id
                and isinstance(source_call_id, str)
                and source_call_id == working_call_id
                and isinstance(content, (str, list))
            ):
                entries.append((event_id, source_call_id, content))
        return entries

    def _capture_compression_tool_results(
        self,
    ) -> dict[str, tuple[str, str | list[Any]]]:
        return {
            event_id: (call_id, content)
            for event_id, call_id, content in self._compression_tool_result_entries()
        }

    def _current_compression_tool_results(self) -> dict[str, str | list[Any]]:
        return {
            event_id: content
            for event_id, _call_id, content in self._compression_tool_result_entries()
        }

    def _persist_compression_replacements(
        self, before: dict[str, tuple[str, str | list[Any]]]
    ) -> None:
        if not before or self._runtime_store is None or self._runtime_emitter is None or self._runtime_context is None:
            return
        current = self._current_compression_tool_results()
        replacements = [
            ContextReplacement(
                target_event_id=event_id,
                target_call_id=call_id,
                replacement=current[event_id],
                reason="lightweight_compression",
            )
            for event_id, (call_id, old_value) in before.items()
            if event_id in current and current[event_id] != old_value
        ]
        if not replacements:
            return
        source_high_water = self._runtime_store.current_high_water
        replay = ModelReplayProjection().build(
            self._runtime_store,
            high_water=source_high_water,
            context_id=self._runtime_context.context_id,
        )
        effective_context = {
            "replacements": [item.to_dict() for item in replacements]
        }
        transition = build_context_transition(
            source_high_water=source_high_water,
            source_digest=replay.source_digest,
            projection_version=replay.projection_version,
            policy_version="compression-policy-v1",
            context_epoch=self._context_epoch,
            reason="lightweight_compression",
            replacements=replacements,
            effective_context=effective_context,
            context_id=self._runtime_context.context_id,
        )
        event = RuntimeEvent.create(
            self._runtime_context,
            role="system",
            author="system",
            actions={"context_transition": transition.to_dict()},
            refs={"context_epoch": self._context_epoch},
            ts=int(time.time() * 1000),
            metadata={
                "lifecycle": "context_transition",
                "context_epoch": self._context_epoch,
                "reason": "lightweight_compression",
            },
        )
        try:
            prepared = self._runtime_emitter.prepare(event)
            prepared_transition = ContextTransition.from_value(
                (prepared.actions or {})["context_transition"]
            )
            validate_transition_candidate(
                replay.messages,
                prepared_transition,
                source_high_water=source_high_water,
                source_digest=replay.source_digest,
                expected_projection_version=replay.projection_version,
                expected_policy_version="compression-policy-v1",
                current_context_epoch=self._context_epoch,
                context_id=self._runtime_context.context_id,
            )
            if hasattr(self._runtime_store, "append_context_transition"):
                self._runtime_store.append_context_transition(
                    prepared,
                    source_high_water=source_high_water,
                    source_digest=replay.source_digest,
                    context_id=self._runtime_context.context_id,
                )
            else:
                self._runtime_emitter.emit(prepared)
        except Exception as error:
            self._runtime_exit_status = "failed"
            self._runtime_exit_reason = f"context transition failed: {error}"
            raise CompactionError(self._runtime_exit_reason) from error

    # Tier 1: 预算截断 — 当利用率 > 50% 时，将超长工具结果头尾保留、中间截断
    def _budget_tool_results_anthropic(self) -> None:
        utilization = self.last_input_token_count / self.effective_window if self.effective_window else 0
        if utilization < 0.5:
            return
        budget = 15000 if utilization > 0.7 else 30000
        for msg in self._anthropic_messages:
            if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
                continue
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("content"), str) and len(block["content"]) > budget:
                    keep = (budget - 80) // 2
                    block["content"] = block["content"][:keep] + f"\n\n[... budgeted: {len(block['content']) - keep * 2} chars truncated ...]\n\n" + block["content"][-keep:]

    def _budget_tool_results_openai(self) -> None:
        utilization = self.last_input_token_count / self.effective_window if self.effective_window else 0
        if utilization < 0.5:
            return
        budget = 15000 if utilization > 0.7 else 30000
        for msg in self._openai_messages:
            if msg.get("role") == "tool" and isinstance(msg.get("content"), str) and len(msg["content"]) > budget:
                keep = (budget - 80) // 2
                msg["content"] = msg["content"][:keep] + f"\n\n[... budgeted: {len(msg['content']) - keep * 2} chars truncated ...]\n\n" + msg["content"][-keep:]

    # Tier 2: 过期剪除 — 利用率 > 60% 时，裁剪旧的只读工具结果
    # 对同一文件多次读取只保留最近一次，只保留最后 KEEP_RECENT_RESULTS 个结果
    def _snip_stale_results_anthropic(self) -> None:
        utilization = self.last_input_token_count / self.effective_window if self.effective_window else 0
        if utilization < SNIP_THRESHOLD:
            return

        results = []
        for mi, msg in enumerate(self._anthropic_messages):
            if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
                continue
            for bi, block in enumerate(msg["content"]):
                if isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("content"), str) and block["content"] != SNIP_PLACEHOLDER:
                    tool_use_id = block.get("tool_use_id")
                    tool_info = self._find_tool_use_by_id(tool_use_id)
                    if tool_info and tool_info["name"] in SNIPPABLE_TOOLS:
                        results.append({"mi": mi, "bi": bi, "name": tool_info["name"], "file_path": tool_info.get("input", {}).get("file_path")})

        if len(results) <= KEEP_RECENT_RESULTS:
            return

        to_snip = set()
        seen_files: dict[str, list[int]] = {}
        for i, r in enumerate(results):
            if r["name"] == "read_file" and r.get("file_path"):
                seen_files.setdefault(r["file_path"], []).append(i)

        for indices in seen_files.values():
            if len(indices) > 1:
                for j in indices[:-1]:
                    to_snip.add(j)

        snip_before = len(results) - KEEP_RECENT_RESULTS
        for i in range(snip_before):
            to_snip.add(i)

        for idx in to_snip:
            r = results[idx]
            self._anthropic_messages[r["mi"]]["content"][r["bi"]]["content"] = SNIP_PLACEHOLDER

    def _snip_stale_results_openai(self) -> None:
        utilization = self.last_input_token_count / self.effective_window if self.effective_window else 0
        if utilization < SNIP_THRESHOLD:
            return
        tool_msgs = []
        for i, msg in enumerate(self._openai_messages):
            if msg.get("role") == "tool" and isinstance(msg.get("content"), str) and msg["content"] != SNIP_PLACEHOLDER:
                tool_msgs.append(i)
        if len(tool_msgs) <= KEEP_RECENT_RESULTS:
            return
        snip_count = len(tool_msgs) - KEEP_RECENT_RESULTS
        for i in range(snip_count):
            self._openai_messages[tool_msgs[i]]["content"] = SNIP_PLACEHOLDER

    # Tier 3: 微压缩 — 空闲时间 > 5 分钟时，清除旧工具结果（标记为 [Old result cleared]）
    def _microcompact_anthropic(self) -> None:
        if not self.last_api_call_time or (time.time() - self.last_api_call_time) < MICROCOMPACT_IDLE_S:
            return
        all_results = []
        for mi, msg in enumerate(self._anthropic_messages):
            if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
                continue
            for bi, block in enumerate(msg["content"]):
                if isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("content"), str) and block["content"] not in (SNIP_PLACEHOLDER, "[Old result cleared]"):
                    all_results.append((mi, bi))
        clear_count = len(all_results) - KEEP_RECENT_RESULTS
        for i in range(max(0, clear_count)):
            mi, bi = all_results[i]
            self._anthropic_messages[mi]["content"][bi]["content"] = "[Old result cleared]"

    def _microcompact_openai(self) -> None:
        if not self.last_api_call_time or (time.time() - self.last_api_call_time) < MICROCOMPACT_IDLE_S:
            return
        tool_msgs = []
        for i, msg in enumerate(self._openai_messages):
            if msg.get("role") == "tool" and isinstance(msg.get("content"), str) and msg["content"] not in (SNIP_PLACEHOLDER, "[Old result cleared]"):
                tool_msgs.append(i)
        clear_count = len(tool_msgs) - KEEP_RECENT_RESULTS
        for i in range(max(0, clear_count)):
            self._openai_messages[tool_msgs[i]]["content"] = "[Old result cleared]"

    def _find_tool_use_by_id(self, tool_use_id: str) -> dict | None:
        """根据 tool_use_id 在 Anthropic 消息历史中反向查找对应的工具调用信息。"""
        for msg in self._anthropic_messages:
            if msg.get("role") != "assistant" or not isinstance(msg.get("content"), list):
                continue
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id") == tool_use_id:
                    return {"name": block["name"], "input": block.get("input", {})}
        return None

    # ─── 工具执行路由 ──────────────────────────────────────
    # 统一分发：plan_mode / agent / skill / MCP / 标准工具。
    # agent 和 skill 在此处理以避免循环依赖（tools.py 不引用 agent.py）。

    async def _execute_tool_call(self, name: str, inp: dict) -> str:
        if name in ("enter_plan_mode", "exit_plan_mode"):
            return await self._execute_plan_mode_tool(name)
        if name == "agent":
            return await self._execute_agent_tool(inp)
        if name == "skill":
            return await self._execute_skill_tool(inp)
        # Route MCP tool calls to the MCP manager
        if self._mcp_manager.is_mcp_tool(name):
            return await self._mcp_manager.call_tool(name, inp)
        return await execute_tool(name, inp, self._read_file_state)

    # ─── Skill 执行（支持 inline / fork 双模式）─────────────
    # inline: 返回解析后的 prompt，注入当前对话
    # fork: 创建独立 sub-agent 执行，输出返回当前对话

    async def _execute_skill_tool(self, inp: dict) -> str:
        from .skills import execute_skill
        result = execute_skill(inp.get("skill_name", ""), inp.get("args", ""))
        if not result:
            return f"Unknown skill: {inp.get('skill_name', '')}"

        if result["context"] == "fork":
            tools = (
                [t for t in self.tools if t["name"] in result["allowed_tools"]]
                if result.get("allowed_tools")
                else [t for t in self.tools if t["name"] != "agent"]
            )
            skill_name = inp.get("skill_name", "")
            self._record_sub_agent_event(
                name=skill_name,
                agent_type="skill-fork",
                prompt=(inp.get("args") or ""),
            )
            print_sub_agent_start("skill-fork", skill_name)
            sub_agent = Agent(
                model=self.model,
                api_base=str(self._openai_client.base_url) if self.use_openai and self._openai_client else None,
                thinking_effort=self.thinking_effort,
                custom_system_prompt=result["prompt"],
                custom_tools=tools,
                is_sub_agent=True,
                permission_mode="plan" if self.permission_mode == "plan" else "bypassPermissions",
                runtime_store=self._runtime_store,
                runtime_sink=self._runtime_sink,
                runtime_parent_run_id=self._runtime_context.run_id if self._runtime_context else None,
                runtime_run_id=f"run-{self.session_id}-skill-{skill_name}-{uuid.uuid4().hex[:8]}",
                runtime_session_id=(
                    self._runtime_context.session_id
                    if self._runtime_context is not None
                    else self.session_id
                ),
                runtime_context_id=self._identity_factory.new("context"),
                runtime_parent_context_id=(
                    self._runtime_context.context_id
                    if self._runtime_context is not None
                    else None
                ),
                artifact_archive=self._artifact_archive,
                llm_capture_policy=self._llm_capture_policy,
            )
            try:
                sub_result = await sub_agent.run_once(inp.get("args") or "Execute this skill task.")
                self.total_input_tokens += sub_result["tokens"]["input"]
                self.total_output_tokens += sub_result["tokens"]["output"]
                print_sub_agent_end("skill-fork", inp.get("skill_name", ""))
                return sub_result["text"] or "(Skill produced no output)"
            except Exception as e:
                print_sub_agent_end("skill-fork", inp.get("skill_name", ""))
                return f"Skill fork error: {e}"
        # inline mode
        return f'[Skill "{inp.get("skill_name", "")}" activated]\n\n{result["prompt"]}'

    # ─── Plan Mode 辅助方法 ──────────────────────────────────

    def _generate_plan_file_path(self) -> str:
        """生成计划文件路径：~/.claude/plans/plan-{session_id}.md"""
        d = Path.home() / ".claude" / "plans"
        d.mkdir(parents=True, exist_ok=True)
        return str(d / f"plan-{self.session_id}.md")

    def _build_plan_mode_prompt(self) -> str:
        """构建 Plan Mode 的 system prompt 扩展：只读限制 + 计划文件路径。"""
        return f"""

# Plan Mode Active

Plan mode is active. You MUST NOT make any edits (except the plan file below), run non-readonly tools, or make any changes to the system.

## Plan File: {self._plan_file_path}
Write your plan incrementally to this file using write_file or edit_file. This is the ONLY file you are allowed to edit.

## Workflow
1. **Explore**: Read code to understand the task. Use read_file, list_files, grep_search.
2. **Design**: Design your implementation approach. Use the agent tool with type="plan" if the task is complex.
3. **Write Plan**: Write a structured plan to the plan file including:
   - **Context**: Why this change is needed
   - **Steps**: Implementation steps with critical file paths
   - **Verification**: How to test the changes
4. **Exit**: Call exit_plan_mode when your plan is ready for user review.

IMPORTANT: When your plan is complete, you MUST call exit_plan_mode. Do NOT ask the user to approve — exit_plan_mode handles that."""

    async def _execute_plan_mode_tool(self, name: str) -> str:
        """enter_plan_mode / exit_plan_mode 工具的实现。
        exit_plan_mode 包含审批流程：4 选项（clear-execute / execute / manual / keep-planning）。"""
        if name == "enter_plan_mode":
            if self.permission_mode == "plan":
                return "Already in plan mode."
            self._pre_plan_mode = self.permission_mode
            self.permission_mode = "plan"
            self._plan_file_path = self._generate_plan_file_path()
            self._system_prompt = self._base_system_prompt + self._build_plan_mode_prompt()
            if self.use_openai and self._openai_messages:
                self._openai_messages[0]["content"] = self._system_prompt
            print_info("Entered plan mode (read-only). Plan file: " + self._plan_file_path)
            return f"Entered plan mode. You are now in read-only mode.\n\nYour plan file: {self._plan_file_path}\nWrite your plan to this file. This is the only file you can edit.\n\nWhen your plan is complete, call exit_plan_mode."

        if name == "exit_plan_mode":
            if self.permission_mode != "plan":
                return "Not in plan mode."
            plan_content = "(No plan file found)"
            if self._plan_file_path and Path(self._plan_file_path).exists():
                plan_content = Path(self._plan_file_path).read_text()

            # Interactive approval flow
            if self._plan_approval_fn:
                result = await self._plan_approval_fn(plan_content)
                choice = result.get("choice", "manual-execute")

                if choice == "keep-planning":
                    feedback = result.get("feedback") or "Please revise the plan."
                    return (
                        f"User rejected the plan and wants to keep planning.\n\n"
                        f"User feedback: {feedback}\n\n"
                        f"Please revise your plan based on this feedback. When done, call exit_plan_mode again."
                    )

                # User approved — determine target mode
                if choice == "clear-and-execute":
                    target_mode = "acceptEdits"
                elif choice == "execute":
                    target_mode = "acceptEdits"
                else:  # manual-execute
                    target_mode = self._pre_plan_mode or "default"

                # Exit plan mode
                self.permission_mode = target_mode
                self._pre_plan_mode = None
                saved_plan_path = self._plan_file_path
                self._plan_file_path = None
                self._system_prompt = self._base_system_prompt
                if self.use_openai and self._openai_messages:
                    self._openai_messages[0]["content"] = self._system_prompt

                if choice == "clear-and-execute":
                    self._clear_history_keep_system()
                    self._context_cleared = True
                    print_info(f"Plan approved. Context cleared, executing in {target_mode} mode.")
                    return (
                        f"User approved the plan. Context was cleared. Permission mode: {target_mode}\n\n"
                        f"Plan file: {saved_plan_path}\n\n"
                        f"## Approved Plan:\n{plan_content}\n\n"
                        f"Proceed with implementation."
                    )

                print_info(f"Plan approved. Executing in {target_mode} mode.")
                return (
                    f"User approved the plan. Permission mode: {target_mode}\n\n"
                    f"## Approved Plan:\n{plan_content}\n\n"
                    f"Proceed with implementation."
                )

            # Fallback: no approval function (e.g. sub-agents)
            self.permission_mode = self._pre_plan_mode or "default"
            self._pre_plan_mode = None
            self._plan_file_path = None
            self._system_prompt = self._base_system_prompt
            if self.use_openai and self._openai_messages:
                self._openai_messages[0]["content"] = self._system_prompt
            print_info("Exited plan mode. Restored to " + self.permission_mode + " mode.")
            return f"Exited plan mode. Permission mode restored to: {self.permission_mode}\n\n## Your Plan:\n{plan_content}"

        return f"Unknown plan mode tool: {name}"

    def _clear_history_keep_system(self) -> None:
        """清空历史但保留 system prompt（Plan Mode 'clear-and-execute' 选项用）。"""
        self._anthropic_messages = []
        self._openai_messages = []
        if self.use_openai:
            self._openai_messages.append({"role": "system", "content": self._system_prompt})
        self.last_input_token_count = 0

    async def _execute_agent_tool(self, inp: dict) -> str:
        """执行 agent 工具 — fork-return 模式启动子 Agent。
        子 Agent 使用独立上下文运行，token 消耗回计到父 Agent。"""
        agent_type = inp.get("type", "general")
        description = inp.get("description", "sub-agent task")
        prompt = inp.get("prompt", "")

        print_sub_agent_start(agent_type, description)

        self._record_sub_agent_event(
            name=description,
            agent_type=agent_type,
            prompt=prompt,
        )

        config = get_sub_agent_config(agent_type)
        sub_agent = Agent(
            model=self.model,
            api_base=str(self._openai_client.base_url) if self.use_openai and self._openai_client else None,
            thinking_effort=self.thinking_effort,
            custom_system_prompt=config["system_prompt"],
            custom_tools=config["tools"],
            is_sub_agent=True,
            permission_mode="plan" if self.permission_mode == "plan" else "bypassPermissions",
                runtime_store=self._runtime_store,
                runtime_sink=self._runtime_sink,
                runtime_parent_run_id=self._runtime_context.run_id if self._runtime_context else None,
                runtime_run_id=f"run-{self.session_id}-{agent_type}-{uuid.uuid4().hex[:8]}",
                runtime_session_id=(
                    self._runtime_context.session_id
                    if self._runtime_context is not None
                    else self.session_id
                ),
                runtime_context_id=self._identity_factory.new("context"),
                runtime_parent_context_id=(
                    self._runtime_context.context_id
                    if self._runtime_context is not None
                    else None
                ),
                artifact_archive=self._artifact_archive,
                llm_capture_policy=self._llm_capture_policy,
        )

        try:
            result = await sub_agent.run_once(prompt)
            self.total_input_tokens += result["tokens"]["input"]
            self.total_output_tokens += result["tokens"]["output"]
            print_sub_agent_end(agent_type, description)
            return result["text"] or "(Sub-agent produced no output)"
        except Exception as e:
            print_sub_agent_end(agent_type, description)
            return f"Sub-agent error: {e}"

    # ─── Anthropic 后端 ─────────────────────────────────────
    # 流式调用 Anthropic API；工具统一在完整 final boundary 后执行。

    async def _chat_anthropic(self, user_message: str) -> None:
        self._anthropic_messages.append({"role": "user", "content": user_message})

        # 启动异步记忆预取（非阻塞，每个用户轮次触发一次）
        memory_prefetch: MemoryPrefetch | None = None
        if not self.is_sub_agent:
            sq = self._build_side_query()
            if sq:
                memory_prefetch = start_memory_prefetch(
                    user_message, sq,
                    self._already_surfaced_memories, self._session_memory_bytes,
                )

        while True:
            if self._aborted:
                break

            self._refresh_provider_context_from_canonical()

            _pre_size = self._msg_char_count()
            self._run_compression_pipeline()
            _post_size = self._msg_char_count()
            if _post_size < _pre_size:
                utilization = self.last_input_token_count / self.effective_window if self.effective_window else 0
                tier = 1 if utilization > 0.5 else 2 if utilization > SNIP_THRESHOLD else 3
                await self._emit("compaction", {"tier": tier})

            # 本轮 index
            turn_index = self.current_turns + 1
            await self._emit("turn_start", {"turn_index": turn_index})

            # 消费记忆预取结果（非阻塞轮询，zero-wait）。
            if memory_prefetch and memory_prefetch.settled and not memory_prefetch.consumed:
                memories = memory_prefetch.task.result()
                if memories:
                    self._persist_memory_context_event(memories)
                    self._refresh_provider_context_from_canonical()
                    for m in memories:
                        self._already_surfaced_memories.add(m.path)
                        self._session_memory_bytes += len(m.content.encode())
                memory_prefetch.consumed = True

            if not self.is_sub_agent:
                start_spinner()

            request_id = uuid.uuid4().hex
            self._current_request_id = request_id
            api_start = time.time()
            self._start_runtime_model_call(request_id, "anthropic", {"messages": self._anthropic_messages})
            try:
                response = await self._call_anthropic_stream()
            except Exception as error:
                self._record_runtime_model_error(error)
                raise

            if not self.is_sub_agent:
                stop_spinner()

            self.last_api_call_time = time.time()
            self.total_input_tokens += response.usage.input_tokens
            self.total_output_tokens += response.usage.output_tokens
            self.last_input_token_count = response.usage.input_tokens

            tool_uses = [
                block for block in response.content if getattr(block, "type", None) == "tool_use"
            ]

            try:
                normalized_blocks = self._normalize_anthropic_response_blocks(response.content)
            except ProviderContentNormalizationError as error:
                self._record_runtime_model_error(error)
                raise

            if self._runtime_recorder:
                try:
                    for block_index, block in enumerate(normalized_blocks):
                        if block["type"] == "text":
                            text = _normalize_provider_text(
                                block.get("text"),
                                provider="anthropic",
                                block_kind="text",
                                block_index=block_index,
                            )
                            self._runtime_recorder.final_text(text)
                        elif block["type"] == "thinking":
                            thinking = _normalize_provider_text(
                                block.get("thinking"),
                                provider="anthropic",
                                block_kind="thinking",
                                block_index=block_index,
                            )
                            self._runtime_recorder.final_thinking(
                                thinking, signature=block.get("signature")
                            )
                        elif block["type"] == "tool_use":
                            self._runtime_recorder.final_tool_call(
                                block["id"], block["name"], block.get("input", {})
                            )
                except ProviderContentNormalizationError as error:
                    self._record_runtime_model_error(error)
                    raise

            # ★ 发射 turn_end 事件
            cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0
            cache_create = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
            finish = "end_turn" if tool_uses else "stop"
            if self._runtime_recorder:
                self._runtime_recorder.finish(
                    finish,
                    usage={
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                        "cache_read_tokens": cache_read,
                        "cache_create_tokens": cache_create,
                    },
                    latency_ms=int((time.time() - api_start) * 1000),
                )
            await self._emit("turn_end", {
                "turn_index": turn_index,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "cache_read_tokens": cache_read,
                "cache_create_tokens": cache_create,
                "finish_reason": finish,
            })

            if self._llm_capture_manager:
                latency_ms = int((time.time() - api_start) * 1000)
                response_dict = {"content": normalized_blocks}
                usage_dict = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
                self._capture_llm(
                    request_id=request_id,
                    messages=self._anthropic_messages,
                    response=response_dict,
                    usage=usage_dict,
                    latency_ms=latency_ms,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    cache_read_tokens=cache_read,
                    finish_reason=finish,
                )

            message = {
                "role": "assistant",
                "content": normalized_blocks,
            }

            self._anthropic_messages.append(message)

            if not tool_uses:
                if not self.is_sub_agent:
                    print_cost(self.total_input_tokens, self.total_output_tokens)
                break

            self.current_turns += 1
            budget = self._check_budget()
            if budget["exceeded"]:
                print_info(f"Budget exceeded: {budget['reason']}")
                self._record_budget_exceeded(budget["reason"])
                break

            # Process complete tool calls only after the model final boundary.
            tool_results: list[dict] = []
            context_break = False
            for tu in tool_uses:
                if context_break or self._aborted:
                    break
                inp = dict(tu.input) if hasattr(tu.input, 'items') else tu.input
                print_tool_call(tu.name, inp)

                # 非提前启动工具的权限检查
                perm = check_permission(tu.name, inp, self.permission_mode, self._plan_file_path)
                if perm["action"] == "deny":
                    print_info(f"Denied: {perm.get('message', '')}")
                    raw, success, executed = await self._run_durable_tool(
                        request_id=request_id, call_id=tu.id, name=tu.name, inp=inp,
                        permission={"decision": "deny", "reason": perm.get("message", "")},
                    )
                    res = materialize_tool_result(raw, provider="anthropic")
                    tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": res})
                    continue
                if perm["action"] == "confirm" and perm.get("message") and perm["message"] not in self._confirmed_paths:
                    confirmed = await self._confirm_dangerous(perm["message"])
                    if not confirmed:
                        raw, success, executed = await self._run_durable_tool(
                            request_id=request_id, call_id=tu.id, name=tu.name, inp=inp,
                            permission={"decision": "deny", "reason": perm["message"]},
                        )
                        res = materialize_tool_result(raw, provider="anthropic")
                        tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": res})
                        continue
                    self._confirmed_paths.add(perm["message"])

                t0 = time.time()
                raw, success, executed = await self._run_durable_tool(
                    request_id=request_id, call_id=tu.id, name=tu.name, inp=inp,
                    permission={"decision": "allow", "reason": "permission granted"},
                )
                tool_duration = int((time.time() - t0) * 1000)
                res = materialize_tool_result(raw, provider="anthropic")
                await self._emit("tool_end", {
                    "tool_name": tu.name,
                    "tool_input": inp,
                    "duration_ms": tool_duration,
                    "result_length": len(materialized_content_bytes(res)) if res else 0,
                    "success": success,
                })
                print_tool_result(tu.name, display_tool_result(res))

                # Plan Mode 'clear-and-execute' 后：直接追加工具结果并跳出
                if self._context_cleared:
                    self._context_cleared = False
                    self._anthropic_messages.append({"role": "user", "content": res})
                    context_break = True
                    break
                tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": res})

            if not context_break and tool_results:
                self._anthropic_messages.append({"role": "user", "content": tool_results})
            self._context_cleared = False


            await self._check_and_compact()

    @staticmethod
    def _block_to_dict(block) -> dict:
        """将 Anthropic content block 对象转为 dict 以便 JSON 序列化存储。"""
        if block.type == "text":
            return {"type": "text", "text": block.text}
        if block.type == "thinking":
            result = {"type": "thinking", "thinking": block.thinking}
            signature = getattr(block, "signature", None)
            if signature is not None:
                result["signature"] = signature
            return result
        if block.type == "tool_use":
            return {"type": "tool_use", "id": block.id, "name": block.name, "input": dict(block.input) if hasattr(block.input, 'items') else block.input}
        # Fallback
        return {"type": block.type}

    def _normalize_anthropic_response_blocks(self, content: Any) -> list[dict[str, Any]]:
        """Validate provider text fields before recording or replay storage."""

        normalized: list[dict[str, Any]] = []
        for index, block in enumerate(content):
            block_kind = getattr(block, "type", None)
            if block_kind == "text":
                normalized.append(
                    {
                        "type": "text",
                        "text": _normalize_provider_text(
                            getattr(block, "text", None),
                            provider="anthropic",
                            block_kind="text",
                            block_index=index,
                        ),
                    }
                )
            elif block_kind == "thinking":
                thinking = _normalize_provider_text(
                    getattr(block, "thinking", None),
                    provider="anthropic",
                    block_kind="thinking",
                    block_index=index,
                )
                signature = getattr(block, "signature", None)
                if signature is not None and not isinstance(signature, str):
                    raise ProviderContentNormalizationError(
                        provider="anthropic",
                        block_kind="thinking",
                        block_index=index,
                        value=signature,
                    )
                item: dict[str, Any] = {"type": "thinking", "thinking": thinking}
                if signature is not None:
                    item["signature"] = signature
                normalized.append(item)
            else:
                normalized.append(self._block_to_dict(block))
        return normalized

    async def _call_anthropic_stream(self, on_tool_block_complete=None):
        """流式解析 Anthropic 响应；只记录 partial，不提前执行工具。"""
        async def _do():
            create_params: dict[str, Any] = {
                "model": self.model,
                "max_tokens": _get_anthropic_request_max_tokens(self.model),
                "system": self._system_prompt,
                "tools": get_active_tool_definitions(self.tools),
                "messages": self._anthropic_messages,
            }

            create_params.update(
                _thinking_request_params(
                    self.model,
                    self.thinking_effort,
                    use_openai=False,
                )
            )

            first_text = True
            first_thinking = True
            # 跟踪流式传输中的 tool_use 块（按 index），便于 content_block_stop 时解析执行
            tool_blocks_by_index: dict[int, dict] = {}

            async with self._anthropic_client.messages.stream(**create_params) as stream:
                async for event in stream:
                    if not hasattr(event, 'type'):
                        continue

                    if event.type == "content_block_start":
                        cb = getattr(event, 'content_block', None)
                        if cb and getattr(cb, 'type', None) == "tool_use":
                            tool_blocks_by_index[event.index] = {
                                "id": cb.id, "name": cb.name, "input_json": "",
                            }

                    elif event.type == "content_block_delta":
                        delta = event.delta
                        if hasattr(delta, 'text'):
                            text = _normalize_provider_text(
                                getattr(delta, "text", None),
                                provider="anthropic",
                                block_kind="text",
                                block_index=getattr(event, "index", -1),
                            )
                            if first_text:
                                stop_spinner()
                                self._emit_text("\n")
                                first_text = False
                                # ★ 发射 first_token 事件
                                await self._emit("first_token", {"is_thinking": False})
                            self._emit_text(text)
                            if self._runtime_recorder:
                                self._runtime_recorder.partial_text(text)
                        elif hasattr(delta, 'thinking'):
                            thinking = _normalize_provider_text(
                                getattr(delta, "thinking", None),
                                provider="anthropic",
                                block_kind="thinking",
                                block_index=getattr(event, "index", -1),
                            )
                            if first_thinking:
                                stop_spinner()
                                self._emit_text("\n")
                                first_thinking = False
                                # ★ 发射 first_token 事件
                                await self._emit("first_token", {"is_thinking": True})
                            self._emit_text(thinking)
                            if self._runtime_recorder:
                                self._runtime_recorder.partial_text(thinking)
                        elif hasattr(delta, 'partial_json'):
                            tb = tool_blocks_by_index.get(event.index)
                            if tb:
                                tb["input_json"] += delta.partial_json
                                if self._runtime_recorder:
                                    self._runtime_recorder.partial_tool_arguments(
                                        tb["id"], tb["name"], delta.partial_json
                                    )

                    elif event.type == "content_block_stop":
                        # The final response is the only executable boundary.
                        # Do not invoke a tool from content_block_stop: the
                        # caller still needs to validate and durably dispatch
                        # the complete function call below.
                        tool_blocks_by_index.pop(event.index, None)

                final_message = await stream.get_final_message()

            # Thinking blocks, including their signatures, are part of the
            # provider response state and must be preserved for the next
            # request.  The display path already streams their text; removing
            # them here would make tool-use follow-ups invalid for Anthropic
            # and compatible endpoints.
            final_message.content = [b for b in final_message.content]
            return final_message

        def _record_retry(attempt: int, error: Exception) -> None:
            if self._runtime_recorder:
                self._runtime_recorder.retry(attempt=attempt, reason=str(error))

        return await _with_retry(_do, on_retry=_record_retry)

    # ─── OpenAI 兼容后端 ────────────────────────────────────
    # 流式调用 OpenAI API，与 Anthropic 后端功能等价。
    # 并行执行：相邻的并发安全工具通过 asyncio.gather 并行执行。

    async def _chat_openai(self, user_message: str) -> None:
        self._openai_messages.append({"role": "user", "content": user_message})

        # 启动异步记忆预取（非阻塞，每个用户轮次触发一次）
        memory_prefetch: MemoryPrefetch | None = None
        if not self.is_sub_agent:
            sq = self._build_side_query()
            if sq:
                memory_prefetch = start_memory_prefetch(
                    user_message, sq,
                    self._already_surfaced_memories, self._session_memory_bytes,
                )

        while True:
            if self._aborted:
                break

            self._refresh_provider_context_from_canonical()

            _pre_size = self._msg_char_count()
            self._run_compression_pipeline()
            _post_size = self._msg_char_count()
            if _post_size < _pre_size:
                utilization = self.last_input_token_count / self.effective_window if self.effective_window else 0
                tier = 1 if utilization > 0.5 else 2 if utilization > SNIP_THRESHOLD else 3
                await self._emit("compaction", {"tier": tier})

            # 本轮 index
            turn_index = self.current_turns + 1
            await self._emit("turn_start", {"turn_index": turn_index})

            # Consume memory prefetch if settled (non-blocking poll, zero-wait)
            if memory_prefetch and memory_prefetch.settled and not memory_prefetch.consumed:
                memories = memory_prefetch.task.result()
                if memories:
                    self._persist_memory_context_event(memories)
                    self._refresh_provider_context_from_canonical()
                    for m in memories:
                        self._already_surfaced_memories.add(m.path)
                        self._session_memory_bytes += len(m.content.encode())
                memory_prefetch.consumed = True

            if not self.is_sub_agent:
                start_spinner()

            request_id = uuid.uuid4().hex
            self._current_request_id = request_id
            api_start = time.time()
            self._start_runtime_model_call(request_id, "openai", {"messages": self._openai_messages})
            try:
                response = await self._call_openai_stream()
            except Exception as error:
                if self._runtime_recorder:
                    self._runtime_recorder.error(error)
                    if self._runtime_guard and self._runtime_recorder.events:
                        self._runtime_guard.adopt_terminal_event(self._runtime_recorder.events[-1])
                raise

            if not self.is_sub_agent:
                stop_spinner()

            self.last_api_call_time = time.time()

            usage = response.get("usage") or {}
            if usage:
                self.total_input_tokens += usage["prompt_tokens"]
                self.total_output_tokens += usage["completion_tokens"]
                self.last_input_token_count = usage["prompt_tokens"]

            choice = response.get("choices", [{}])[0] if response.get("choices") else {}
            message = choice.get("message", {})
            tool_calls = message.get("tool_calls")

            try:
                normalized_message = dict(message)
                if "content" in message and message.get("content") is not None:
                    normalized_message["content"] = _normalize_provider_text(
                        message["content"],
                        provider="openai",
                        block_kind="text",
                        block_index=0,
                    )
                if "reasoning_content" in message:
                    normalized_message["reasoning_content"] = _normalize_provider_text(
                        message["reasoning_content"],
                        provider="openai",
                        block_kind="thinking",
                        block_index=0,
                    )
                message = normalized_message
                if self._runtime_recorder:
                    if "reasoning_content" in message:
                        self._runtime_recorder.final_thinking(
                            message["reasoning_content"]
                        )
                    if "content" in message and message.get("content") is not None:
                        self._runtime_recorder.final_text(message["content"])
                    for tool_call in tool_calls or []:
                        function = tool_call.get("function", {})
                        self._runtime_recorder.final_tool_call(
                            tool_call.get("id", "unknown-call"),
                            function.get("name", "unknown"),
                            function.get("arguments", "{}"),
                        )
            except ProviderContentNormalizationError as error:
                self._record_runtime_model_error(error)
                raise

            self._openai_messages.append(message)

            # ★ 发射 turn_end
            cache_read = 0
            pt_details = usage.get("prompt_tokens_details")
            if isinstance(pt_details, dict):
                cache_read = pt_details.get("cached_tokens", 0)
            finish = "end_turn" if tool_calls else "stop"
            if self._runtime_recorder:
                self._runtime_recorder.finish(
                    finish,
                    usage={
                        "input_tokens": usage.get("prompt_tokens"),
                        "output_tokens": usage.get("completion_tokens"),
                        "cache_read_tokens": cache_read,
                    },
                    latency_ms=int((time.time() - api_start) * 1000),
                )
            await self._emit("turn_end", {
                "turn_index": turn_index,
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
                "cache_read_tokens": cache_read,
                "cache_create_tokens": 0,  # OpenAI 不单独报告创建
                "finish_reason": finish,
            })
            if self._llm_capture_manager:
                self._capture_llm(
                    request_id=request_id,
                    messages=self._openai_messages.copy(),
                    response={"choices": [{"message": message}]},
                    usage={
                        "input_tokens": usage.get("prompt_tokens"),
                        "output_tokens": usage.get("completion_tokens"),
                    },
                    latency_ms=int((time.time() - api_start) * 1000),
                    input_tokens=usage.get("prompt_tokens"),
                    output_tokens=usage.get("completion_tokens"),
                    cache_read_tokens=cache_read,
                    finish_reason=finish,
                )
            if not tool_calls:
                if not self.is_sub_agent:
                    print_cost(self.total_input_tokens, self.total_output_tokens)
                break

            self.current_turns += 1
            budget = self._check_budget()
            if budget["exceeded"]:
                print_info(f"Budget exceeded: {budget['reason']}")
                self._record_budget_exceeded(budget["reason"])
                break

            # Phase 1: 解析 & 权限检查（串行 — 因为权限确认需要用户交互）
            oai_checked: list[dict] = []
            for tc in tool_calls:
                if self._aborted:
                    break
                if tc.get("type") != "function":
                    continue
                fn_name = tc["function"]["name"]
                raw_arguments = tc["function"].get("arguments", "")
                try:
                    inp = json.loads(raw_arguments)
                except Exception:
                    inp = {}

                print_tool_call(fn_name, inp)

                perm = check_permission(fn_name, inp, self.permission_mode, self._plan_file_path)
                if perm["action"] == "deny":
                    print_info(f"Denied: {perm.get('message', '')}")
                    oai_checked.append({
                        "tc": tc, "fn": fn_name, "inp": inp, "allowed": False,
                        "arguments_raw": raw_arguments, "decision": "deny", "reason": perm.get("message", ""),
                    })
                    continue
                if perm["action"] == "confirm" and perm.get("message") and perm["message"] not in self._confirmed_paths:
                    confirmed = await self._confirm_dangerous(perm["message"])
                    if not confirmed:
                        oai_checked.append({
                            "tc": tc, "fn": fn_name, "inp": inp, "allowed": False,
                            "arguments_raw": raw_arguments, "decision": "deny", "reason": perm["message"],
                        })
                        continue
                    self._confirmed_paths.add(perm["message"])
                oai_checked.append({
                    "tc": tc, "fn": fn_name, "inp": inp, "allowed": True,
                    "arguments_raw": raw_arguments, "decision": "allow", "reason": "permission granted",
                })

            # Phase 2: 分组 & 执行（连续的并发安全工具分组后 asyncio.gather 并行执行）
            oai_batches: list[dict] = []
            for ct in oai_checked:
                safe = ct["allowed"] and ct["fn"] in CONCURRENCY_SAFE_TOOLS
                if safe and oai_batches and oai_batches[-1]["concurrent"]:
                    oai_batches[-1]["items"].append(ct)
                else:
                    oai_batches.append({"concurrent": safe, "items": [ct]})

            oai_context_break = False
            for batch in oai_batches:
                if oai_context_break or self._aborted:
                    break

                if batch["concurrent"]:
                    async def _run_oai_safe(ct_item: dict) -> tuple[dict, str, bool]:
                        raw, success, executed = await self._run_durable_tool(
                            request_id=request_id,
                            call_id=ct_item["tc"].get("id", f"tool-{ct_item['fn']}"),
                            name=ct_item["fn"],
                            inp=ct_item["inp"],
                            arguments=ct_item.get("arguments_raw"),
                            permission={"decision": "allow", "reason": ct_item.get("reason", "")},
                        )
                        res = materialize_tool_result(raw, provider="openai")
                        return ct_item, res, success

                    t0_batch = time.time()
                    results = await asyncio.gather(*[_run_oai_safe(ct) for ct in batch["items"]])
                    for ct_item, res, success in results:
                        tool_duration = int((time.time() - t0_batch) * 1000)
                        await self._emit("tool_end", {
                            "tool_name": ct_item["fn"],
                            "tool_input": ct_item["inp"],
                            "duration_ms": tool_duration,
                            "result_length": len(materialized_content_bytes(res)) if res else 0,
                            "success": success,
                        })
                        print_tool_result(ct_item["fn"], display_tool_result(res))
                        self._openai_messages.append({"role": "tool", "tool_call_id": ct_item["tc"]["id"], "content": res})
                else:
                    for ct in batch["items"]:
                        if not ct["allowed"]:
                            raw, success, executed = await self._run_durable_tool(
                                request_id=request_id,
                                call_id=ct["tc"].get("id", f"tool-{ct['fn']}"),
                                name=ct["fn"],
                                inp=ct["inp"],
                                arguments=ct.get("arguments_raw"),
                                permission={"decision": ct.get("decision", "deny"), "reason": ct.get("reason", "")},
                            )
                            res = materialize_tool_result(raw, provider="openai")
                            self._openai_messages.append({"role": "tool", "tool_call_id": ct["tc"]["id"], "content": res})
                            continue
                        t0 = time.time()
                        raw, success, executed = await self._run_durable_tool(
                            request_id=request_id,
                            call_id=ct["tc"].get("id", f"tool-{ct['fn']}"),
                            name=ct["fn"],
                            inp=ct["inp"],
                            arguments=ct.get("arguments_raw"),
                            permission={"decision": "allow", "reason": ct.get("reason", "")},
                        )
                        tool_duration = int((time.time() - t0) * 1000)
                        res = materialize_tool_result(raw, provider="openai")
                        print_tool_result(ct["fn"], display_tool_result(res))
                        await self._emit("tool_end", {
                            "tool_name": ct["fn"],
                            "tool_input": ct["inp"],
                            "duration_ms": tool_duration,
                            "result_length": len(materialized_content_bytes(res)) if res else 0,
                            "success": success,
                        })
                        if self._context_cleared:
                            self._context_cleared = False
                            self._openai_messages.append({"role": "user", "content": res})
                            oai_context_break = True
                            break
                        self._openai_messages.append({"role": "tool", "tool_call_id": ct["tc"]["id"], "content": res})

            self._context_cleared = False
            await self._check_and_compact()

    async def _call_openai_stream(self) -> dict:
        """流式调用 OpenAI API，实时输出文本，收集 tool_calls 增量。
        返回与 OpenAI API 兼容的响应格式以便统一处理。"""
        async def _do():
            create_params = {
                "model": self.model,
                "tools": _to_openai_tools(get_active_tool_definitions(self.tools)),
                "messages": self._openai_messages,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            create_params.update(
                _thinking_request_params(
                    self.model,
                    self.thinking_effort,
                    use_openai=True,
                )
            )

            stream = await self._openai_client.chat.completions.create(**create_params)

            content = ""
            reasoning_content = ""
            first_text = True
            tool_calls: dict[int, dict] = {}
            finish_reason = ""
            usage = None

            async for chunk in stream:
                if chunk.usage:
                    usage = {
                        "prompt_tokens": chunk.usage.prompt_tokens,
                        "completion_tokens": chunk.usage.completion_tokens,
                    }

                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                # 捕获 reasoning_content（DeepSeek的思考内容）
                rc = None
                if hasattr(delta, "reasoning_content"):
                    raw_reasoning = getattr(delta, "reasoning_content")
                    # ``None`` is the SDK's absent-field marker.  Every other
                    # value, including falsey values such as 0/False/[],
                    # must cross the same strict provider boundary.
                    if raw_reasoning is not None:
                        rc = _normalize_provider_text(
                            raw_reasoning,
                            provider="openai",
                            block_kind="thinking",
                            block_index=0,
                        )

                if rc:
                    if not reasoning_content:
                        self._emit_text("\n")
                        await self._emit("first_token", {"is_thinking": True})
                    self._emit_text(rc)
                    if self._runtime_recorder:
                        self._runtime_recorder.partial_text(rc)
                    reasoning_content += rc

                if delta is not None and getattr(delta, "content", None) is not None:
                    text_delta = _normalize_provider_text(
                        getattr(delta, "content"),
                        provider="openai",
                        block_kind="text",
                        block_index=0,
                    )
                    if first_text:
                        stop_spinner()
                        self._emit_text("\n")
                        first_text = False
                        await self._emit("first_token", {"is_thinking": False})
                    self._emit_text(text_delta)
                    if self._runtime_recorder:
                        self._runtime_recorder.partial_text(text_delta)
                    content += text_delta

                if delta and delta.tool_calls:
                    for tc in delta.tool_calls:
                        existing = tool_calls.get(tc.index)
                        if existing:
                            if tc.function and tc.function.arguments:
                                existing["arguments"] += tc.function.arguments
                                if self._runtime_recorder:
                                    self._runtime_recorder.partial_tool_arguments(
                                        existing["id"] or "unknown-call",
                                        existing["name"] or "unknown",
                                        tc.function.arguments,
                                    )
                        else:
                            tool_calls[tc.index] = {
                                "id": tc.id or "",
                                "name": (tc.function.name if tc.function else "") or "",
                                "arguments": (tc.function.arguments if tc.function else "") or "",
                            }
                            if self._runtime_recorder and tc.function and tc.function.arguments:
                                self._runtime_recorder.partial_tool_arguments(
                                    tc.id or "unknown-call",
                                    (tc.function.name or "unknown"),
                                    tc.function.arguments,
                                )

                if chunk.choices[0].finish_reason:
                    finish_reason = chunk.choices[0].finish_reason

            assembled = None
            if tool_calls:
                assembled = [
                    {"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                    for _, tc in sorted(tool_calls.items())
                ]

            message = {
                "role": "assistant",
                "content": content or None,
                "tool_calls": assembled,
            }
            # DeepSeek thinking 模式要求所有 assistant 消息都包含 reasoning_content
            # 即使为空也需要保存，以保持一致性
            model_lower = self.model.lower()
            is_deepseek_thinking = "deepseek" in model_lower and ("v4" in model_lower or "v3" in model_lower or "reasoner" in model_lower)
            
            if reasoning_content:
                message["reasoning_content"] = reasoning_content
            elif is_deepseek_thinking:
                # DeepSeek thinking 模式下，即使没有 reasoning_content 也需要设置空字符串
                message["reasoning_content"] = ""

            return {
                "choices": [{
                    "message": message,
                    "finish_reason": finish_reason or "stop",
                }],
                "usage": usage,
            }

        def _record_retry(attempt: int, error: Exception) -> None:
            if self._runtime_recorder:
                self._runtime_recorder.retry(attempt=attempt, reason=str(error))

        return await _with_retry(_do, on_retry=_record_retry)

    # ─── 共享方法 ────────────────────────────────────────────

    async def _confirm_dangerous(self, command: str) -> bool:
        """危险操作确认：调用 confirm_fn 或 fallback 到阻塞式 input。"""
        print_confirmation(command)
        if self.confirm_fn:
            return await self.confirm_fn(command)
        # Fallback: blocking input
        try:
            answer = input("  Allow? (y/n): ")
            return answer.lower().startswith("y")
        except EOFError:
            return False
