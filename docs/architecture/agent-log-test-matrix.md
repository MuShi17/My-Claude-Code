# Canonical Runtime Event 测试矩阵

该矩阵是 C02 的离线测试入口登记。核心 fixture 位于 `src/mini_claude/tests/runtime_fixtures.py`，golden 位于同目录 `golden/canonical_runtime_event.json`；所有条目不得依赖真实 API key、网络或用户的 `~/.mini-claude` 数据。

整改状态（2026-09-05）：R-01 至 R-10 的技术整改已完成，R-11 已完成工件交叉回写；G0-G9 受控验收已通过。Canonical route 已在隔离 HOME 执行，默认入口仍保持 `shadow/legacy`，需显式批准参数启用。

| 边界 | 主要场景 | 责任 change | 当前入口 |
| --- | --- | --- | --- |
| envelope/schema | 必填字段、非法载荷、schema version | C01/C04 | `test_canonical_event_fixtures.py` + C04 contract |
| ordering/store | 相同时间戳、重复 append、conflict、重开 | C05 | C02 temporary-store contract |
| provider lifecycle | Anthropic/OpenAI 等价 stream、usage、error | C06 | `FakeProviderScript` |
| tool boundary | permission、dispatch-before-execute、outcome pairing | C06 | `scenario_events` + fault points |
| child/terminal | parent/child identity、retry、cancel、唯一终态 | C07 | C07 lifecycle contract |
| projections | session/model replay/trace、partial/hidden、digest | C08 | golden + stable comparator |
| recovery | open、partial-only、uncertain、corrupt、legacy-only | C10 | temporary HOME/SQLite contract |
| compaction/artifact | checkpoint high-water、archive ref、hash/size/MIME | C09 | golden compaction + archive fault |
| privacy | secret redaction、capture mode、bounded payload | C04/C09 | `assert_no_secrets` + redaction contract |
| CLI/shadow | one-shot、list/latest/resume、legacy shadow、rollback、corruption exit | C10/C11 | isolated HOME fake-provider CLI smoke |

## 运行方式

从仓库根目录执行（使用 `py313` 环境）：

```bash
D:\Anaconda\envs\py313\python.exe -m pytest -q -p no:cacheprovider
D:\Anaconda\envs\py313\python.exe -m pytest -q -p no:cacheprovider --runxfail
```

纯 fixture 子集可执行：

```bash
D:\Anaconda\envs\py313\python.exe -m pytest src/mini_claude/tests/test_canonical_event_fixtures.py -q -p no:cacheprovider
```

C04/C05/C08/C10 的原 pending contract probe 已随能力落地转换为正常测试；不得重新使用 `importorskip`、无条件 skip 或 stale `xfail` 掩盖缺失实现。测试结果以运行时实际收集的数量为准，不绑定历史的 `109 passed` 文案。

当前结果：全量测试 `139 passed, 4 warnings`；`--runxfail` `139 passed, 4 warnings`；OpenSpec 严格校验 `22 passed, 0 failed`；`git diff --check` exit 0。G9 定向 smoke 覆盖 canonical approved route、resume 新 Run、旧 legacy 文件保留、list/latest/resume 和 rollback。真实生产 provider、多进程/多实例并发及生产指标仍不在本地 smoke 的证明范围内。
