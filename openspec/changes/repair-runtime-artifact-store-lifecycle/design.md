## Context

当前 `Agent._setup_runtime_facade()` 在没有 caller-owned Store/Sink 时为每轮 `chat()` 创建 `SQLiteRuntimeStore`，并第一次创建 `ArtifactArchive(metadata_store=该 Store)`。`chat()` 的 `finally` 随后关闭自建 Emitter/Store，但没有清理 Archive。下一轮虽然创建了新的 Store，Archive 仍持有旧 Store 的引用；只有结果超过 `DurableToolBoundary.max_result_bytes` 时才会触发该失效引用。

Maka 将 `ToolRuntime` 限定为一个 turn/一次运行，Host 长期持有 Artifact/Runtime Store；turn 结束只等待工具 settlement，Host drain 后才关闭 Store。本 change 将同一边界应用到 Python Agent，同时保留现有 canonical event、SQLite 和 artifact 契约。

## Goals / Non-Goals

**Goals:**

- 同一 Agent 的多个用户 turn 复用同一个有效的 canonical Store 和 Archive。
- 让 Archive 的 metadata store、Emitter 的 sink 和 Agent 当前 canonical sink 保持对象身份一致。
- 让 Agent 在会话结束时以明确、幂等的入口关闭自己拥有的资源。
- 保持 caller-owned 资源的所有权隔离，并覆盖 parent/sub-agent 共享 Store 的场景。
- 归档失败继续 fail closed：不返回没有实际归档对象的 ref，也不把底层异常当作成功结果。

**Non-Goals:**

- 不改变大结果阈值、`read_file` 输出上限、artifact 文件格式或 SQLite schema。
- 不把直接影响下一轮模型输入的同步大结果归档改成 fire-and-forget；Maka 的附属 artifact warning 语义不直接套用到该关键路径。
- 不增加自动重试、不删除或迁移历史 artifact/legacy tool-results 文件。
- 不引入跨进程 Store、远程对象存储、continuation 或新的恢复协议。

## Decisions

### 1. 会话级 Store，turn 级 Runtime Context

Agent 第一次需要 canonical runtime 时惰性创建 Store/Archive/Emitter，后续 `chat()` 只重建 `RunContext`、`RunStateGuard`、`ModelCallRecorder` 和 `DurableToolBoundary`。`chat()` 的终结流程只执行 terminal finalize、flush 和 snapshot，不关闭会话级资源。

备选方案是维持每轮关闭 Store，并在关闭时把自动创建的 Archive 置空、下一轮重新绑定。该方案能修复当前复现，但仍把会话级 Archive/Store 错误地建模为 turn 资源，也会继续产生不必要的连接创建和资源切换，因此只作为紧急回滚方案，不作为最终设计。

### 2. 所有权显式化

- Agent 自建 Store 时设置 `runtime_store_owned=True`，只有 Agent 的关闭入口可以关闭它。
- caller 传入的 `runtime_store` 或 `runtime_sink` 始终视为外部资源，Agent 只 flush，不 close。
- 自动创建的 Archive 与自建 Store 同生命周期；caller 传入的 Archive 不被替换、不被关闭。
- Parent 传给 sub-agent 的 Store/Archive 视为 caller-owned，sub-agent 结束 turn 不得关闭共享资源。

### 3. 身份一致性优先于隐式重绑

Runtime Facade 首次建立时记录 canonical sink 身份和自动 Archive 的 metadata-store 身份。后续 setup 若发现 Emitter sink、Runtime Store/Sink 或自动 Archive 的绑定发生变化，直接报告资源错配并要求重新创建 Agent；不在隐藏状态下自动 reopen 一个已关闭的 SQLite 对象。若未来需要切换 session，应显式创建新的 Agent/Facade。

### 4. 显式异步关闭

增加 `Agent.aclose()`，顺序为：停止/断开 Agent 持有的 MCP 连接，flush canonical sink，在 Store 仍可读时完成 snapshot，最后只关闭 Agent-owned Emitter/Store，并以幂等方式清理引用。关闭后的 Agent 不允许再次 `chat()`。

CLI 的 REPL 和 one-shot 入口使用 `try/finally` 调用 `aclose()`；resume 打开的 caller-owned Store 由 CLI 外层负责关闭，避免双重所有权。

### 5. 保持 Archive 的原子与 fail-closed 语义

`DurableToolBoundary` 仍在生成 bounded outcome 前同步调用 Archive。文件/metadata/runtime metadata 任一提交失败时，不产生 `bounded_ref`；现有 `archive_error` 结果和 canonical failure 语义保持不变。若失败前已留下完整的 content/metadata 文件，后续对同一内容的显式归档可利用 content-addressed existing-object 分支补写 metadata mirror，不执行自动重试或删除。

## Risks / Trade-offs

- [Agent 关闭入口遗漏会造成 SQLite 句柄延迟释放] → REPL、one-shot 和 resume 分别增加 `finally`，并增加幂等 close 测试。
- [共享 Store 被 sub-agent 误关闭] → 所有权测试覆盖 parent/sub-agent，关闭逻辑只依据 `runtime_store_owned`。
- [Store/Sink 被外部提前关闭] → Facade 身份检查快速失败；不尝试隐式 reopen，避免猜测调用方的资源边界。
- [改变 Store 跨轮复用可能暴露旧测试资源泄漏] → 为直接构造 Agent 的测试增加明确 cleanup；不改变 caller-owned context-manager 的行为。
- [归档失败时文件与 runtime metadata 暂时不一致] → 保持无悬空 ref；保留完整文件，后续显式 content-addressed 归档可补齐 mirror，不做破坏性清理。

## Migration Plan

1. 创建本 change 的 delta contract，并在实现前通过 strict OpenSpec validation。
2. 修改 Agent Facade 和 CLI 收尾，保持现有 archive failure contract。
3. 添加单元/集成回归，验证两轮大结果、外部 Store 所有权、关闭和 reopen mirror。
4. 运行定向测试、全量 Python 测试和 OpenSpec strict validation。
5. 若出现回归，回滚仅限本 change 的代码/测试/工件；不删除用户的 runtime.sqlite 或 artifact 文件。

## Open Questions

无。MVP 使用“关闭后不可复用”的 Agent 语义；需要切换 session 时创建新的 Agent，不在本 change 引入 reopen API。
