## Context

现状是 `AgentLogger` 以 session 下的 `logs/{ask}.jsonl` 和 `llm/{session}.jsonl` 写入，`_write_event` 在没有当前 ask 时直接返回；`Agent.chat()` 为每轮创建 main logger，子 Agent 通过 parent logger 创建但不会自动开始 ask。`Tracer.on_turn_end` 立即写 turn，`on_tool_end` 只修改内存 `_turns`，因此工具详情可能不会出现在已落盘 trace。

## Goals / Non-Goals

**Goals:**

- 在不改变公开 API 和历史文件的前提下，修复 child、timestamp、llm_ref、trace tool details 和异常 flush。
- 让旧格式成为可靠的 shadow 输出，便于 C11 做 stable parity。

**Non-Goals:**

- 不在本 change 引入 canonical event schema、SQLite 或新的 authority。
- 不把旧 JSONL 反向迁移成新事件，不改变旧 reader 的目录布局。
- 不解决跨进程并发写入；如需扩展由 C01 重新冻结。

## Decisions

1. **Logger 的 ask 生命周期由调用边界保证。** child chat 开始时创建自己的 ask/文件句柄，父 logger 只提供 session/root 配置和关联信息；比让 `_write_event` 静默创建隐式文件更容易发现生命周期错误。
2. **时间从可注入 UTC clock 生成。** 使用 timezone-aware datetime/高精度 `time.time_ns` 转换为毫秒，测试可固定 clock；避免格式化硬编码 `.000Z`。
3. **先写 LLM capture 再关联 response。** response 仅在 capture 成功并拿到 ref 后写 `llm_ref`；失败时保留 error/missing 状态，避免悬挂引用。
4. **Tracer 延迟 turn 落盘并支持安全重写。** 在 turn end 先合并工具 detail，再原子替换对应记录或用兼容的 finalization 记录；具体选择以现有 trace reader 的格式约束为准，不能破坏旧文件。
5. **flush 在 finally 边界执行。** Agent chat、child chat 和 tracer close 都走幂等 flush；flush 错误进入诊断路径并不能掩盖原始模型/工具错误。

## Risks / Trade-offs

- [延迟写 turn 丢失最后一条记录] → 临时文件/原子替换，并在异常和取消的 finally 中写 partial/error。
- [新增毫秒字段改变 golden] → C02 comparator 比较格式和稳定相对顺序；C11 仅对允许字段忽略非确定时间。
- [父子文件路径改变] → 先针对现有 reader 建立兼容测试，必要的新增字段只追加不重命名。
- [用户已有 agent.py 修改冲突] → 实施时使用已记录的隔离 worktree，禁止覆盖当前用户修改。

## Migration Plan

只修复新写入路径；历史 `logs/`、`llm/`、`traces/` 保持原样并继续只读。C04 以该 legacy writer 作为 diagnostic sink，若修复导致公开行为回归可关闭 shadow 而不丢失历史数据。
