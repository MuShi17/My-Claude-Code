## Context

C08 projection 已确定 high-water/version/digest 和 bounded replay，C05 预留 `llm_captures`，当前 `agent.py` 的 `_persist_large_result` 单独写 `~/.mini-claude/tool-results`，没有统一 hash/ref/atomic protocol。C09 需要兼容该旧目录的只读数据，但为新 archive 建立明确边界。

## Goals / Non-Goals

**Goals:**

- 让 compaction checkpoint 可验证来源和覆盖范围。
- 让大 payload 先原子归档再引用，支持 integrity/size/MIME/redaction metadata。
- 对 LLM capture 提供 off/metadata/redacted 策略，避免默认持久化敏感 wire body。

**Non-Goals:**

- 不把 artifact 内容嵌进 canonical event，不改变 event ordering 或历史。
- 不实现远程对象存储、跨机器 artifact replication、自动清理策略或完整压缩算法。
- 不对 legacy tool-results 历史文件做 destructive migration。

## Decisions

1. **内容寻址 archive。** 脱敏后按 SHA-256/size/MIME 生成 ref，写临时文件、flush/fsync、原子 rename，再提交 metadata；比直接写 placeholder 更能避免悬空引用。
2. **Checkpoint 绑定 high-water + digest。** 摘要 coverage 明确到 event ordinal/turn，recent tail 保持最近可重放窗口；新事件只生成新 checkpoint。
3. **Capture mode 默认最小化。** off 不存 body，metadata-only 存 request shape hash/usage/latency，redacted 才存 bounded body/ref；每条带 policy version。
4. **Store metadata 与文件双边校验。** runtime store 保存 ref/hash/size/metadata，读取时校验文件，任一失败报告 integrity/archive error。
5. **Placeholder 是 projection 输入。** Model/session projection 只显示 bounded preview/ref，显式诊断请求才允许按权限展开 archive，避免上下文不受控增长。

## Risks / Trade-offs

- [归档文件与 SQLite metadata 不一致] → 先文件 commit 后 metadata，启动/recovery 扫描 orphan/悬空 ref；失败不发 placeholder。
- [hash 泄露内容关联] → hash 只对脱敏内容生成，scope/权限和 redaction version 一并保存。
- [摘要不完整] → checkpoint 标记 coverage/high-water/source digest，恢复时不把 summary 当作全量事实。
- [旧 tool-results 格式差异] → C10 仅只读读取并用 bounded compatibility adapter，不混入新 ref schema。

## Migration Plan

先实现 C08 可消费的 reference/placeholder 与 checkpoint builder，再将 `_persist_large_result` 迁移为新 archive facade；旧目录继续只读。C10 resume 使用 checkpoint + recent tail，C11 验证 archive failure、privacy 和 rollback 不影响 canonical history。
