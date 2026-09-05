## Purpose

为 Canonical Runtime Event 重构提供确定性、无需外部服务且可覆盖故障边界的测试基础，使不同 provider、projection 和 legacy shadow 的比较具有可重复证据。

## ADDED Requirements

### Requirement: Test coverage is organized by runtime boundary

测试资产 MUST 明确覆盖 envelope/schema、不变量与顺序、双 provider 生命周期、权限和 durable tool boundary、子 Agent/终态、projection/replay、recovery、compaction/artifact 和 privacy/redaction 边界。

#### Scenario: New boundary has a test owner

- **WHEN** 新增一个 canonical runtime boundary 或事件类型
- **THEN** 测试矩阵中出现对应的 fixture、contract 或 integration 验证项，并标明其所属 change

#### Scenario: Error paths are included

- **WHEN** provider、sink、SQLite、权限或工具执行出现错误
- **THEN** 矩阵包含该错误的事件序列、预期终态和可恢复性断言

### Requirement: Core fixtures are deterministic and offline

测试 MUST 能注入固定 clock、ID factory、provider response、tool result、permission decision 和环境，不得要求真实 API key、网络或当前系统时间才能运行核心契约测试。

#### Scenario: Repeated fixture run is identical

- **WHEN** 同一个 fixture scenario 在干净临时目录运行两次
- **THEN** canonical 稳定字段、event ordering、projection digest 和 golden output 相同

#### Scenario: Provider implementations share one script

- **WHEN** 用同一 logical conversation 分别驱动 Anthropic 与 OpenAI fake provider
- **THEN** 两者产生可比较的 canonical 生命周期，而不是要求测试读取 provider 私有 wire payload

### Requirement: Golden artifacts are versioned and privacy-safe

golden events、provider messages、traces 和 compaction outputs MUST 使用固定 schema version、稳定字段和脱敏内容；测试输出不得包含真实凭据、用户 secrets 或未受控的大结果。

#### Scenario: Golden mismatch identifies stable fields

- **WHEN** projection 或 parity 与 golden 不一致
- **THEN** 失败报告指出 event kind、identity、ordinal、status、refs 等稳定字段的差异，并忽略允许的时间/随机字段

#### Scenario: Secret is rejected from a fixture

- **WHEN** fixture 输入含有看似凭据的字段
- **THEN** builder 在生成 golden 前执行 redaction 或拒绝不安全输入，且测试产物中不出现原值

### Requirement: Storage and failure injection are controllable

测试 MUST 能为临时 SQLite 注入重复 append、写失败、重开、partial-only、terminal 重复和损坏记录场景，并能验证 sink 的 canonical fail-closed 与 diagnostic fail-open 策略。

#### Scenario: Duplicate append is exercised

- **WHEN** 同一个 event identity 被 fixture 重放到 store
- **THEN** 测试可断言 exact replay 幂等、conflicting payload 拒绝和 ordinal 不重复

#### Scenario: Crash boundary is reproducible

- **WHEN** fault hook 在 dispatch 前、工具执行中、outcome 写入前或 terminal 写入前触发
- **THEN** 测试能够重开临时 store 并检查 recovery projection 得到预期 open/partial/terminal 分类

### Requirement: Contract and CLI smoke commands are explicit

测试入口 MUST 能单独运行纯契约测试、故障/存储测试、projection/recovery 测试和不依赖真实 API 的 CLI `--resume` smoke；失败必须返回非零退出码。

#### Scenario: Pre-implementation contract test can fail

- **WHEN** C02 在后续实现尚未存在时运行 contract suite
- **THEN** 缺失能力以明确的失败呈现，而不会因为没有测试文件或跳过而误报通过

#### Scenario: CI command is discoverable

- **WHEN** 开发者查看项目测试入口
- **THEN** 文档或命令帮助能指向完整 suite、子集和是否需要网络/API key 的说明
