## 1. 领域模型

- [x] 1.1 实现 immutable RuntimeEvent、RunContext、content/actions/refs 类型和 schema validation，并用 C02 invalid/valid envelope fixtures 验证错误类别与稳定序列化
- [x] 1.2 实现统一 event/run/invocation/tool identity factory 与 canonical encoder/digest，并验证双 provider、child run 和不同字段顺序产生预期稳定结果
- [x] 1.3 实现 redaction policy、redaction version 和 bounded reference/placeholder 处理，并用凭据、大结果和嵌套参数 fixtures 验证原值不落盘

## 2. Sink 与 shadow facade

- [x] 2.1 定义 EventSink 协议、recording/memory sink 与可区分 canonical/diagnostic sink errors，并运行纯 domain contract suite 验证
- [x] 2.2 实现 canonical、legacy adapter、composite/shadow sink 的调用顺序和失败策略，并用 canonical/legacy fault fixtures 验证 fail-closed/fail-open 边界
- [x] 2.3 将 model/tool/lifecycle event 的 legacy mapping 接入现有 logger/tracer 兼容形状，并用现有 reader 和 C03 regression fixture 验证旧文件可读

## 3. 接口 Gate

- [x] 3.1 导出供 C05/C06 使用的最小 facade、异常和配置选项，并验证无 SQLite 时 recording sink 可独立运行
- [x] 3.2 运行 `openspec validate --changes --strict` 并确认 C04 tasks 不提前改变 Agent Loop 的 durable 执行权威
