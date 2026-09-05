## Decisions

1. model response 的 `finish()` 只结束 model call，不代表整个 run 已终态。
2. budget exceeded 由 run-level guard 生成唯一 terminal event；若当前 recorder 已结束，不再调用 recorder 的 model-call terminal API。
3. terminal emission 使用稳定 idempotency，重复 budget/late event 不得产生第二个 terminal。
4. 原始 provider/tool 异常优先保留，terminal failure 单独进入 controlled failure 诊断。
