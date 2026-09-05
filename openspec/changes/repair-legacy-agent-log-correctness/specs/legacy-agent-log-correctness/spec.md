## Purpose

在 Canonical Runtime Event shadow 阶段保持既有 JSONL、trace 和 LLM 日志的可读、可关联和可诊断行为，消除会污染 parity 结果的已知正确性缺陷。

## ADDED Requirements

### Requirement: Child logging retains all events

当子 Agent 或嵌套 skill 使用 legacy logger 时，系统 MUST 为其建立可写的 ask 生命周期和稳定的 parent/child 关联；子 Agent 的 API、工具、错误和终态事件不得因未调用 `new_ask` 而丢失。

#### Scenario: Child run writes an ask

- **WHEN** 父 Agent 启动子 Agent 并执行一次完整 chat
- **THEN** 子 Agent 的 legacy ask 文件包含其实际事件，且事件能通过 session/agent/parent identity 与父运行关联

#### Scenario: Nested child fails before normal completion

- **WHEN** 子 Agent 在 provider 或工具阶段抛出异常
- **THEN** 已发生的 legacy 事件仍被 flush，且失败记录不会因为 child logger 没有当前 ask 而丢弃

### Requirement: Legacy timestamps carry actual UTC milliseconds

新写入的 legacy 事件 MUST 使用可解析的 UTC timestamp，并反映真实的毫秒部分；同一进程内事件的时间字段不得统一伪装为 `.000Z`。

#### Scenario: Sub-second events are distinguishable

- **WHEN** 两个 legacy 事件在同一秒内相隔非整秒发生
- **THEN** 至少一个事件的序列化 timestamp 反映非零毫秒，且两者仍符合 UTC 格式

#### Scenario: Clock is injected in a test

- **WHEN** 测试注入固定 clock 生成 legacy 事件
- **THEN** 输出 timestamp 与注入值一致且格式稳定，不读取真实墙钟

### Requirement: Model responses link to LLM records

每条 legacy `api_response` MUST 在适用时包含可解析的 `llm_ref`，并与对应 session/model、ask 和 LLM JSONL 记录关联；缺失 LLM 记录时不得伪造成功引用。

#### Scenario: Response has an LLM reference

- **WHEN** provider response 已写入 LLM JSONL
- **THEN** api response 记录包含指向该记录的稳定引用，且引用可通过 session/ask 解析

#### Scenario: LLM write fails

- **WHEN** LLM capture 写入失败
- **THEN** api response 保留可诊断错误状态或明确缺失引用，不声称存在不可读取的 LLM 记录

### Requirement: Traces persist completed tool details

legacy trace 的 turn 记录 MUST 在 turn 结束后能够包含已完成的工具调用、输入摘要、结果摘要、错误和耗时；后续 `on_tool_end` 更新不得只留在内存而与已落盘 turn 分叉。

#### Scenario: Turn includes a tool outcome

- **WHEN** 一个 turn 执行一个或多个工具并随后结束
- **THEN** 读取该 turn 的 trace 能看到每个工具调用与 outcome 的稳定关联

#### Scenario: Tool fails after turn event

- **WHEN** 工具在 turn 已开始后失败
- **THEN** trace 最终记录包含失败类别和 tool identity，且 ask summary 与 turn 状态一致

### Requirement: Legacy flush is failure-visible and backward compatible

正常结束、异常、取消和进程即将退出时，legacy logger/tracer MUST 尝试 flush 已生成数据；既有文件命名、字段兼容和公开 Agent API MUST 保持不变，历史文件 MUST 不被重写。

#### Scenario: Exception flushes partial legacy data

- **WHEN** chat 在模型或工具阶段抛出异常
- **THEN** 已发生的 legacy JSONL/trace 数据可在进程外读取，并保留明确的未完成或错误信息

#### Scenario: Existing legacy reader still works

- **WHEN** 使用当前支持的 legacy reader 读取修复后新文件和旧文件
- **THEN** reader 不需要迁移旧文件即可解析必要字段，新增字段不会破坏已有行为
