## Purpose

用可重复的双 provider、故障、隐私和 CLI 场景证明 Canonical Runtime Event 与 legacy shadow 的稳定语义等价，并在不破坏旧数据的前提下安全控制 authority 切换和回滚。

## ADDED Requirements

### Requirement: Shadow parity compares defined stable semantics

parity harness MUST 对每个场景比较 session/model/trace 的 stable identities、event kinds、ordering relations、tool call/result pairing、usage/error/terminal 和 artifact refs；时间、随机 ID 和明确的 provider metadata 差异 MUST 单独标记而不能误报。

#### Scenario: Equivalent Anthropic and OpenAI run

- **WHEN** 双 provider 执行同一固定 turn、工具调用、权限和终态 scenario
- **THEN** harness 报告 stable projection 等价，并列出允许 provider metadata 差异

#### Scenario: Legacy shadow has a missing field

- **WHEN** legacy 输出无法表达一个 canonical lifecycle event
- **THEN** 报告明确 mapping gap，不改变 canonical 事实，也不把 gap 静默算作 parity success

### Requirement: Failure scenarios cover every authority boundary

最终验收 MUST 覆盖 canonical append/commit/lock/corruption、legacy sink、provider、permission、tool dispatch/outcome、child run、terminal、partial、artifact、capture、projection、recovery 和 Ctrl+C/asyncio cancellation 故障。

#### Scenario: Canonical store fails before tool dispatch

- **WHEN** fault injection 使 durable dispatch append 失败
- **THEN** parity/acceptance 证明工具未执行、run 进入明确失败路径、legacy 不会伪造成功副作用

#### Scenario: Diagnostic sink fails

- **WHEN** legacy shadow 或 trace 写入失败
- **THEN** harness 证明 canonical execution/terminal 仍符合 policy，并记录 diagnostic gap

### Requirement: Cutover requires explicit evidence and is reversible

authority 切换 MUST 由显式配置/feature flag 控制，仅在所有阻塞 gap、strict validation、故障测试和 CLI smoke 有证据时允许；rollback MUST 只改变路由，不删除或改写 runtime.sqlite、legacy logs、traces、llm、session 或 artifacts。

#### Scenario: Cutover is attempted with a blocker

- **WHEN** parity 或 recovery 存在未豁免 blocker
- **THEN** 切换被拒绝并输出 gap owner/evidence，不进入 canonical authority

#### Scenario: Rollback after cutover

- **WHEN** canonical-first 运行后执行 rollback flag
- **THEN** 旧会话仍可启动、canonical 新会话仍可读取，所有原始数据保留且无需手工编辑用户文件

### Requirement: Realistic CLI smoke is offline and isolated

切换验证 MUST 在临时 HOME/工作目录中运行真实 CLI list/latest/resume/one-shot smoke，使用 fake provider 或离线 fixture，不依赖用户 API key、真实会话、网络或未声明的外部状态。

#### Scenario: Canonical session resumes in temporary HOME

- **WHEN** 临时 HOME 中预置 canonical runtime.sqlite 并运行 `--resume`
- **THEN** CLI 选择正确 high-water/session projection，输出可观察来源与状态且不访问真实用户数据

#### Scenario: Legacy-only session resumes

- **WHEN** 临时 HOME 只预置 legacy session/log/traces/llm
- **THEN** CLI 安全以 legacy-readonly fallback 启动，且不会生成 fabricated dispatch 或修改旧文件

### Requirement: Gap closure is traceable and non-destructive

每个 parity mismatch MUST 归类为 blocker、允许差异或明确 remaining gap，并链接到 change/task/test evidence；未闭合 blocker 时 MUST 保留 legacy authority，最终报告 MUST 说明数据保留和回滚证据。

#### Scenario: Gap is closed

- **WHEN** 一个此前的 mismatch 被修复并重跑固定 scenario
- **THEN** 新报告包含前后 stable diff、测试命令和闭合证据，不能只删除 golden 差异

#### Scenario: Gap remains at release gate

- **WHEN** 仍有未闭合但被批准保留的 gap
- **THEN** 报告明确其影响和后续 owner，authority 不越过安全边界，legacy/canonical 数据均保持可读
