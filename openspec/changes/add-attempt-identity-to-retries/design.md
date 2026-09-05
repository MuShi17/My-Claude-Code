## Decisions

1. `attempt_id` 在每次实际 provider attempt 开始时生成，`attempt` 仅表示顺序。
2. retry event 必须携带 request/run/attempt identity 和 reason。
3. 同一 attempt 的 start/finish/error/capture 不重复生成 identity。
4. ID 只用于事件关联，不引入 wire replay 或自动副作用重试语义。
