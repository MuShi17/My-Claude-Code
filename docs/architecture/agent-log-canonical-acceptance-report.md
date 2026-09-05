# Agent Log Canonical Runtime Event Acceptance Report

状态：R-01～R-11 技术整改与 G0～G9 受控验收完成；Canonical route 已在隔离 HOME 切换并完成 post-cutover smoke 与 rollback。

> 本报告已覆盖并修订此前“无未闭合 blocker”的历史表述。当前不把测试全绿等同于 authority 已切换；默认读取路径仍为 `shadow/legacy`。

## 验收依据

- Maka reference commit：`1e543a7385614adc671623efe2586cf5317582d4`。
- 稳定比较器：`stable-semantic-v1`。
- 默认路由：`shadow`；canonical route 受 `AuthorityGate` blocker gate 保护。
- 数据策略：runtime.sqlite、legacy JSONL/session/traces/llm 和 artifact archive 保留；不执行 canonical rewrite、自动清理、commit、push、merge 或 release。

## 场景结果

| 类别 | 结果 |
|---|---|
| provider/session/model/trace parity | stable semantic parity 通过；provider/time/ID 差异单独标记 |
| durable tool boundary | dispatch 未提交时工具不执行；outcome/ref 缺失进入 blocker/uncertain |
| artifact/capture | 原子写入、hash/size 校验、dedup、三档隐私策略通过 |
| recovery/resume | terminal/open/partial-only/uncertain/corrupt 分类、幂等 closure、v2 snapshot 通过 |
| authority/rollback | canonical route 受显式批准保护；切换后 smoke 与 legacy rollback 可逆 |
| CLI | 临时 HOME 下 Anthropic/OpenAI fake provider 的 one-shot/list/latest/resume、shadow、canonical、rollback smoke 通过 |

## 运行命令

```text
D:\Anaconda\envs\py313\python.exe -m pytest -q -p no:cacheprovider
D:\Anaconda\envs\py313\python.exe -m pytest -q -p no:cacheprovider --runxfail
openspec validate --changes --strict --no-interactive
git diff --check
git status --short
```

最终命令结果：全量 `139 passed, 4 warnings`；`--runxfail` `139 passed, 4 warnings`；OpenSpec `22 passed, 0 failed`；`git diff --check` exit 0。新增 G9 定向 smoke 通过：canonical approved route、resume 新 Run、legacy 文件保留、list/latest/resume 与 rollback 均通过。

## Gap / decision

R-01～R-10 的实现级 P0/P1 blocker 已关闭，R-11 已完成状态回写；独立 reviewer 已完成本轮修复后的只读 Gap Closure，确认 G0-G8 通过、I-01～I-14 无已知实现阻断。用户已显式批准 G9，隔离 HOME 的 canonical route 与 post-cutover smoke 已通过，rollback 已验证。

- 当前默认入口仍为 `shadow/legacy`；Canonical route 需要显式传入 `--log-authority canonical --approve-canonical`。
- 未验证范围仍为真实 Provider 网络、跨进程/分布式写入、多实例部署和生产长期运行指标。

未验证范围：真实 Provider 网络、跨进程/分布式写入、多实例部署和生产长期运行指标。canonical authority 没有在默认配置中自动打开；失败时继续使用 rollback 路由，legacy 与 canonical 数据均保持可读。
