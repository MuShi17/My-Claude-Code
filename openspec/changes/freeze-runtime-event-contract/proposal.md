## Why

当前日志事实分散在 AgentLogger 的 ask/LLM JSONL、Tracer 的 turn 文件和 session.json 中，事件没有稳定身份、显式顺序、终态封存或可重放的统一语义；Anthropic 与 OpenAI 两条循环也容易产生不一致。必须先冻结 Canonical Runtime Event 的可观察契约，才能让后续存储、投影、恢复和切换在同一事实源上演进。

## What Changes

- 记录实际实现基线、唯一 writer、隔离 worktree 规则以及 Maka 参考仓库的固定只读 commit。
- 定义 provider-neutral 的事件 envelope、分层身份、显式顺序、高水位、partial、终态和引用语义。
- 明确模型、工具、权限、子运行、错误、取消和压缩事件的可观察边界，以及敏感数据脱敏要求。
- 固化 Phase 1 shadow write、legacy 只读兼容、authority 切换和不删除旧数据的回滚规则。
- 将不采用的 Maka continuation、workspace authority、分布式复制和自动副作用重试排除在本批次之外。

## Capabilities

### New Capabilities

- `runtime-event-contract`: Canonical Runtime Event 的字段、身份、排序、内容动作、可见性、终态和隐私契约。

### Modified Capabilities

<!-- No existing OpenSpec capabilities are present; legacy behavior is handled by a later change. -->

## Impact

- 影响 `src/mini_claude/agent.py`、`logger.py`、`tracer.py`、`session.py` 及未来的 runtime event/store/projection 模块。
- 影响 JSONL/session 的 shadow 兼容、SQLite 存储 schema、CLI `--resume` 以及测试夹具。
- 不引入第三方依赖；持久化阶段仅允许使用 Python 标准库 `sqlite3`。
