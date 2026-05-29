# 观测体系设计

**日期**: 2026-05-29
**方案**: B — Observer 模式（回调/事件驱动）

## 概述

为 Mini Claude Code 建立可观测体系，追踪每次用户 ask 的性能指标：首 Token 速度、任务轮次、工具调用、Token 消耗、缓存命中率等。同时重构会话存储目录结构，支持每次 ask 生成独立 trace 文件。

## 架构

```
Agent (agent.py)                    Tracer (tracer.py)              session.py
┌─────────────────────┐    事件     ┌──────────────────────┐  写文件  ┌────────────────┐
│ _event_hooks        │───发射──→  │ SessionTracer         │────────→│ traces/*.jsonl │
│ on/off/_emit        │            │   - on_turn_start()   │         │ session.json   │
│                     │            │   - on_first_token()  │         └────────────────┘
│ _chat_anthropic/    │            │   - on_turn_end()     │
│ _chat_openai        │            │   - on_tool_start()   │
└─────────────────────┘            │   - on_tool_end()     │
                                   │   - finalize()        │
                                   └──────────────────────┘
```

## 1. 事件钩子接口

Agent 新增 `EventEmitter` 模式，暴露 10 个事件：

| 事件 | payload | 触发时机 |
|------|---------|---------|
| `chat_start` | `{message, timestamp}` | chat() 入口 |
| `turn_start` | `{turn_index}` | 每轮 API 调用前 |
| `first_token` | `{timestamp, is_thinking}` | 首个 text/thinking delta 到达 |
| `turn_end` | `{turn_index, input_tokens, output_tokens, cache_read_tokens, cache_create_tokens, finish_reason, duration_ms}` | API 响应完成 |
| `tool_start` | `{tool_name, tool_input}` | 工具执行前 |
| `tool_end` | `{tool_name, duration_ms, result_length}` | 工具执行后 |
| `compaction` | `{tier: 1\|2\|3\|4}` | 压缩触发时 |
| `permission` | `{message, allowed}` | 权限确认后 |
| `chat_end` | `{total_turns, total_duration_ms}` | chat() 正常结束 |
| `chat_error` | `{error}` | chat() 异常 |

Agent 新增方法：`on(event, callback)`, `off(event, callback)`, `_emit(event, payload)`。

## 2. 目录结构

```
之前:  ~/.mini-claude/sessions/{session_id}.json

之后:  ~/.mini-claude/sessions/{session_id}/
         ├── session.json          # 原会话数据（metadata + messages）
         └── traces/
              ├── 001.jsonl        # 第 1 次 ask
              ├── 002.jsonl        # 第 2 次 ask
              └── ...
```

session.json 新增 `metadata.askCount` 用于追踪 ask 编号。

## 3. Trace 文件格式（JSONL）

每次 `chat()` 结束生成一个 JSONL 文件。第一行是 `ask` 概览，后续行是每个 `turn`：

```jsonl
{"type":"ask","ask_index":1,"message":"fix the bug","timestamp":"...","total_turns":3,"total_duration_ms":4500,"total_input_tokens":12000,"total_output_tokens":800,"total_tool_calls":5}
{"type":"turn","turn_index":1,"input_tokens":4000,"output_tokens":300,"cache_read_tokens":3800,"cache_create_tokens":0,"first_token_ms":320,"total_duration_ms":1800,"tool_calls":[{"name":"read_file","input":{...},"duration_ms":120,"result_length":2048}],"finish_reason":"end_turn"}
{"type":"turn","turn_index":2,...}
```

### 字段说明

**ask 行**:
- `ask_index`: 本次 session 内的 ask 序号（从 1 开始）
- `message`: 用户原始输入
- `timestamp`: ISO 8601
- `total_turns`: 本轮 API 调用轮次
- `total_duration_ms`: 整个 ask 耗时
- `total_input_tokens` / `total_output_tokens` / `total_tool_calls`: 汇总

**turn 行**:
- `turn_index`: 在本次 ask 内的轮次序号
- `first_token_ms`: 首个 token 到达时间（从 API 请求发出算起）
- `total_duration_ms`: 本轮 API 调用耗时
- `cache_read_tokens`: API 返回的缓存命中 token 数
- `cache_create_tokens`: API 返回的缓存写入 token 数
- `tool_calls`: 本轮所有工具调用的数组，每项含 `name`/`input`/`duration_ms`/`result_length`
- `finish_reason`: `end_turn`（有工具调用）/ `stop`（无工具调用）
- `compaction_triggered`: 仅压缩触发时为 `true`

## 4. Tracer 模块

`src/mini_claude/tracer.py`：

- **`SessionTracer`**: 每轮 `chat()` 创建，订阅 Agent 事件
  - `on_turn_start()` — 记录本轮起始时间
  - `on_first_token()` — `first_token_ms = now - turn_start`
  - `on_turn_end()` — 提取 usage 数据（含缓存命中）
  - `on_tool_start/end()` — 累积 tool_calls 列表
  - `on_compaction()` — 标记本轮 compaction_triggered
  - `finalize()` — 返回完整 JSONL 行列表，含 ask 行 + 所有 turn 行

## 5. session.py 改造

- `save_session()` — 保存到 `{session_id}/session.json`
- `load_session()` — 先尝试新路径，再回退旧路径（兼容）
- `list_sessions()` — 遍历子目录，兼容旧格式
- 新增 `save_trace(session_id, ask_index, lines)` — 写 `{session_id}/traces/{ask_index:03d}.jsonl`

## 6. Agent 集成

- `Agent.__init__()` 新增 `self._event_hooks: dict[str, list]` 和 `self._ask_count = 0`
- `Agent.on()` / `Agent.off()` / `Agent._emit()` — 事件订阅/发射
- `Agent.chat()` — 创建 SessionTracer，订阅事件，finally 中调用 `finalize()` + `save_trace()`
- `_chat_anthropic()/ _chat_openai()` — 在关键位置插入 `_emit()` 调用
- `_call_anthropic_stream()` — 首次 text/thinking delta 时发射 `first_token`
- `restore_session()` — 从 metadata.askCount 恢复 ask 编号

## 7. 兼容性

- `load_session()` 和 `list_sessions()` 向后兼容旧的扁平 `.json` 格式
- Web 模式（web/api.py）无需额外改动：monkey-patch 叠加在 Agent 事件系统之上
- 首次使用新格式时，手动迁移无需脚本（load → save 自动升级）
