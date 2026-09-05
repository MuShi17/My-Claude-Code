## 1. 状态机与 identity

- [x] 1.1 实现 main turn/run、child run、parent_run_id、attempt identity 的创建和关联，并用 parent/child fixture 验证事件隔离与查询关系
- [x] 1.2 定义 open/running/awaiting-tool/terminal 状态和合法 completed/failed/cancelled/aborted/budget_exceeded transitions，并用非法跃迁 contract test 验证拒绝
- [x] 1.3 将 C05 seal/idempotency 接入统一 RunStateGuard 与 terminal finalizer，并用重复 terminal/late event fixture 验证唯一封存

## 2. 退出与重试路径

- [x] 2.1 接入 provider error、tool error、permission denial、budget、provider abort、Ctrl+C 和 asyncio cancellation 的统一终态处理，并用 fault matrix 验证每条路径有 terminal evidence
- [x] 2.2 实现显式 provider retry 的 attempt 记录、原因和前次状态保留，并用 retry fixture 验证不会覆盖前次 attempt
- [x] 2.3 对 dispatch 后 outcome 未知的工具实现 recovery-visible 状态并禁止自动重执行，用 crash fixture 验证工具调用次数不增加

## 3. 生命周期 Gate

- [x] 3.1 验证 child terminal 不封存 parent、parent 等待/失败可单独读取，并运行 C02 child/projection contract suite
- [x] 3.2 验证 diagnostic flush failure 不遮蔽原始 cancellation/error，且 canonical terminal failure 可观察
- [x] 3.3 运行离线回归与 strict OpenSpec validation，确认没有引入 continuation、跨机器 authority 或自动副作用重试
