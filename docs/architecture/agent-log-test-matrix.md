# Canonical Runtime Event 测试矩阵

该矩阵覆盖 Canonical-only 运行时。fixture 位于 `src/mini_claude/tests/runtime_fixtures.py`，测试不依赖真实 API key、网络或用户目录。

| 边界 | 主要场景 | 当前入口 |
|---|---|---|
| envelope/schema | 必填字段、严格 opening、非法载荷、schema version | `test_runtime_event_domain.py`、`test_canonical_event_fixtures.py` |
| ordering/store | event_seq、重复 append、冲突、重开、sequence gap | `test_runtime_store.py` |
| provider lifecycle | Anthropic/OpenAI 语义等价、usage、error、重试 | `test_runtime_lifecycle.py`、`test_canonical_event_fixtures.py` |
| tool boundary | permission、durable dispatch、operation journal、unknown outcome | `test_runtime_lifecycle.py`、`test_recovery_resume.py` |
| child/terminal | parent lineage、attempt、cancel、唯一终态 | `test_run_lifecycle.py`、`test_remediation_integration.py` |
| projections | Session/Model/Metrics/Trace、partial/hidden、digest | `test_projections.py`、`test_canonical_acceptance.py` |
| recovery | open、uncertain、corrupt、future schema、保守 closure | `test_recovery_resume.py` |
| compaction/artifact | checkpoint high-water、archive ref、hash/size、故障 | `test_compaction_artifacts.py` |
| privacy | secret redaction、capture off/metadata-only/redacted、bounded payload | `test_compaction_artifacts.py`、`test_remediation_integration.py` |
| CLI boundary | isolated HOME one-shot/list/latest/resume、旧文件忽略、旧参数拒绝 | `test_cli_smoke.py`、`test_canonical_acceptance.py` |

## 运行方式

```powershell
D:\Anaconda\envs\py313\python.exe -m pytest -q -p no:cacheprovider
openspec validate --changes --strict --no-interactive
git diff --check
```

测试结果以测试运行器实际收集数量为准，不固定历史通过数。旧 logs/traces/session JSON/tool-results 文件仅用于负向共存测试，不会被应用读取。

## 证据边界

本矩阵证明本地 Python/SQLite、确定性 fixture 和 fake-provider 语义；不证明真实 Provider 网络、多进程/多实例部署或生产长期 SLO。具体删除面与旧数据边界见 [Canonical-only Acceptance Report](agent-log-canonical-acceptance-report.md)。
