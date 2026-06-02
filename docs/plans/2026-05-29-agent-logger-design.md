# Agent Logger 设计文档

> **For Claude:** 实现时使用 superpowers:writing-plans 生成实现计划。

**Goal:** 建立 agent 实时运行时日志系统，支持 crash-safe 逐条记录、LLM 内容分离存储、sub-agent 追踪。

**Architecture:** `logger.py` — AgentLogger 类，实时 flush 写入 JSONL。与 tracer 职责分离（tracer 侧重 per-ask 性能聚合，logger 侧重 per-event 运行时观测）。

---

## 1. 设计决策

| 维度 | 决策 |
|------|------|
| 与 tracer 关系 | 共存，职责分离 |
| 存储位置 | `sessions/{id}/logs/` + `sessions/{id}/llm/` |
| LLM 内容格式 | 按 session 一个 JSONL 文件 |
| Sub-agent 日志 | 全部写入主 agent 的 session |
| 写入时机 | 实时 flush，每事件立即落盘 |
| tracer 写入时机 | 改为实时写入（与 logger 一致） |

---

## 2. 目录结构

```
sessions/{id}/
  session.json
  traces/                 # tracer.py（改为实时写入）
    001.jsonl
  logs/                   # 新增：运行时日志
    001.jsonl
    002.jsonl
  llm/                    # 新增：LLM prompt/response
    {session_id}.jsonl
```

---

## 3. 主日志事件格式

`logs/{ask_index:03d}.jsonl`，每行一个 JSON 事件：

```
api_request   — API 调用开始
api_response  — API 响应（延迟、token、llm_ref）
tool_call     — 工具调用详情
sub_agent     — 子 agent 启动
error         — 运行时错误
```

```json
{"type":"api_request","request_id":"req_001","agent_id":"main","timestamp":"2026-...","model":"claude-opus-4-6"}
{"type":"api_response","request_id":"req_001","agent_id":"main","timestamp":"2026-...","latency_ms":1234,"input_tokens":5000,"output_tokens":200,"cache_read_tokens":3000,"llm_ref":"req_001"}
{"type":"tool_call","request_id":"req_001","agent_id":"main","timestamp":"2026-...","tool_name":"read_file","params":{"file_path":"..."},"duration_ms":45,"success":true}
{"type":"sub_agent","request_id":"req_001","agent_id":"main","timestamp":"2026-...","sub_agent_name":"explore","sub_agent_type":"explore","prompt_summary":"查找所有 API 路由..."}
{"type":"error","request_id":"req_001","agent_id":"main","timestamp":"2026-...","error_type":"api_timeout","message":"Request timed out after 30s"}
```

## 4. LLM 内容格式

`llm/{session_id}.jsonl`，每行一个完整的 API 请求/响应：

```json
{
  "request_id": "req_001",
  "timestamp": "2026-05-29T12:00:00Z",
  "model": "claude-opus-4-6",
  "messages": [
    {"role": "system", "content": "You are a coding agent..."},
    {"role": "user", "content": "Read sample.txt and replace..."}
  ],
  "response": {
    "content": [{"type": "text", "text": "..."}],
    "stop_reason": "end_turn"
  },
  "usage": {
    "input_tokens": 5000,
    "output_tokens": 200,
    "cache_read_input_tokens": 3000
  }
}
```

主日志 `llm_ref` 字段关联，按 `request_id` 检索。

## 5. AgentLogger 类

```python
class AgentLogger:
    def __init__(self, session_id, agent_id="main", parent_logger=None)
    def new_ask(ask_index)                                        # 切换到新的 log 文件
    def log_api_request(request_id, model)                         # API 调用开始
    def log_api_response(request_id, latency_ms, tokens, llm_ref) # API 响应
    def log_tool_call(request_id, tool_name, params, duration, success)
    def log_sub_agent(request_id, name, sub_type, prompt_summary)
    def log_error(request_id, error_type, message)
    def save_llm_content(request_id, model, messages, response, usage)
```

- 每个写方法调用后立即 `flush()`
- `parent_logger` 非空时，日志写入 parent 的 session 目录
- Sub-agent 的 `agent_id` 格式：`main.explore_1`、`main.skill_fork_2`

## 6. tracer 改造

`SessionTracer.finalize()` 不再批量写文件，改为：
- `on_turn_end` 写 turn 行到 trace JSONL
- `on_first_token` 更新后不写文件（等 turn_end 统一写）
- ask 概览行在最后写入

## 7. 实现顺序

```
Task 1: 实现 logger.py — AgentLogger 类，实时 JSONL 写入
Task 2: 改造 tracer.py — 改为实时写入，移除 finalize() 批量逻辑
Task 3: 集成到 agent.py — 在 API 调用、工具执行、sub-agent 启动处调用 logger
Task 4: 集成到 session.py — save_session 时创建 logger，restore 时恢复
Task 5: 端到端验证 — 跑一个 ask，确认日志实时生成且 crash-safe
```
