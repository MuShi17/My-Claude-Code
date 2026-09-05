# Observability Tracing 实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为 Mini Claude Code 建立可观测体系，追踪首Token速度、任务轮次、工具调用、Token消耗、缓存命中率，每次用户 ask 生成独立 trace 文件。

**Architecture:** Observer 模式 — Agent 新增事件发射器，新建 tracer.py SessionTracer 订阅事件累积指标，session.py 重构目录结构为 `sessions/{id}/session.json` + `sessions/{id}/traces/*.jsonl`。

**Tech Stack:** Python 3.11+, asyncio, 无第三方依赖

**Design Doc:** `docs/plans/2026-05-29-observability-tracing-design.md`

---

### Task 1: 改造 session.py 目录结构

**Files:**
- Modify: `src/mini_claude/session.py`
- Modify: `src/mini_claude/agent.py:456-470` (_auto_save method)

**Step 1: 改造 save_session**

将 `save_session` 从扁平 `.json` 改为 `{session_id}/session.json` 目录结构。

```python
# session.py 完整改写

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
```

**Step 2: 更新 agent.py _auto_save 中的 metadata**

在 `agent.py` 的 `_auto_save` 方法中，metadata 加 `askCount` 字段：

```python
# agent.py:456-470
def _auto_save(self) -> None:
    try:
        save_session(self.session_id, {
            "metadata": {
                "id": self.session_id,
                "model": self.model,
                "cwd": str(Path.cwd()),
                "startTime": self.session_start_time,
                "messageCount": self._get_message_count(),
                "askCount": self._ask_count,  # 新增
            },
            "anthropicMessages": self._anthropic_messages if not self.use_openai else None,
            "openaiMessages": self._openai_messages if self.use_openai else None,
        })
    except Exception:
        pass
```

**Step 3: 验证 session.json 路径正确**

运行以下命令确认新格式写入无误：

```bash
cd src && python -c "
from mini_claude.session import save_session, load_session, list_sessions, save_trace, SESSION_DIR
import uuid
sid = 'test_' + uuid.uuid4().hex[:8]
save_session(sid, {'metadata': {'id': sid, 'model': 'test', 'startTime': 'now', 'messageCount': 0}})
import os
print('session.json exists:', os.path.exists(os.path.join(str(SESSION_DIR), sid, 'session.json')))
data = load_session(sid)
print('load ok:', data is not None)
sessions = list_sessions()
print('found in list:', any(s['id'] == sid for s in sessions))
# cleanup
import shutil
shutil.rmtree(os.path.join(str(SESSION_DIR), sid))
print('PASS')
"
```

**Step 4: Commit**

```bash
git add src/mini_claude/session.py src/mini_claude/agent.py
git commit -m "feat: restructure session storage to dir-based layout with trace support"
```

---

### Task 2: 创建 tracer.py 模块

**Files:**
- Create: `src/mini_claude/tracer.py`

**Step 1: 编写 SessionTracer 类**

```python
"""观测追踪模块 — 收集每次 ask 的性能指标并写入 JSONL trace 文件。"""

from __future__ import annotations

import time
import json
from typing import Any


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
```

**Step 2: 快速验证 Tracer 基本功能**

```bash
cd src && python -c "
from mini_claude.tracer import SessionTracer
t = SessionTracer(1, 'hello test')
t.on_turn_start({'turn_index': 0})
import time
time.sleep(0.05)  # simulate 50ms first token
t.on_first_token({})
t.on_turn_end({'input_tokens': 100, 'output_tokens': 50, 'cache_read_tokens': 90, 'cache_create_tokens': 0, 'finish_reason': 'stop'})
lines = t.finalize()
import json
for line in lines:
    parsed = json.loads(line)
    print(parsed['type'], '- turns:', parsed.get('total_turns', parsed.get('turn_index')))
assert len(lines) == 2  # 1 ask + 1 turn
assert lines[0].startswith('{\"type\":\"ask\"')
assert lines[1].startswith('{\"type\":\"turn\"')
assert '"first_token_ms"' in lines[1]
print('PASS')
"
```

**Step 3: Commit**

```bash
git add src/mini_claude/tracer.py
git commit -m "feat: add SessionTracer for per-ask observability"
```

---

### Task 3: 在 Agent 中添加事件发射器

**Files:**
- Modify: `src/mini_claude/agent.py:173-252` (__init__, 添加 _event_hooks, _ask_count, on/off/_emit 方法)

**Step 1: 添加事件发射器基础设施**

在 `Agent.__init__` 中新增两行：

```python
# agent.py, 在 __init__ 末尾的现有属性中新增:

        # 事件钩子系统（观测/Observer模式）
        self._event_hooks: dict[str, list] = {}

        # Ask 计数（每次 chat() 入口自增，用于 trace 文件编号）
        self._ask_count: int = 0
```

在 Agent 类中新增三个方法（放在 `__init__` 之后）：

```python
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
        for cb in self._event_hooks.get(event, []):
            try:
                res = cb(payload)
                if _asyncio.iscoroutine(res):
                    await res
            except Exception:
                pass  # 观测错误不影响主流程
```

**Step 2: 验证事件发射器可用**

```bash
cd src && python -c "
from mini_claude.agent import Agent
import asyncio

received = []
def handler(payload):
    received.append(payload)

agent = Agent()
agent.on('test', handler)
asyncio.run(agent._emit('test', {'key': 'value'}))
assert len(received) == 1
assert received[0] == {'key': 'value'}
agent.off('test', handler)
asyncio.run(agent._emit('test', {}))
assert len(received) == 1  # 已取消订阅
print('PASS')
"
```

**Step 3: Commit**

```bash
git add src/mini_claude/agent.py
git commit -m "feat: add event emitter (on/off/_emit) to Agent"
```

---

### Task 4: 在 Agent 循环中埋点 — Anthropic 后端

**Files:**
- Modify: `src/mini_claude/agent.py:352-378` (chat 方法)
- Modify: `src/mini_claude/agent.py:896-1033` (_chat_anthropic 方法)
- Modify: `src/mini_claude/agent.py:1047-1121` (_call_anthropic_stream 方法)
- Modify: `src/mini_claude/agent.py:456-470` (_auto_save 方法)

**Step 1: 改造 chat() 方法 — 创建 Tracer + 订阅 + 清理**

```python
# agent.py, chat() 方法替换为:
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

        # 创建观测器（仅主 Agent）
        tracer: Any = None
        if not self.is_sub_agent:
            from .tracer import SessionTracer
            tracer = SessionTracer(self._ask_count, user_message)
            self.on("turn_start", tracer.on_turn_start)
            self.on("first_token", tracer.on_first_token)
            self.on("turn_end", tracer.on_turn_end)
            self.on("tool_start", tracer.on_tool_start)
            self.on("tool_end", tracer.on_tool_end)
            self.on("compaction", tracer.on_compaction)
            self.on("permission", tracer.on_permission)

        await self._emit("chat_start", {"message": user_message, "timestamp": time.time()})

        coro = self._chat_openai(user_message) if self.use_openai else self._chat_anthropic(user_message)
        self._current_task = asyncio.current_task()
        try:
            await coro
        except asyncio.CancelledError:
            self._aborted = True
        except Exception as e:
            await self._emit("chat_error", {"error": str(e)})
            raise
        finally:
            self._current_task = None
            # 取消观测订阅 + 写 trace
            if tracer:
                for evt, cb in [
                    ("turn_start", tracer.on_turn_start),
                    ("first_token", tracer.on_first_token),
                    ("turn_end", tracer.on_turn_end),
                    ("tool_start", tracer.on_tool_start),
                    ("tool_end", tracer.on_tool_end),
                    ("compaction", tracer.on_compaction),
                    ("permission", tracer.on_permission),
                ]:
                    self.off(evt, cb)
                try:
                    from .session import save_trace
                    save_trace(self.session_id, self._ask_count, tracer.finalize())
                except Exception:
                    pass

        if not self.is_sub_agent:
            print_divider()
            self._auto_save()
```

**Step 2: 在 _chat_anthropic 循环中插入事件发射点**

关键修改点（含具体行号和上下文）：

**a) turn_start + turn_index 追踪** — 在 `while True:` 循环体内，`_run_compression_pipeline()` 调用之后、spinner 之前：

```python
# 在 _chat_anthropic 的 while True 循环中
# 替换 self._run_compression_pipeline() 之后的代码块:

            self._run_compression_pipeline()

            # 本轮 index
            turn_index = self.current_turns + 1

            await self._emit("turn_start", {"turn_index": turn_index})

            # 消费记忆预取结果（非阻塞轮询，zero-wait）
            ...  # 原逻辑不变
```

**b) first_token** — 在 `_call_anthropic_stream` 中，`first_text` 和 `first_thinking` 标记处发射：

```python
# 在 _call_anthropic_stream 的 _do() 中，修改 delta 处理:

                        if hasattr(delta, 'text'):
                            if first_text:
                                stop_spinner()
                                self._emit_text("\n")
                                first_text = False
                                # ★ 发射 first_token 事件
                                asyncio.create_task(
                                    self._emit("first_token", {"is_thinking": False})
                                )
                            self._emit_text(delta.text)

                        elif hasattr(delta, 'thinking'):
                            if first_thinking:
                                stop_spinner()
                                self._emit_text("\n")
                                first_thinking = False
                                # ★ 发射 first_token 事件
                                asyncio.create_task(
                                    self._emit("first_token", {"is_thinking": True})
                                )
                            self._emit_text(delta.thinking)
```

**c) turn_end** — 在 `response = await self._call_anthropic_stream(...)` 后，提取 usage 后：

```python
            response = await self._call_anthropic_stream(on_tool_block_complete=_on_tool_block)

            if not self.is_sub_agent:
                stop_spinner()

            self.last_api_call_time = time.time()
            self.total_input_tokens += response.usage.input_tokens
            self.total_output_tokens += response.usage.output_tokens
            self.last_input_token_count = response.usage.input_tokens

            tool_uses = [b for b in response.content if b.type == "tool_use"]

            # ★ 发射 turn_end 事件
            cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0
            cache_create = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
            finish = "end_turn" if tool_uses else "stop"
            await self._emit("turn_end", {
                "turn_index": turn_index,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "cache_read_tokens": cache_read,
                "cache_create_tokens": cache_create,
                "finish_reason": finish,
            })
```

**d) tool_start / tool_end** — 在 `_execute_tool_call` 调用处包装测时：

```python
                # 替换原来的 raw = await self._execute_tool_call(...)
                t0 = time.time()
                await self._emit("tool_start", {"tool_name": tu.name, "tool_input": inp})
                raw = await self._execute_tool_call(tu.name, inp)
                tool_duration = int((time.time() - t0) * 1000)
                res = self._persist_large_result(tu.name, raw)
                await self._emit("tool_end", {
                    "tool_name": tu.name,
                    "tool_input": inp,
                    "duration_ms": tool_duration,
                    "result_length": len(raw.encode()) if raw else 0,
                })
```

同样对 early_executions 路径也添加：

```python
                early_task = early_executions.get(tu.id)
                if early_task:
                    t0 = time.time()
                    await self._emit("tool_start", {"tool_name": tu.name, "tool_input": inp})
                    raw = await early_task
                    tool_duration = int((time.time() - t0) * 1000)
                    res = self._persist_large_result(tu.name, raw)
                    await self._emit("tool_end", {
                        "tool_name": tu.name,
                        "tool_input": inp,
                        "duration_ms": tool_duration,
                        "result_length": len(raw.encode()) if raw else 0,
                    })
                    print_tool_result(tu.name, res)
                    tool_results.append(...)
                    continue
```

**e) compaction** — 在 `_run_compression_pipeline` 调用前后各层的触发标记处发射。

为尽量减少侵入，在 `_run_compression_pipeline` 中检测是否有压缩动作。但更简单的方式是：在 T1-T4 各方法实际执行裁剪/剪除/微压缩/完整压缩时发射事件。

```python
    # 修改 _run_compression_pipeline 末尾，检查是否有动作
    # 实际更简洁的做法：在各压缩方法内部有实质操作时发射

    # 在 _budget_tool_results_anthropic 的截断逻辑中:
    # （在检查 if utilization < 0.5: return 之后，在截断循环中发射）

    # 在 _check_and_compact 触发真实 compact 时:
    async def _check_and_compact(self) -> None:
        if self.last_input_token_count > self.effective_window * 0.85:
            print_info("Context window filling up, compacting conversation...")
            await self._emit("compaction", {"tier": 4})
            await self._compact_conversation()
```

简化做法：在每个有实际压缩动作的地方发射。Tier 1-3 发射在 `_run_compression_pipeline` 中检测 utilization 阈值触发时。最简单的方法是每次 `_run_compression_pipeline` 调用前记录 token 状态，调用后对比：

```python
    # 在 _chat_anthropic 的 while 循环中:
    _pre_compaction = self.last_input_token_count
    self._run_compression_pipeline()
    if self.last_input_token_count != _pre_compaction:
        await self._emit("compaction", {"tier": 1})
```

但 last_input_token_count 在 compression 中不变（只裁剪消息），所以改为记录消息内容的 hash 或简单地在 T1/T2/T3 方法内部有实质裁剪时 emit。权衡后采用最简方式：在 compression 方法内 `any_compacted` 标记发射。

实际上考虑到改动最小化，可以在 `_run_compression_pipeline` 前后计算消息总字符数：

```python
    def _msg_char_count(self) -> int:
        msgs = self._openai_messages if self.use_openai else self._anthropic_messages
        return sum(len(str(m)) for m in msgs)

    # 在 while 循环中 compression 前后:
    _pre_size = self._msg_char_count()
    self._run_compression_pipeline()
    _post_size = self._msg_char_count()
    if _post_size < _pre_size:
        # 判断 tier: 看当前触发条件
        utilization = self.last_input_token_count / self.effective_window if self.effective_window else 0
        tier = 1 if utilization > 0.5 else 2 if utilization > SNIP_THRESHOLD else 3
        await self._emit("compaction", {"tier": tier})
```

**f) permission** — 在 `_confirm_dangerous` 调用处：

```python
                if perm["action"] == "confirm" and ...:
                    confirmed = await self._confirm_dangerous(perm["message"])
                    await self._emit("permission", {"message": perm["message"], "allowed": confirmed})
```

**Step 4: Commit**

```bash
git add src/mini_claude/agent.py
git commit -m "feat: instrument _chat_anthropic loop with event emissions"
```

---

### Task 5: 在 Agent 循环中埋点 — OpenAI 后端

**Files:**
- Modify: `src/mini_claude/agent.py:1127-1266` (_chat_openai 方法)
- Modify: `src/mini_claude/agent.py:1267-1366` (_call_openai_stream 方法)

**Step 1: 改造 _chat_openai**

在 `_chat_openai` 的 while 循环中插入与 Anthropic 后端相同的事件发射点：

**a) turn_start** — 同 Anthropic，在 `_run_compression_pipeline()` 之后、spinner 之前：

```python
            self._run_compression_pipeline()
            turn_index = self.current_turns + 1
            await self._emit("turn_start", {"turn_index": turn_index})
```

**b) first_token** — 在 `_call_openai_stream` 的 `_do()` 中：

```python
                if delta and delta.content:
                    if first_text:
                        stop_spinner()
                        self._emit_text("\n")
                        first_text = False
                        asyncio.create_task(
                            self._emit("first_token", {"is_thinking": False})
                        )
                    self._emit_text(delta.content)
                    content += delta.content

                # reasoning_content 的第一个 chunk
                if rc:
                    if not reasoning_content:
                        asyncio.create_task(
                            self._emit("first_token", {"is_thinking": True})
                        )
                    ...
```

**c) turn_end** — response 返回后：

```python
            response = await self._call_openai_stream()
            if not self.is_sub_agent:
                stop_spinner()
            self.last_api_call_time = time.time()

            if response.get("usage"):
                self.total_input_tokens += response["usage"]["prompt_tokens"]
                self.total_output_tokens += response["usage"]["completion_tokens"]
                self.last_input_token_count = response["usage"]["prompt_tokens"]

            choice = response.get("choices", [{}])[0] if response.get("choices") else {}
            message = choice.get("message", {})
            self._openai_messages.append(message)

            tool_calls = message.get("tool_calls")

            # ★ 发射 turn_end
            usage = response.get("usage", {})
            cache_read = usage.get("prompt_tokens_details", {}).get("cached_tokens", 0) if isinstance(usage.get("prompt_tokens_details"), dict) else 0
            finish = "end_turn" if tool_calls else "stop"
            await self._emit("turn_end", {
                "turn_index": turn_index,
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
                "cache_read_tokens": cache_read,
                "cache_create_tokens": 0,  # OpenAI 不单独报告创建
                "finish_reason": finish,
            })
```

**d) tool_start / tool_end** — 在 openai 后端的两处工具执行路径：

```python
                # 并行执行路径
                if batch["concurrent"]:
                    # 为每个并发工具发送 tool_start
                    for ct in batch["items"]:
                        await self._emit("tool_start", {"tool_name": ct["fn"], "tool_input": ct["inp"]})
                    t0_batch = time.time()
                    results = await asyncio.gather(...)
                    for ct_item, res in results:
                        tool_duration = int((time.time() - t0_batch) * 1000)
                        await self._emit("tool_end", {
                            "tool_name": ct_item["fn"],
                            "tool_input": ct_item["inp"],
                            "duration_ms": tool_duration,
                            "result_length": len(res.encode()) if res else 0,
                        })
                        ...
                else:
                    # 串行路径
                    for ct in batch["items"]:
                        if not ct["allowed"]:
                            ...
                            continue
                        t0 = time.time()
                        await self._emit("tool_start", {"tool_name": ct["fn"], "tool_input": ct["inp"]})
                        raw = await self._execute_tool_call(ct["fn"], ct["inp"])
                        tool_duration = int((time.time() - t0) * 1000)
                        res = self._persist_large_result(ct["fn"], raw)
                        print_tool_result(ct["fn"], res)
                        await self._emit("tool_end", {
                            "tool_name": ct["fn"],
                            "tool_input": ct["inp"],
                            "duration_ms": tool_duration,
                            "result_length": len(raw.encode()) if raw else 0,
                        })
```

**e) compaction** — 同 Anthropic 后端的处理方式：

```python
    # 在 _chat_openai 的 while 循环中:
    _pre_size = self._msg_char_count()
    self._run_compression_pipeline()
    _post_size = self._msg_char_count()
    if _post_size < _pre_size:
        utilization = self.last_input_token_count / self.effective_window if self.effective_window else 0
        tier = 1 if utilization > 0.5 else 2 if utilization > SNIP_THRESHOLD else 3
        await self._emit("compaction", {"tier": tier})
```

此外在 `_check_and_compact` 触发完整 compact 时（同 Anthropic 后端共享同一条路径，已在 Task 4 中处理）。

**f) permission** — 在 OpenAI 后端的权限确认处：

```python
                if perm["action"] == "confirm" and ...:
                    confirmed = await self._confirm_dangerous(perm["message"])
                    await self._emit("permission", {"message": perm["message"], "allowed": confirmed})
```

**Step 2: 验证事件流向完整**

手动测试（需要 API key，可以跳过实际调用只验证事件订阅框架）：

```bash
cd src && python -c "
from mini_claude.agent import Agent
agent = Agent()
events = []
agent.on('chat_start', lambda p: events.append(('chat_start', p)))
agent.on('turn_start', lambda p: events.append(('turn_start', p)))
agent.on('chat_end', lambda p: events.append(('chat_end', p)))
import asyncio
asyncio.run(agent._emit('chat_start', {'message': 'test'}))
asyncio.run(agent._emit('turn_start', {'turn_index': 1}))
asyncio.run(agent._emit('chat_end', {}))
assert len(events) == 3
print('event flow:', [e[0] for e in events])
print('PASS')
"
```

**Step 3: Commit**

```bash
git add src/mini_claude/agent.py
git commit -m "feat: instrument _chat_openai loop with event emissions"
```

---

### Task 6: 恢复会话时 ask_count 回填

**Files:**
- Modify: `src/mini_claude/agent.py:442-451` (restore_session 方法)

**Step 1: 从 metadata 恢复 ask_count**

```python
# agent.py restore_session 方法:
    def restore_session(self, data: dict) -> None:
        if data.get("anthropicMessages"):
            self._anthropic_messages = data["anthropicMessages"]
        if data.get("openaiMessages"):
            self._openai_messages = data["openaiMessages"]
        meta = data.get("metadata")
        if meta and meta.get("id"):
            self.session_id = meta["id"]
            self._ask_count = meta.get("askCount", 0)  # ★ 新增
        print_info(f"Session restored ({self._get_message_count()} messages).")
```

**Step 2: 验证**

```bash
cd src && python -c "
from mini_claude.agent import Agent
agent = Agent()
agent.restore_session({
    'metadata': {'id': 'abc123', 'askCount': 5},
    'anthropicMessages': [],
})
assert agent.session_id == 'abc123'
assert agent._ask_count == 5
print('PASS')
"
```

**Step 3: Commit**

```bash
git add src/mini_claude/agent.py
git commit -m "feat: restore ask_count from session metadata on resume"
```

---

### Task 7: 端到端集成验证

**Step 1: 完整性检查**

```bash
cd src && python -c "
# 验证所有模块导入正常
from mini_claude.agent import Agent
from mini_claude.tracer import SessionTracer
from mini_claude.session import save_session, load_session, list_sessions, save_trace
print('All imports OK')

# 验证 Agent 有事件系统
agent = Agent()
assert hasattr(agent, '_event_hooks')
assert hasattr(agent, '_ask_count')
assert hasattr(agent, 'on')
assert hasattr(agent, 'off')
assert hasattr(agent, '_emit')
print('Agent events OK')

# 验证 Tracer 基本功能
t = SessionTracer(1, 'test')
t.on_turn_start({'turn_index': 0})
t.on_first_token({})
t.on_turn_end({'input_tokens': 100, 'output_tokens': 50, 'cache_read_tokens': 0, 'cache_create_tokens': 0, 'finish_reason': 'stop'})
lines = t.finalize()
assert len(lines) == 2
import json
ask = json.loads(lines[0])
assert ask['type'] == 'ask'
turn = json.loads(lines[1])
assert turn['type'] == 'turn'
assert turn['first_token_ms'] >= 0
print('Tracer OK')

# 验证 session 存储
import uuid, os, shutil
sid = 'e2e_' + uuid.uuid4().hex[:8]
save_session(sid, {'metadata': {'id': sid, 'model': 'test', 'askCount': 3}})
session_dir = os.path.expanduser(f'~/.mini-claude/sessions/{sid}')
assert os.path.isdir(session_dir)
assert os.path.exists(os.path.join(session_dir, 'session.json'))
trace_lines = ['{\"type\":\"ask\",\"ask_index\":1}', '{\"type\":\"turn\",\"turn_index\":1}']
save_trace(sid, 1, trace_lines)
assert os.path.exists(os.path.join(session_dir, 'traces', '001.jsonl'))
# cleanup
shutil.rmtree(session_dir)
print('Session storage OK')
print('ALL PASS')
"
```

**Step 2: Commit**

```bash
git add src/mini_claude/agent.py src/mini_claude/tracer.py src/mini_claude/session.py
git commit -m "test: add end-to-end integration verification"
```

---

### 实现顺序汇总

```
Task 1: session.py 目录结构改造        ← 基础
Task 2: tracer.py SessionTracer        ← 数据模型
Task 3: Agent 事件发射器               ← 基础设施
Task 4: _chat_anthropic 埋点           ← 核心集成
Task 5: _chat_openai 埋点              ← 核心集成
Task 6: restore_session ask_count      ← 边角
Task 7: 端到端验证                      ← 最终验证
```
