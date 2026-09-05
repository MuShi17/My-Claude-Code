## Purpose

为 Agent Loop 提供一个 provider-neutral、可持久化、可重放并能支撑恢复与诊断投影的 Canonical Runtime Event 事实契约，同时保留旧日志的兼容边界。

## ADDED Requirements

### Requirement: Canonical event envelope is self-describing

每个 canonical event MUST 包含 schema version、唯一 event id、session/turn/run 坐标、创建时间、partial 标记、来源与作者、model visibility、content/actions/refs 中的语义载荷，并能被稳定编码和校验。

#### Scenario: Provider-neutral model event

- **WHEN** Anthropic 或 OpenAI provider 产生一段模型文本、thinking 或 function call
- **THEN** 系统记录具有相同 canonical envelope 语义的事件，provider-specific 原始字段只能作为受控引用或 metadata 保存

#### Scenario: Invalid envelope is rejected

- **WHEN** 事件缺少必需身份、载荷类型不匹配或 schema version 不受支持
- **THEN** canonical 写入被拒绝并返回可区分的契约错误，不能静默生成半结构化事实

### Requirement: Event identity and ordering are explicit

事件 MUST 使用分层身份区分 session、turn、run、invocation 和 tool call；持久化顺序 MUST 由 writer/store 分配的单调 ordinal 表达，不能依赖时间戳排序，读取结果 MUST 可按 ordinal 重建 immutable prefix 和 high-water。

#### Scenario: Same timestamp remains ordered

- **WHEN** 两个事件拥有相同时间戳但先后写入
- **THEN** 读取结果仍按显式 ordinal 保持唯一且稳定的先后顺序

#### Scenario: Child run is addressable

- **WHEN** 一个子 Agent run 由父 run 启动
- **THEN** 其事件同时带有自身 run identity 和可查询的 parent run identity，而不会混入父 run 的事件序列

### Requirement: Semantic payloads cover runtime boundaries

契约 MUST 能表达 text、thinking、function call、function response、error、invocation lifecycle、token usage、permission、tool dispatch、tool outcome、compaction checkpoint 和 artifact reference；事件不得把工具副作用的意图与结果伪装成同一条不可区分文本。

#### Scenario: Durable tool boundary can be represented

- **WHEN** 模型请求工具并获得结果
- **THEN** 事件序列能够分别表达 function call、权限决定、tool dispatch、工具 outcome 和 function response，并保留关联 call identity

#### Scenario: Failure remains observable

- **WHEN** provider、权限判断或工具执行失败
- **THEN** 系统记录带有稳定错误类别和关联 identity 的 error/outcome 事件，而不是只写一条最终文本

### Requirement: Partial and terminal semantics are unambiguous

流式中间事件 MUST 可标记为 partial；partial 不能被当成已完成的模型消息。每个 run MUST 最终通过唯一的 terminal event 表示 completed、failed、cancelled、aborted 或 budget_exceeded；终态之后不得追加该 run 的语义事件。

#### Scenario: Partial stream does not become a message

- **WHEN** provider 在最终响应前发送增量文本或未完成 tool arguments
- **THEN** 读取模型消息时忽略或合并 partial，不能将未完成载荷作为可执行的最终 tool call

#### Scenario: Terminal event seals a run

- **WHEN** run 写入合法 terminal event
- **THEN** run 被封存，重复写入相同 terminal identity 是幂等的，不同 terminal 或后续普通事件被拒绝

### Requirement: Canonical encoding and privacy policy are deterministic

canonical 编码 MUST 对同一语义使用稳定字段顺序和类型表示；敏感 request/response、环境变量、凭据和工具参数 MUST 按统一 redaction policy 处理，并在需要时仅保留 hash、size、MIME、artifact ref 或 bounded placeholder。

#### Scenario: Redaction is applied before persistence

- **WHEN** event payload 包含 API key、token、密码或受保护工具参数
- **THEN** canonical 与 legacy shadow 写入均不包含原始敏感值，并保留可诊断的 redaction metadata

#### Scenario: Stable digest ignores nondeterministic presentation

- **WHEN** 相同事件以不同 JSON 字段插入顺序编码
- **THEN** canonical digest 相同，便于 replay、projection rebuild 和 shadow parity 比较

### Requirement: Compatibility and migration boundaries are explicit

Phase 1 MUST 支持 canonical 与 legacy 并行写入；旧 JSONL、traces、llm 和 session 数据 MUST 保留且可只读读取。切换或失败时 MUST 能通过配置回到 legacy authority，且不得通过删除或改写 canonical 事实实现回滚。

#### Scenario: Shadow write failure follows the frozen policy

- **WHEN** diagnostic legacy sink 写入失败
- **THEN** canonical 事实仍按策略继续或明确失败；诊断失败不能悄悄改变模型和工具执行语义

#### Scenario: Rollback preserves evidence

- **WHEN** authority 从 canonical 回退到 legacy
- **THEN** 已写入的 runtime event 数据库、旧日志和 session 文件均保持可读，回退只改变读取/写入路由
