# Agent Log Canonical-only Acceptance Report

状态：Canonical-only 清理实现完成并已通过独立只读评审后的本地 Gate；验证以 `py313` fixture、SQLite 和隔离 HOME smoke 为证据。该报告不把本地 fake-provider 结果扩展为生产就绪结论。

## 当前边界

- RuntimeEvent v2 是唯一运行时事实格式；SQLite 是唯一运行时事实源。
- 每个 session 使用 `~/.mini-claude/sessions/{session_id}/runtime.sqlite`。
- Session、Model Replay、Run Trace、Metrics、Compaction 和 Recovery 都是可删除、可重建的 Canonical projection。
- 工具遵循 durable dispatch → side effect → outcome；dispatch 无 outcome 时只标记 uncertain，不自动重做副作用。
- LLM body capture 仅由 `off`、`metadata-only`、`redacted` 策略控制，不经日志写入路径。
- 历史 JSON/JSONL、旧根数据库和 tool-results 文件不删除，但应用不发现、不读取、不迁移。

## 关键 Gate

| Gate | 证据 | 目标结论 |
|---|---|---|
| 契约与顺序 | RuntimeEvent strict parser、opening、event_seq、幂等/冲突/terminal 测试 | 事件身份、顺序和终态可验证 |
| 工具 durable boundary | SQLite operation/journal、权限拒绝、dispatch 故障、unknown recovery 测试 | 副作用前必须有 durable dispatch |
| Projection 重建 | Session/Model/Metrics/Trace/Compaction/Recovery 测试 | 物化数据不是事实源 |
| Canonical-only | runtime 静态引用扫描、旧文件共存、隔离 HOME CLI smoke | 不创建、不读取旧日志路线 |
| 失败闭环 | append/finalize、future schema、corrupt event、identity/gap、artifact 故障测试 | 失败可观测且不静默降级 |
| 隐私 | capture 三档策略和 synthetic secret negative tests | `off` 不落 body，`redacted` 不泄露 marker |

## 可复现命令

```powershell
D:\Anaconda\envs\py313\python.exe -m pytest -q -p no:cacheprovider
openspec validate --changes --strict --no-interactive
git diff --check
```

测试数量以当前运行时收集结果为准，不在文档中固定历史数量。未覆盖范围包括真实 Provider 网络、跨进程/多实例竞争、部署环境和生产长期 SLO。

## 旧数据边界

删除兼容代码不会删除既有 `logs/`、`traces/`、旧 session JSON、旧根数据库或 `tool-results`。这些文件只保留供未来显式离线导出；当前应用不会把它们作为 Canonical session，也不会因 `--list`、`--latest` 或 `--resume` 改写它们。

实施方案与逐项 change 见 `D:\桌面\MuShi\MuShiKnowledgeBase\04_Projects\my-coding-agent\ISS001-canonical-only-runtime-cleanup-plan-2026-09-06.md`。

## 独立评审与 Gap Closure（2026-09-06）

独立只读 reviewer 初始结论为 `CONDITIONAL`。主代理逐项复核后确认并修复以下真实缺口：

- tool outcome 现在必须匹配已 dispatch operation 的 session/run/invocation/turn/provider call/tool identity；已完成 operation 拒绝第二个不同 event id 的 outcome。
- terminality 只由 typed terminal status 决定；`end_run` 无 terminal status、partial opening 均被拒绝。
- Model Replay 只接受 call 之后的 function response；逆序结果保留诊断但不会进入 provider context。
- Anthropic adapter 将 signed thinking 转为 provider-native `type/thinking/signature` block；OpenAI adapter 不发送不受支持的 neutral thinking block。
- `mini_claude_py.egg-info/SOURCES.txt` 已移除已删除 logger/tracer 并补齐当前 runtime/projection 文件清单。
- follow-up 独立只读复核确认上述缺口均已关闭；未签名或外部 provider 的 thinking 在 Anthropic adapter 中会被过滤，不再以 neutral `kind` 结构流入 API。

评审中关于旧第一批 `139 passed/22 passed`、shadow route 和 `--log-authority` 的意见，已核实为历史批次文档，不是当前 Canonical-only 批次的实现证据；当前批次文档已单独记录最新状态。未采纳超出本批次边界的生产 Provider、多实例部署和 SLO 要求。
