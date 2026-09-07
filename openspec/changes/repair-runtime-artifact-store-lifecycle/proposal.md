## Why

多轮 REPL 会话中，Agent 在第一轮结束时关闭了自建的 SQLite Runtime Store，但保留了绑定该 Store 的 `ArtifactArchive`。第二轮大工具结果进入归档路径后，Archive 仍向已关闭的 Store 写入元数据，最终暴露 `runtime store is closed`。需要把单轮运行状态与会话级持久化资源分离，消除失效 Store 引用。

## What Changes

- 将自建 Runtime Store、Artifact Archive、Runtime Event Emitter 和 LLM capture manager 的生命周期提升到 Agent/CLI 会话级。
- 让 `chat()` 只创建和终结当前 turn 的 Runtime Context、RunStateGuard 和 DurableToolBoundary，不再关闭会话级 Store。
- 增加显式、幂等的 Agent 异步关闭入口，并在 REPL、one-shot 和 resume 入口正确释放各自拥有的资源。
- 明确 caller-owned 与 agent-owned Store/Sink/Archive 的所有权，禁止 Agent 关闭或替换调用方资源。
- 校验 Runtime Emitter、Archive 和当前 Store 的对象身份；发现运行时资源错配时快速失败，不继续写入错误 Store。
- 保持大工具结果“先归档、再生成 bounded ref”的现有契约；归档失败不得生成悬空 ref。
- 增加连续多轮、关闭顺序、调用方所有权、归档失败和重开后的 metadata mirror 回归测试。

## Capabilities

### New Capabilities

- `runtime-artifact-store-lifecycle`: 定义 Agent turn 级运行资源与会话级 Runtime Store/Artifact Archive 的生命周期、所有权和一致性边界。

### Modified Capabilities

<!-- No existing capability requirements are changed. The existing bounded archive failure contract is preserved. -->

## Impact

- 影响 `src/mini_claude/agent.py` 的 Runtime Facade 初始化、`chat()` 收尾和资源关闭入口。
- 影响 `src/mini_claude/__main__.py` 的 REPL、one-shot 和 resume 资源收尾。
- 新增 Runtime Store/Artifact Archive 生命周期与多轮归档集成测试。
- 不新增依赖，不改变 SQLite schema、artifact schema、API 配置、16KB durable result boundary 或历史 artifact 文件。
