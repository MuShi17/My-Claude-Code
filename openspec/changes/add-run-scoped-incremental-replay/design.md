## Context

See `proposal.md` for the motivation. 当前 `_refresh_provider_context_from_canonical()` 每次调用 `ModelReplayProjection().build(store)`，而 projection 会通过 `read_event_records()` 读取并归约完整事件列表。Maka 的 `ai-sdk-turn.ts` 将 prior replay 与 current-turn durable projection 分开。

## Goals / Non-Goals

**Goals:**

- 一次 Run 内稳定复用 prior prefix；
- current Turn 只消费新增 durable events；
- 增量和 cold replay 使用相同 reducer/transition 语义；
- cursor 失效时安全重建并记录原因；
- 为未来 prefix cache 和长任务性能诊断提供明确指标。

**Non-Goals:**

- 不改变 Canonical Store 的 append-only 事实模型；
- 不保证 Provider SDK 内部完全不复制或不序列化历史消息；
- 不实现分布式共享 cursor 或多进程 writer 协调；
- 不在本 change 中重新定义压缩和 memory event 语义。

## Decisions

### 1. Prior prefix 与 current-turn suffix 分离

Run 开始时以当前 context epoch 的 Canonical prefix 建立 `ReplayCursor`。每次新事件提交后，使用 `read_event_records(after_ordinal=cursor.high_water)` 或等价 API 获取增量，并将新消息追加到 current suffix。

选择按 ordinal 增量读取，是因为 ordinal 是 canonical 因果顺序；不使用 timestamp 或“最后一个 message”作为边界。备选方案是每步完整 Replay，正确但重复成本高。

### 2. Cursor 是可丢弃投影，不是事实源

Cursor 保存 prefix messages、reducer pending state、high-water、source digest、projection version 和 epoch。它可以内存缓存或作为 latest-context projection 保存；任何字段不匹配都触发 cold rebuild，不能自行修补 Canonical history。

### 3. 只在完整 message group 边界暴露 suffix

增量 reducer 继续跟踪未完成 function call、response pairing、thinking/text continuation 和 multi-tool group。尚未满足 Provider 顺序约束的事件保留为 pending/diagnostic，不直接追加为可发送消息。

### 4. Context transition 是 cursor invalidation 边界

前置 change 提交 compaction/microcompact transition 后，旧 cursor 的 epoch 或 source digest 不再适用。新 cursor 从 transition 规定的 effective context 初始化；不通过把旧数组原地改写来隐藏 epoch 变化。

### 5. 诊断只记录摘要

每次请求记录 `prefix_digest`、`source_high_water`、`context_epoch`、`read_event_count`、`projection_ms`、`warm/cold` 和 `rebuild_reason`。不记录 raw provider request，避免诊断数据破坏 privacy/capture boundary。

## Risks / Trade-offs

- [Risk] 当前 Store 缺少 after-ordinal API → 增加只读增量查询，保留 full read 作为明确 cold path。
- [Risk] current suffix 仍需要构造完整消息数组 → 接受 Provider SDK 级别的必要 materialization，优化重点是 ledger scan/reducer，而不是承诺零复制。
- [Risk] warm state 与新 transition 竞态 → 只在 canonical append/transition 成功返回后推进 cursor，并校验 high-water/digest。
- [Risk] 增量 bug 隐藏在长任务 → 每个 fixture 同时执行 warm 和 cold replay，并逐字段/逐字节比较 Provider materialization。

## Migration Plan

1. 增加 cursor 和 Store after-ordinal 读取接口；
2. 为当前 Turn 实现增量 reducer/suffix projection，但保留 cold rebuild fallback；
3. 接入 Agent Anthropic/OpenAI provider loops；
4. 接入 transition epoch invalidation 和 request diagnostics；
5. 增加长任务读取量/投影耗时 benchmark，并比较 warm/cold 等价性。

回滚时将 cursor path 设置为 cold rebuild；保留 cursor diagnostics 和 Canonical facts，不删除或修改历史。

## Open Questions

无。数组拼接成本与 Provider SDK 内部成本不在本 change 的“避免完整 ledger replay”承诺内，已明确为设计边界。

## Post-audit corrections

- A tool-call group is exposed only when all calls currently indexed for its
  invocation have valid durable results. Calls are ordered by model event
  ordinal; results retain durable completion order. This makes inverse
  parallel completion produce the same result as cold replay.
- Response identity maps retain ordered `(event_id, call_id)` pairs. Call IDs
  are scoped labels and are never used as a cross-run global identity.
- The normal suffix query skips the cold-read full sequence audit. Append
  transactions enforce sequence monotonicity; full validation remains on cold
  reads and explicit audit paths, so incremental diagnostics do not hide an
  O(history) SQL scan.
