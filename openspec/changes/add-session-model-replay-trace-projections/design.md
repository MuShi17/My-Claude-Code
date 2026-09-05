## Context

C05 提供按 ordinal/high-water 的 immutable prefix，C06/C07 已定义 model/tool/run 生命周期；现有 `session.py` 保存 JSON 消息快照，`tracer.py` 维护独立 turn 文件。C08 必须把这些读取模型改为 canonical-derived，同时保留 legacy 只读兼容直到 C10。

## Goals / Non-Goals

**Goals:**

- 提供三个职责明确、可从同一 prefix 重建的 projection。
- 统一 model message 的 visibility、partial、thinking signature 和 tool pairing，支持双 provider parity。
- 让 trace 只用于诊断，带 high-water/version/digest，不产生执行副作用。

**Non-Goals:**

- 不修改 RuntimeEvent 语义、不把 projection 作为第二事实源、不依赖 legacy logger。
- 不在本 change 实现 compaction/archive/capture（C09）或启动 recovery/resume（C10）。

## Decisions

1. **共享 reducer 输入、分离输出。** 三个 projection 使用同一个按 ordinal 的 event iterator，各自维护输出 state；比互相读取 projection 更不易形成隐式依赖。
2. **明确 model visibility。** `modelVisibility` 决定是否进入 replay，诊断/hidden event 仍进入 trace；partial 只进入 bounded recovery metadata，不能成为最终消息。
3. **按 identity 配对工具。** 用 invocation/tool-call identity 配对 call/result，不按数组位置或时间，处理并行工具和 child run。
4. **digest 包含输入高水位与 projection version。** stable digest 对规范化输出计算，报告可重建性；非确定时间/随机值不进入比较字段。
5. **损坏严格、边缘显式。** 影响语义的 decode/unmatched 记录 diagnostic；只允许安全 placeholder，不静默丢 event 或从 legacy 补回。

## Risks / Trade-offs

- [Provider message constraints 不同] → provider-neutral replay 后由 adapter 做最小合法化，稳定 parity 比较语义字段。
- [历史 canonical schema 演进] → C01 schema_version 与 projection version 分离；升级需兼容 reader 或明确 migration error。
- [partial 与 final 合并复杂] → 使用 high-water/partial snapshot，只有 final/terminal boundary 进入可执行 replay。
- [trace 读取意外触发动作] → projection API 只读，测试断言 event count/digest 和 tool invocation 未改变。

## Migration Plan

先实现离线 reducers 和 golden projection，再把 session/model context 切换为 canonical-first；Tracer 继续作为 legacy shadow/兼容读取。C09 为大结果提供 ref 后，projection 只展开受限 preview；C10 接入启动恢复和 session v2。
