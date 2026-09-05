## Purpose

让 Agent 从 canonical runtime facts 的稳定 high-water 恢复 session 和下一轮上下文，安全处理未封存、partial、损坏与不确定工具状态，同时为历史 legacy 数据保留只读兼容。

## ADDED Requirements

### Requirement: Recovery classifies every discovered run

恢复扫描 MUST 按 canonical events、run state、partial snapshot 和 refs 将运行分类为 terminal、open、partial-only、unmatched/uncertain、corrupt 或 legacy-only，并返回 high-water、diagnostic 和 recommended action。

#### Scenario: Completed run is discovered

- **WHEN** store 包含合法 completed terminal 和完整 event prefix
- **THEN** recovery 将其标为 terminal/complete，可直接生成 session projection，不追加虚假终态

#### Scenario: Partial-only run is discovered

- **WHEN** 运行只有 partial snapshot 或未封存流式事件
- **THEN** recovery 标为 partial/open，保留 bounded preview，不声称模型 call 或工具已完成

### Requirement: Startup closes unresolved runs idempotently

启动恢复 MUST 对按策略可判定的 open run 追加一次 failed、cancelled 或 aborted terminal；对 dispatch 已 durable 但 outcome 未知的工具 MUST 保守标记 uncertainty，不自动重新执行副作用。

#### Scenario: Startup sees an interrupted model run

- **WHEN** 上次进程在 provider 调用中断且没有 terminal
- **THEN** 启动可幂等追加 aborted/failed terminal，保留 partial/usage/error evidence，重复启动不会追加第二个 terminal

#### Scenario: Startup sees uncertain tool dispatch

- **WHEN** dispatch event 已 durable 但 tool outcome 缺失
- **THEN** recovery 输出人工/上层可处理的 uncertain 状态，绝不自动再次调用该工具

### Requirement: Session snapshot v2 is canonical-derived

新 session snapshot MUST 标明 v2、source high-water、source digest、projection version 和覆盖 session/turn；其消息内容 MUST 来自 canonical projection，不能把 snapshot 当作独立事实源。

#### Scenario: Snapshot is regenerated

- **WHEN** 从相同 canonical high-water 重建 session v2
- **THEN** 消息、metadata 和 digest 稳定，snapshot 的变化不会改写 event store

#### Scenario: Snapshot is stale

- **WHEN** snapshot high-water 小于 store 当前 high-water
- **THEN** resume 检测到 stale 并从 canonical 增量/全量重建，而不是直接使用过期消息

### Requirement: CLI resume is canonical-first with safe fallback

`Agent.restore_session` 与 CLI list/latest/resume MUST 优先查询 canonical store/projection；仅当没有 canonical 数据时才只读读取旧 session/log/traces/llm，并明确标识来源和兼容限制。

#### Scenario: Canonical session exists

- **WHEN** 用户运行 `--resume` 且 canonical events 可读
- **THEN** 选择最新合法 canonical session/run，恢复的 model messages 来自 projection，不读取 legacy 补事实

#### Scenario: Only legacy session exists

- **WHEN** 用户恢复一个只有旧 session.json/日志的会话
- **THEN** CLI 以 legacy-readonly 标识加载可解析消息，不生成 fabricated tool_dispatch 或自动迁移副作用

### Requirement: Corruption and migration are non-destructive

遇到 schema/version/corruption/ref 错误时，恢复 MUST 返回可定位诊断并保留原数据库和旧文件；迁移只能通过新增 v2 projection/metadata 或事务化步骤完成，不得删除历史事实。

#### Scenario: Canonical row is corrupt

- **WHEN** resume 读取到无法校验的 event row 或 artifact ref
- **THEN** CLI 返回清晰的 recovery diagnostic/安全 partial 状态，原始数据仍可供修复工具读取

#### Scenario: Rollback after failed migration

- **WHEN** session v2 生成失败或 authority 开关回退
- **THEN** 旧 session/legacy reader 仍可启动，runtime.sqlite 与旧文件不被删除或原地破坏
