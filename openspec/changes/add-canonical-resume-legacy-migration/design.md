## Context

C05 提供 event store/high-water/seal，C07 提供 parent/attempt/terminal，C08 提供 Session/ModelReplay projection，C09 提供 checkpoint/artifact refs。当前 `session.py` 以 JSON snapshot 保存消息，CLI/Agent restore 依赖旧 session 路径；因此 C10 是读取 authority 和启动安全边界，而不是事件 schema 变更。

## Goals / Non-Goals

**Goals:**

- 从 canonical facts 分类和关闭未封存 run，并使 startup 幂等。
- 生成带来源证明的 session v2，让 `--resume` canonical-first，旧数据可安全只读。
- 在损坏、悬空 ref、迁移失败和回滚时不丢数据、不自动重放副作用。

**Non-Goals:**

- 不实现恢复后继续原 Run 的 continuation；resume 只恢复上下文/启动新的显式 turn。
- 不把 legacy log 反向生成没有证据的 dispatch、tool outcome、usage 或 terminal。
- 不删除、重写或批量迁移历史 legacy 文件。

## Decisions

1. **RecoveryProjection 只读扫描 + 可审计 closure。** 先读取 immutable prefix/partial/refs 分类，再对确定的 open run调用 idempotent terminal API；不让读取逻辑隐式执行工具。
2. **Legacy fallback 显式标识。** canonical 缺失时允许加载旧 message snapshot，但 session 来源/能力限制进入 CLI/UI 状态，且禁止把日志推断成副作用事实。
3. **Session v2 是 cache/projection。** 保存 high-water/digest/projection version，过期则重建；事件 store 是唯一事实，不以 v2 反写 canonical。
4. **Startup 修复按保守策略。** model/permission/dispatch 状态按 evidence 分类；dispatch without outcome 使用 uncertain，不自动 resume/retry。
5. **Atomic snapshot write。** v2 写临时文件并原子替换当前 v2 path，保留旧 snapshot/legacy path 直到新文件完整校验。

## Risks / Trade-offs

- [启动时追加 terminal 仍可能 crash] → terminal append exact idempotency，重复启动只返回已有结果。
- [legacy fallback 看似可继续执行] → 只加载消息，明确 readonly，下一次副作用必须重新经过 C06 durable boundary。
- [旧 session 与 canonical 内容不一致] → canonical-first，报告 stale/diff，绝不静默合并。
- [损坏数据阻塞恢复] → bounded safe partial + 定位 diagnostic，保留 repair/review 入口而不是跳过。

## Migration Plan

先实现 RecoveryProjection 与 v2 snapshot，再把 CLI/Agent restore 路由到 canonical-first；旧路径继续可读。C11 在临时 HOME 中验证 canonical、legacy-only、corrupt、rollback 和重复 startup，确认切换无需手工改用户数据。
