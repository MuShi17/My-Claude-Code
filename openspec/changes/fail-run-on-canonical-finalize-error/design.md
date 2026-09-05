## Decisions

1. canonical authority 下 finalize 失败必须改变 run exit status，并向调用方报告失败。
2. shadow 模式也必须记录 canonical failure diagnostics，但 legacy 结果不能伪装成 canonical 成功。
3. 原始 provider/tool/cancellation 异常优先保留；finalize error 作为 cause/diagnostic 关联。
4. 所有 failure path 仍执行 best-effort flush，但 flush error 不能被吞成成功。
