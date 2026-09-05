## Context

当前 `agent.py` 在 Anthropic/OpenAI 分支中重复处理流式文本、tool calls、permissions、usage 和日志调用；大结果另有 `_persist_large_result`，但没有与 tool dispatch 的统一事实关联。C04 提供 domain facade，C05 提供 durable SQLite append/seal，C02 提供 fake provider 和 fault fixture。

## Goals / Non-Goals

**Goals:**

- 把两个 provider 归一到一个内部 lifecycle recorder 和事件序列。
- 在任何工具函数前完成 permission + durable dispatch，覆盖并行工具、streaming、异常、预算和取消。
- 保持现有工具权限模式和用户可见行为兼容，同时去掉 logger 作为 source of truth。

**Non-Goals:**

- 不引入自动重试不确定副作用、不做 continuation resume、不移植 Maka tool journal。
- 不在本 change 实现 child run terminal state machine（C07）、projection（C08）或 artifact archive（C09）。

## Decisions

1. **Provider adapter → common recorder。** 各 provider 只负责解析其响应为 common chunks/lifecycle，ModelCallRecorder 负责发射统一 events；避免在两条分支中复制顺序逻辑。
2. **Dispatch 是执行前的 durable barrier。** 权限通过后先 append dispatch 并确认提交，再调用 tool runner；不确定是否提交时宁可不执行并进入 recovery，而不是猜测副作用。
3. **Tool call identity贯穿序列。** 使用 invocation/tool-call identity 连接 partial、permission、dispatch、outcome 和 function response，避免按工具名或数组位置配对。
4. **Bounded partials。** 对 streaming delta 按字节/字符上限采样或摘要，完整结果交由 C09；partial 不进入最终 provider message 直到 final boundary。
5. **Budget/cancel 由 recorder 记录后停止。** 检查点在模型返回、工具结果和下一次调用前，状态写入后阻止新副作用；不自动恢复旧 run。

## Risks / Trade-offs

- [两个 provider 解析差异] → common lifecycle contract + 双 provider fixtures + stable parity；保留 provider metadata 便于诊断。
- [dispatch 提交后进程崩溃] → C10 recovery 将其分类为可能已执行/未完成，不自动重放工具；C07 负责明确 terminal。
- [并行工具顺序] → 每个 call 独立 identity，dispatch ordinal 先于执行；投影按事件顺序而非完成时间配对。
- [旧 logger 调用残留] → contract test 搜索/断言 Agent Loop 不依赖其返回值，legacy adapter 只能观察 canonical facade。

## Migration Plan

先在 fake provider 下接入 recorder 和 durable boundary，再逐步替换 Anthropic/OpenAI 两分支；保留旧 logger shadow 和 `_persist_large_result` 的兼容行为，等 C09 artifact ref 具备后再替换大结果表示。C11 通过开关做全场景 parity 后才进入 authority 切换。
