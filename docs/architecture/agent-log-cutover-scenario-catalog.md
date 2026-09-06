# Agent Log Canonical-only 场景目录

该目录记录移除旧日志路线后的验收场景。所有场景都以 Canonical RuntimeEvent 和 SQLite store 为事实源。

| 场景 | 覆盖边界 | 证据 |
|---|---|---|
| 双 provider 等价 turn | Anthropic/OpenAI、model final、usage、tool pairing | `test_canonical_event_fixtures.py`、`test_projections.py` |
| 事件契约与顺序 | strict envelope、opening、event_seq、duplicate/conflict、terminal | `test_runtime_event_domain.py`、`test_runtime_store.py` |
| 工具 durable boundary | permission、dispatch-before-execute、operation idempotency、uncertain | `test_runtime_lifecycle.py`、`test_recovery_resume.py` |
| child/retry/terminal/cancellation | 父子 run、attempt、terminal seal、取消 | `test_run_lifecycle.py`、`test_remediation_integration.py` |
| projection rebuild | Session、Model、Metrics、Trace、Compaction、Recovery | `test_projections.py`、`test_recovery_resume.py` |
| artifact/capture/privacy | 原子 archive、dedup、hash、secret redaction、三档 capture | `test_compaction_artifacts.py`、`test_remediation_integration.py` |
| corruption/fail-closed | future schema、损坏事件、identity mismatch、sequence gap、append/finalize failure | `test_recovery_resume.py`、`test_runtime_store.py` |
| isolated CLI | one-shot/list/latest/resume、旧文件忽略、旧参数拒绝、无旧目录创建 | `test_cli_smoke.py`、`test_canonical_acceptance.py` |

## 运行约束

- Canonical authority 无运行时切换开关，CLI 不接受 authority、rollback 或 approval 选项。
- 旧文件不删除、不迁移、不改写；应用不把它们作为 session、recovery 或 tool-result 输入。
- dispatch 没有 durable outcome 时只进入人工复核状态，不自动执行或重试副作用。
- 本地 fake-provider 证据不等同于真实网络、跨进程或生产证据。
