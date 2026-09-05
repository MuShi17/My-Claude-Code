# Agent Log Canonical Runtime Event 切换场景目录

本目录是 C11 的单一验收入口，场景输入来自 C02 固定 fixture；比较只使用 C01/C08 定义的稳定语义，时间、随机 ID 和 provider metadata 作为允许差异单独报告。

| 场景 | 覆盖边界 | 证据 |
|---|---|---|
| 双 provider 等价 turn | Anthropic/OpenAI、model final、usage、tool call/result pairing | `test_cutover_gate.py::test_dual_provider_semantic_parity_and_allowed_metadata_report` |
| 缺失 event/ref/pairing | blocker 不被 comparator 掩盖 | `test_cutover_gate.py::test_missing_event_pairing_or_ref_is_a_blocker_and_gap_is_traceable` |
| projection parity | session/model/trace stable projection | `test_cutover_gate.py::test_session_model_trace_artifact_and_recovery_evidence_is_stable` |
| 权限拒绝与 dispatch barrier | 无权限副作用、canonical dispatch fail-closed | `test_runtime_lifecycle.py`、`test_cutover_gate.py::test_representative_fault_matrix_is_explicit_and_fail_closed` |
| child/retry/terminal/cancellation | 父子 run、attempt、terminal seal、async cancellation | `test_run_lifecycle.py`、`test_runtime_lifecycle.py` |
| partial/budget/error | partial bounded、unknown usage、budget/provider error terminal | `test_runtime_lifecycle.py`、`test_run_lifecycle.py` |
| artifact/capture/privacy | 原子 archive、dedup、hash、secret redaction、off/metadata/redacted | `test_compaction_artifacts.py` |
| recovery/resume | open/partial-only/uncertain/corrupt、幂等 closure、session v2、legacy readonly | `test_recovery_resume.py`、`test_cli_smoke.py` |
| diagnostic sink failure | legacy shadow 失败不改变 canonical 结果 | `test_cutover_gate.py::test_legacy_sink_failure_is_diagnostic_but_canonical_remains_authoritative` |
| authority blocker/rollback | canonical gate、shadow 默认路由、legacy rollback | `test_cutover_gate.py::test_authority_gate_blocks_canonical_with_unclosed_blocker_and_rolls_back_safely` |
| 隔离 CLI | 临时 HOME、无 API key、无网络、无用户数据访问 | `test_cutover_gate.py::test_cli_resume_in_isolated_home_is_offline_and_does_not_touch_user_data` |

## 路由约束

- `legacy`：只走旧诊断/兼容路径。
- `shadow`：canonical first，同时将可表达内容投影到 legacy；默认测试/运行路由。
- `canonical`：必须提供 `AuthorityGate` 证据和显式批准；CLI 使用 `--log-authority canonical --approve-canonical`。
- `--log-rollback`：只将路由退回 legacy，不删除或改写 runtime.sqlite、JSONL、traces、llm、session 或 artifacts。
