## Context

C06 将工具 dispatch 和 provider 生命周期纳入 C05 store，但现有 Agent/子 Agent 主要由 `agent.py` 临时创建 logger 和任务，缺乏统一 run state、parent identity、attempt 和终态 guard。批次明确不支持 Maka continuation，也不允许自动重试不确定副作用。

## Goals / Non-Goals

**Goals:**

- 建立主/子 run/attempt 的生命周期状态机，并为所有退出路径调用同一 terminal finalizer。
- 使 terminal seal、重复 terminal、late callback 和 cancellation 行为可测试、可恢复。
- 保持已有用户触发方式和权限/预算语义，增加 durable 证据。

**Non-Goals:**

- 不支持恢复后继续原 Run、跨机器 continuation、workspace authority 或自动副作用 reconcile。
- 不将 retry 设计为隐藏的重新执行；retry 必须由上层显式触发并保留历史 attempt。

## Decisions

1. **RunStateGuard 统一跃迁。** 所有 provider/tool/child/CLI exit 都经过同一 guard + terminal finalizer；避免每个异常分支自行写状态。
2. **Child run 独立封存。** parent_run_id 只建立查询关系，不共享 sealed flag 或 event ordinal；这样 child 失败不会伪造 parent completed。
3. **Attempt 是运行内的可观察重试单元。** provider retry 可创建新 attempt；工具 dispatch uncertainty 只产生 recovery evidence，不自动 replay。
4. **Cancellation 使用 finally + idempotent seal。** 捕获 `CancelledError`、KeyboardInterrupt、provider abort 和 budget signal 后，尽最大努力先停止新工作，再写 terminal；重复 finalization 由 store 幂等。
5. **Late callbacks 隔离。** guard 在 terminal 后拒绝普通事件，必要的回调只进入 diagnostic sink，不能污染模型消息或投影。

## Risks / Trade-offs

- [异常路径复杂导致终态遗漏] → 给每个入口建立 state machine contract 和 fault matrix，并用 finally 统一收口。
- [父子并行结束顺序不确定] → 用独立 identities/ordinals 和 C08 关系投影，禁止按文件顺序推断。
- [取消时工具已在系统调用中] → 只停止新副作用，记录 outcome unknown；不声称已取消成功或自动重试。
- [retry 产生重复模型请求] → attempt/request identity、usage 和原因持久化，C11 parity 显示每一次 attempt。

## Migration Plan

先让 C06 的 main run 接入 guard，再接入子 Agent 和 cancellation/budget hooks；旧 session 文件继续按 C10 只读 fallback。C08/C10 消费新的 terminal/parent/attempt events，C11 验证所有退出场景和回滚。
