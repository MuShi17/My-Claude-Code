## 1. Recovery projection

- [x] 1.1 实现 terminal/open/partial-only/unmatched/uncertain/corrupt/legacy-only 分类、high-water/source digest 和 diagnostics，并用 C02 recovery fixtures 验证分类不依赖 legacy 补事实
- [x] 1.2 实现 startup 对可判定 open run 的幂等 terminal closure，并用重复启动、provider crash、cancel、budget 和 dispatch-unknown fixture 验证不产生第二终态或自动工具重试
- [x] 1.3 接入 artifact/checkpoint/ref integrity 状态，验证悬空 ref、hash mismatch 和 source digest mismatch 进入安全诊断路径

## 2. Session v2 与 restore

- [x] 2.1 实现 canonical-derived session v2（schema/projection version、high-water、digest、coverage），并用相同 prefix 重建和 stale snapshot fixture 验证
- [x] 2.2 将 `Agent.restore_session`、session list/latest 和 CLI `--resume` 改为 canonical-first，使用 C08 ModelReplay/SessionProjection 并验证关闭 legacy 文件仍可恢复 canonical session
- [x] 2.3 实现 legacy-only readonly fallback、来源标识和无 fabricated dispatch 约束，并用历史 session/log/traces/llm fixture 验证公开兼容

## 3. Non-destructive migration Gate

- [x] 3.1 为 v2 snapshot atomic write、schema mismatch、corrupt event、migration failure 和 authority rollback 增加 fault tests，验证 runtime.sqlite 与 legacy 数据均保留
- [x] 3.2 运行 C02 recovery/CLI suite、项目离线回归和 strict OpenSpec validation，确认 resume 不支持隐式 continuation 或副作用自动重放
