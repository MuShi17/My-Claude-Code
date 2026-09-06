# Agent Log Canonical Runtime Event 实施基线（历史）

> 本文记录 Item 01 的实施基线和设计参考。它不授权 commit、push、merge、发布、删除数据或清理用户工作区。
>
> 当前运行时状态以 Canonical-only 批次和 [Canonical-only Acceptance Report](agent-log-canonical-acceptance-report.md) 为准；本文中的 shadow/cutover 方案是历史记录。

## 目标仓库

| 项目 | 取证结果 |
| --- | --- |
| 路径 | `D:/PycharmProjects/pythonProject/My-Claude-Code` |
| branch | `main` |
| HEAD | `35a06324e68e2113b93a38b55ae4747030458cdd` |
| remote | `https://github.com/MuShi17/My-Claude-Code` |
| 基线状态 | 取证时源代码工作区 clean；当前 `openspec/changes/` 下的 C01-C11 是本批次由 Codex 创建的变更 artifacts |
| writer | 本批次由当前 Codex task 作为唯一逻辑 writer；其他 Agent/进程不得并行写入 runtime store 或修改本批次源码 |
| worktree | 当前仓库作为实施 worktree；开始任何与用户修改重叠的实现前必须重新检查 diff，禁止覆盖、reset 或清理用户修改 |

批次总览中此前记录的 `main@331c84...` 和 dirty worktree 与本次实际取证不一致；后续实现以本文记录和每个 change 实施时重新取得的 git 状态为准。

## Maka 只读参考

| 项目 | 取证结果 |
| --- | --- |
| 路径 | `D:/PycharmProjects/pythonProject/maka` |
| branch | `main` |
| fixed commit | `1e543a7385614adc671623efe2586cf5317582d4` |
| remote | `https://github.com/apache/maka.git` |
| 参考文件 | `docs/blogs/log-is-the-runtime.zh-CN.md`、`docs/architecture/runtime-core-architecture-draft.zh-CN.md`、`packages/core/src/runtime-event.ts`、`packages/core/src/runtime-event-store.ts`、`packages/storage/src/sqlite-runtime-schema.ts`、`packages/storage/src/sqlite-runtime-store.ts`、`packages/runtime/src/agent-run-recovery.ts`、`packages/runtime/src/continuation-replay.ts`、`packages/runtime/src/ai-sdk-message-projection.ts` |

Maka 目录只读，不复制其后续提交的漂移设计。

## 采用的概念

- append-only canonical runtime events、分层 session/turn/run/invocation identity。
- store-assigned ordinal、immutable prefix、high-water 和 exact replay idempotency。
- terminal event seal、bounded partial snapshot、durable tool dispatch/outcome。
- provider-aware model/session/trace projections、compaction checkpoint 和 artifact refs。

## 明确不采用

- 完整 Agent Graph、跨机器 continuation authority、workspace version authority。
- 分布式复制、多 writer 协议、复杂 tool journal/reconcile。
- 对不确定副作用的自动重试、bit-exact provider wire replay、runtime-host/UI。

## Change 顺序与边界

历史 Gate 顺序为：C01 契约与基线 → C02 测试夹具 → C03 legacy correctness → C04 domain/shadow sink → C05 SQLite store → C06 Agent Loop durable boundary → C07 run lifecycle → C08 projections → C09 compaction/artifacts/capture → C10 resume/recovery → C11 parity/cutover。当前实现已追加 Canonical-only 清理批次 C12-C18。

C04 与 C05 可以分别在纯领域层和纯存储层开发，但在 C05 不变量通过前，工具执行路径不得依赖 Canonical Store。C08-C10 只能读取已冻结事件语义；如果必须改变历史语义，需提升 `schema_version` 并回到 C01/C04，而不是修改投影来掩盖缺口。

## 删除与回滚禁令

历史阶段不得删除或原地改写用户数据。当前 Canonical-only 清理同样不删除既有 `logs/`、`traces/`、旧 session、旧根数据库或 tool-results；但已删除运行时对这些格式的读取、写入、fallback 和 authority rollback 代码。
