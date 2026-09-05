## Context

C01 的事件契约和 C03 的 legacy correctness 是前置输入。项目当前不存在 RuntimeEvent、EventSink 或 redaction 模块，Agent Loop 在 `agent.py` 中直接进行 provider-specific 日志调用；C05 将在本接口之下提供 SQLite durability。

## Goals / Non-Goals

**Goals:**

- 提供单一、可测试的 domain emission API，所有 provider/child/tool 共享身份和校验。
- 让 canonical、legacy 和 recording sink 可组合，且每个 sink 有独立 failure policy。
- 在不依赖 SQLite 的情况下验证编码、脱敏、映射和 shadow parity 基础。

**Non-Goals:**

- 不实现事务、ordinal 分配、run seal 或高水位读取；它们属于 C05。
- 不在本 change 重写 provider lifecycle 或工具执行顺序；属于 C06/C07。
- 不持久化完整敏感 wire payload，不复制 Maka 的 Tool Journal。

## Decisions

1. **不可变 domain event。** 用 frozen/dataclass-like value object 加验证和稳定 serializer，避免 sink 在写入时改变语义；dict 只作为边界输入输出。
2. **EventSink 是最小协议。** 只暴露 emit/flush/close 及可区分异常，recording sink 用于 contract tests，SQLite sink 后续实现同一协议。
3. **Composite 顺序为 canonical 先、legacy 后。** 先确保 canonical 成功，再执行诊断 shadow；部分 legacy 失败不能让已 durable canonical 失效。
4. **Redaction 在公共门面执行。** 所有 sink 共享脱敏后的 event，避免一个 sink 忘记保护；原始值不进入 event object 的持久化生命周期。
5. **引用替代大载荷。** domain 层只携带 bounded placeholder/ref metadata，实际原子归档由 C09 完成。

## Risks / Trade-offs

- [legacy adapter 丢失新语义] → 保留 canonical identity 和明确 mapping gap，C11 只比较定义的 stable projection 字段。
- [sink 顺序产生半双写] → canonical failure fail-closed，legacy failure 可观测；C05/C11 增加 crash/failure tests。
- [domain 模型与 C01 漂移] → C02 contract fixture 作为冻结样本，schema_version 变更必须回到 C01。
- [脱敏误报或漏报] → 固定 pattern/field-path 测试，加 redaction version 和可审计统计。

## Migration Plan

先并行加入 domain/facade 与 recording/legacy adapter，保持旧调用可用；C06 再把 provider 分支改为只通过 facade 发射，C05 完成后替换 canonical sink 为 SQLite。任何阶段都不删除旧 logger 数据。
