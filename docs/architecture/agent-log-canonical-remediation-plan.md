# Agent Log Canonical Runtime Event：问题清单、修复计划与验收 Gate

> 状态：整改与 G0-G9 受控验收完成
>
> 当前批次结论：隔离 HOME 的 Canonical authority 切换、切换后 smoke 与 rollback 已通过；默认入口仍由显式 flag 控制
>
> 依据：两份独立只读验收报告的综合结果
>
> 编制日期：2026-09-05

## 1. 决策摘要

R-01 至 R-10 的实现、集成测试和 R-11 工件回写已经完成，G0-G8 验收通过。测试全绿仍只证明当前代码与离线/fake-provider 场景满足既定契约，不能替代真实生产 provider、多进程/多实例并发和生产指标验证，也不能自动授权切换 Canonical authority。

在下列条件全部满足前，运行时必须继续保持 `shadow/legacy` 读取路径：

1. 所有运行时事件都从真实 Agent Loop 发出并可被 canonical store 接收。
2. Session、Model Replay、Trace、Compaction、Recovery 均使用 canonical event，而不是仅存在独立 helper。
3. `off` 隐私策略、终态封存、损坏恢复、child authority 传递和 retry identity 通过真实集成测试。
4. 11 个 change 的 OPSX 工件、批次源文档和验收报告状态一致。
5. 所有验收 Gate 通过，并取得用户对 Canonical authority 的显式批准。

本文件同时作为整改后的收口记录，不会自动切换 authority，也不替代 G9 所需的用户显式批准。

## 2. 验收基线

### 2.1 已确认通过的项目

| 项目 | 结果 | 说明 |
| --- | --- | --- |
| 完整离线测试 | `139 passed, 4 warnings` | 使用 `D:\Anaconda\envs\py313\python.exe` 执行；`--runxfail` 同样为 `139 passed, 4 warnings` |
| 定向 remediation/integration/CLI 测试 | 相关定向批次均通过 | 覆盖 fake-provider Anthropic/OpenAI、privacy、replay、compaction、recovery、finalize、child policy 与 CLI smoke；不等价于真实生产 provider 集成通过 |
| OpenSpec 严格校验 | `22 passed, 0 failed` | 原 11 个 change 与 11 个整改 change 均有 proposal/design/tasks/spec |
| `git diff --check` | exit 0 | 只有 LF/CRLF 警告，无 whitespace error |
| Maka 参考边界 | 通过 | 未引入完整 Agent Graph、workspace authority、分布式复制或自动重试等非目标能力 |
| 非破坏性 | 通过 | 两次验收均未提交、推送、合并、删除或清理用户已有改动 |

### 2.2 必须区分的两个状态

- 仓库内原 11 个 OpenSpec change 与 11 个整改 change 均已形成完整工件，整改 change 的 tasks 已完成。
- 需求源目录 `D:\桌面\MuShi\MuShiKnowledgeBase\05_Areas\需求与任务编排\10-需求拆解清单\2026-09-05-Agent-Log-Canonical-Runtime-Event重构.items`：批次总览、任务卡和 Item 01-11 已完成整改回写；Item 11 已记录独立复核通过，当前仅保留 G9 显式批准。

这两组状态仍需分层理解：代码侧 change tasks、需求管理侧回写、本地技术 Gate 和独立只读复核已对齐；G9 已在隔离 HOME 完成可回滚切换验证，但真实生产环境验证仍不能由本地测试自动推导。

## 3. 综合问题清单

严重级别：`P0` 表示阻止 Canonical authority；`P1` 表示阻止最终验收，但不一定阻止继续在 shadow 下开发；`P2` 表示文档或测试质量问题，必须在收口前清理。

| 编号 | 级别 | 问题 | 证据与影响 |
| --- | --- | --- | --- |
| I-01 | P0 | `LLM capture=off` 仍会写入 raw request/response | `agent.py:612` 无条件调用 legacy logger，`logger.py:238` 写入完整内容；独立探针确认 synthetic secret 落盘，违反 C09 隐私要求 |
| I-02 | P0 | Model Replay projection 未接入下一轮 provider context | `agent.py:988` 只有 helper，真实循环仍直接读写 `_anthropic_messages`/`_openai_messages`，C08 的 canonical-first 只停留在模块级 |
| I-03 | P0 | Compaction checkpoint 未接入真实 compaction 路径 | `agent.py:1041` 仍直接调用 provider summarizer 并修改内存消息；`CompactionCheckpointBuilder` 没有真实 high-water/digest 产物 |
| I-04 | P0 | Canonical 恢复损坏时可能静默降级 legacy | `__main__.py:318` 将打开/恢复异常转为 `resume_store=None`，`__main__.py:333` 继续 legacy fallback；损坏数据可能被误判为无 canonical 数据 |
| I-05 | P0 | Canonical terminal finalize 失败被吞掉 | `agent.py:814` 捕获 finalize 异常后仅打印并继续返回；Store 无法封存时当前 Run 没有 controlled fail |
| I-06 | P0 | 父 Agent 的 authority/approval/rollback 未传递给 child Agent | child 构造位于 `agent.py:1298`、`agent.py:1461`，只传 store/sink/parent run 等参数，可能造成父子运行时策略分叉 |
| I-07 | P0 | budget terminal 存在 double-finish | budget 路径在 recorder 已 `finish()` 后再次调用 `budget_exceeded()`，实际出现 `model call has already finished`，无法保证 budget terminal evidence |
| I-08 | P0 | retry 事件没有稳定 `attempt_id` | `runtime_lifecycle.py:261-272` 只记录 attempt number；独立探针确认 `attempt_id_present=False`，无法可靠关联尝试级事件 |
| I-09 | P0 | 真实 Agent Loop 没有发出 canonical user event | `agent.py:1491`、`agent.py:1813` 只追加 provider message array；Session/Model Replay 无法从 canonical stream 完整重建用户输入 |
| I-10 | P1 | C11 one-shot/list/latest/resume 的真实 CLI 证据不足 | 现有 smoke 主要覆盖 help/resume，未证明临时 HOME 下真实 one-shot provider loop、shadow parity 和恢复链路 |
| I-11 | P1 | pending `xfail` 和硬编码测试基线未清理 | 能力已经存在但仍有“not implemented yet” `xfail`；`test_cutover_gate.py:197` 仍写死 `109 passed, 1 xfailed`，与当前 119 passed 不一致 |
| I-12 | P1 | canonical runtime.sqlite 路径不一致 | Item 05 要求 `~/.mini-claude/sessions/{session_id}/runtime.sqlite`，C05 与 `__main__.py:306` 使用 `~/.mini-claude/runtime.sqlite`，多 session 隔离和恢复对象不明确 |
| I-13 | P1 | 批次源文档没有真实 writeback | 00-批次总览、01-任务卡和 Item 01-11 仍为待确认/待开始，99 个验收项未勾选；无法证明需求侧验收已完成 |
| I-14 | P1 | 现有 acceptance report 与代码证据矛盾 | `docs/architecture/agent-log-canonical-acceptance-report.md` 声称无未闭合 blocker，但两份独立审计已确认 I-01 至 I-09 等阻塞项 |

以上是整改前的历史问题记录，保留原始证据位置以便审计追溯。当前状态：I-01 至 I-12 已由 R-01 至 R-10 修复并通过对应技术 Gate；I-13、I-14 已由 R-11 完成文档与测试矩阵回写；独立复核已确认闭环。当前仍未完成的是 G9 的用户显式批准，不再存在已知的本地实现 P0/P1 阻断。

| 问题范围 | 修复 change | 当前结果 | 验收证据 |
| --- | --- | --- | --- |
| I-01 隐私绕过 | R-01 | 已修复 | off/metadata/redacted/full 与真实 Agent fake-provider 落盘测试 |
| I-02/I-09 context 与 user event | R-04、R-05 | 已修复 | Anthropic/OpenAI canonical user event、projection 与 stale-array 测试 |
| I-03 compaction | R-06 | 已修复 | checkpoint high-water/digest、重开、immutable prefix 与故障测试 |
| I-04/I-12 recovery 与路径 | R-07 | 已修复 | session 隔离、corrupt/partial/legacy-only 分类与 CLI exit 测试 |
| I-05/I-07 terminal/budget | R-02、R-08 | 已修复 | 唯一 terminal、budget 幂等及 finalize failure controlled-fail 测试 |
| I-06 child policy | R-09 | 已修复 | authority/approval/rollback/capture/store/sink 继承测试 |
| I-08 retry identity | R-03 | 已修复 | attempt_id 贯穿 start/retry/finish/error/usage 测试 |
| I-10/I-11 CLI 与测试清理 | R-10 | 已修复 | fake-provider one-shot/list/latest/resume/shadow/rollback，stale xfail 清理 |
| I-13/I-14 工件一致性 | R-11 | 已修复 | 批次总览、任务卡、Item、矩阵、报告及计划交叉回写 |

## 4. 逐项修复计划与实施结果

修复项保持细粒度，每项均应形成一个独立 change 或一个可独立审查的 change slice。除 R-11 外，修复阶段不得切换 Canonical authority。

### R-01：关闭 LLM capture 隐私绕过

对应问题：I-01，主要覆盖 C09。

- 修改 `Agent._capture_legacy_llm` 与 legacy shadow sink 的路由，使 `LLMCapturePolicy(mode="off")` 在任何 sink、异常路径和 provider 分支下都不写 raw 内容。
- `metadata`/usage/latency 等非敏感字段可以保留；raw request/response 必须只在显式 capture 模式且经过统一 redaction 后归档。
- 增加真实 Agent 集成探针，而不是只测试 `LLMCaptureManager` 独立模块。

完成条件：`off`、`metadata`、`redacted`、`full` 四种策略均有测试；synthetic secret 不得出现在 legacy JSONL、canonical payload、artifact archive 和异常日志中。

### R-02：修复 budget terminal 的生命周期顺序

对应问题：I-07，主要覆盖 C07。

- 明确 model call 与 run terminal 的职责：budget 超限必须在 model recorder 仍可写入时记录，或由统一 RunStateGuard 负责生成唯一 terminal event。
- 禁止对已经 `finish()` 的 recorder 再调用 `budget_exceeded()`；重复 terminal、late event 和异常收尾必须幂等处理。
- 为 Anthropic、OpenAI、同步预算检查和异步取消分别增加 fault matrix 场景。

完成条件：budget 场景总有一个 `budget_exceeded` terminal，且不会抛出 `model call has already finished`；每个 run 至多一个 terminal seal。

### R-03：补齐 retry attempt identity

对应问题：I-08，主要覆盖 C07。

- 为每次 provider attempt 生成稳定的 `attempt_id`，并在 model start、retry、model finish/error、usage 中保持一致。
- `attempt` 数字只作为顺序字段，不能替代 identity。
- retry 事件应能关联 `request_id`、`run_id`、`attempt_id` 和 retry reason。

完成条件：retry fixture、provider error 重试、跨 provider 分支和 projection 查询均能按 `attempt_id` 精确关联，重复事件不产生第二次 attempt。

### R-04：从真实 Agent Loop 发出 canonical user event

对应问题：I-09，覆盖 C04、C06、C08、C10。

- 在 Anthropic/OpenAI 两条真实 provider loop 的 turn boundary 发射 user event。
- 记忆注入、plan mode、resume、compaction 后追加的用户可见上下文必须定义事件语义，区分原始用户输入与系统注入内容。
- Session、Model Replay 和 recovery fixture 必须能够从 canonical stream 重建用户输入顺序。

完成条件：真实 fake-provider one-shot 至少生成 user、model request/response、tool、terminal 等事件；两种 provider 的重建结果一致。

### R-05：将 Model Replay 接入下一轮上下文

对应问题：I-02，覆盖 C08。

- 把 `ModelReplayProjection` 放到 provider loop 的下一轮 context 生成边界，禁止由 `_anthropic_messages`/`_openai_messages` 继续作为 canonical authority。
- 明确 projection 的版本、遗漏事件、tool result 和 compaction checkpoint 行为。
- legacy message array 只作为兼容输出或 shadow comparator 输入。

完成条件：清空或扰动 legacy message array 后，仍可从 canonical store 生成下一轮等价 provider context；parity comparator 只忽略明确允许的 metadata 差异。

### R-06：将 Compaction checkpoint 接入真实路径

对应问题：I-03，覆盖 C09。

- 在真实 compaction 触发点写入 checkpoint，包含 immutable prefix high-water、source digest、coverage、tail 和 projection version。
- 大结果先通过 artifact archive 原子归档，再在消息/事件中保存 ref，禁止只修改内存消息而不留下 checkpoint。
- compaction 失败必须保留原始 canonical prefix，并产生可诊断的失败状态。

完成条件：真实 compaction 场景可重启、重建和验证 digest；追加后续事件不会改变已封存 prefix；artifact hash/ref 可校验。

### R-07：修复 Canonical recovery 的损坏处理与路径

对应问题：I-04、I-12，覆盖 C05、C10。

- 统一 runtime store 路径，优先采用 `~/.mini-claude/sessions/{session_id}/runtime.sqlite`，并同步更新 C05、CLI、session manager、测试和文档。
- canonical 数据损坏时保留数据库与诊断信息，明确返回 corruption 状态；不得把打开失败当作“无 canonical 数据”并静默进入 legacy。
- resume 只能在明确的受控策略下读取 legacy；fallback 必须可观测、可解释且不能覆盖 canonical 数据。

完成条件：missing、corrupt、partial-tail、schema mismatch、legacy-only 五类场景分类明确；corrupt 场景不会静默降级，resume 幂等且不丢失原始文件。

### R-08：让 terminal finalize 失败导致 controlled fail

对应问题：I-05，覆盖 C05、C06、C07、C11。

- `Canonical Store` 写入、seal、fsync 或 finalizer 失败时，当前 Run 必须进入可观测的 controlled failure 状态。
- 不得在没有 terminal evidence 的情况下把 run 报告为成功；legacy shadow 的失败也要区分“canonical failed”与“legacy failed”。
- 统一错误对象、exit status、CLI 输出和恢复策略。

完成条件：注入 store append/seal/fsync/finalizer 故障时，run 有唯一失败终态、诊断字段和保留数据；CLI 返回非成功结果且不吞异常。

### R-09：传递父子 Agent 的运行时 authority 配置

对应问题：I-06，覆盖 C07、C11。

- child Agent 构造必须继承或显式覆盖 `runtime_authority`、approval、rollback、capture policy、store/sink 和 parent run 关系。
- 继承规则写入 API contract，禁止 child 默认回到不同 authority 或隐式创建不一致的 store。
- 增加 parent/child、嵌套 child、取消、retry、rollback 的事件隔离和配置快照测试。

完成条件：父子事件能按 `parent_run_id` 查询，authority 和 rollback policy 可追溯；child 不会绕过父级 Canonical gate。

### R-10：补齐真实 CLI、shadow parity 与测试清理

对应问题：I-10、I-11，覆盖 C01、C02、C11。

- 在临时 HOME 下使用 fake provider 运行真实 `one-shot`、`list`、`latest`、`resume`、`--log-authority shadow` 和 rollback 场景。
- 将 pending `xfail` 转为正常测试或删除已过时的 probe；禁止用历史测试数量硬编码 gate。
- 让测试矩阵记录命令、环境、provider fixture、结果和允许差异。

完成条件：CLI 证据覆盖启动、写入、查询、恢复、失败和回滚；测试套件无与已实现能力矛盾的 stale `xfail`，gate 不依赖固定的 `109 passed` 文案。

### R-11：同步批次状态、OPSX 工件与验收报告

对应问题：I-13、I-14，并作为最终收口项。

- 先完成 R-01 至 R-10 的代码和测试，再回写 00-批次总览、01-任务卡和 Item 01-11 的 status、checkbox、evidence 与实际命令结果。
- 修订现有 acceptance report，明确历史基线、当前 blocker、已通过 gate 和未覆盖风险；不能保留“无未闭合 blocker”的过期表述。
- 11 个 OpenSpec change 在归档前必须与实际实现和批次状态逐项对齐。

完成条件：需求源、OpenSpec、代码、测试矩阵和验收报告没有相互矛盾的状态；每个完成项有文件/命令/测试证据；未完成项明确标为 blocker 或风险。

## 5. 验收 Gate

Gate 必须按依赖顺序推进。任何 P0 Gate 失败，都不得进入 Canonical authority 切换。

当前 Gate 状态：`G0-G9 = PASS（G9 为隔离 HOME 受控切换）`。当前默认运行时仍保持 `shadow/legacy` 读取；Canonical route 通过显式批准参数启用。

| Gate | 入口条件 | 必须验证 | 通过标准 |
| --- | --- | --- | --- |
| G0 基线完整性 | 开始任何修复前 | 记录 HEAD、dirty worktree、11 个 change、批次实际路径和 Python 环境 | 验收只读；测试不会覆盖已有改动；命令与证据可复现 |
| G1 隐私封锁 | R-01 完成 | `off`/metadata/redacted/full；legacy、canonical、artifact、异常日志扫描 synthetic secrets | `off` 不保存 raw；任何落盘 raw 都必须有显式策略与 redaction 证据 |
| G2 事件完整性 | R-02、R-03、R-04 完成 | user/model/tool/retry/budget/error/cancel/terminal 事件及 ID 关联 | 真实 Agent Loop 每个 run 都有完整事件链；每个 attempt 有稳定 `attempt_id`；terminal 唯一 |
| G3 Canonical-first context | R-05 完成 | 清空 legacy arrays 后生成下一轮 Anthropic/OpenAI context；比较 tool、user、model 顺序 | provider context 可由 canonical projection 重建；允许差异有明确 comparator 规则 |
| G4 Compaction 与 artifact | R-06 完成 | 大 tool result、checkpoint、high-water、digest、追加后 immutable prefix、重启重建 | checkpoint 可验证、可重建；artifact 原子写入且通过 hash/ref 校验 |
| G5 Recovery 安全 | R-07 完成 | missing/corrupt/partial/schema mismatch/legacy-only；session path 隔离；resume 幂等 | corruption 不静默 fallback；原始数据库和诊断保留；路径与文档统一 |
| G6 Finalize 与 parent/child | R-08、R-09 完成 | append/seal/fsync/finalizer 故障；child authority/approval/rollback/capture 继承 | finalize 失败 controlled fail；父子 policy 与事件关系可追溯；无隐式 authority 漂移 |
| G7 真实 CLI 与 shadow parity | R-10 完成 | 临时 HOME 的 one-shot/list/latest/resume、shadow、rollback、fake provider | 全链路可运行；shadow comparator 只报告允许差异；无 stale xfail 或硬编码旧基线 |
| G8 批次收口 | R-11 完成 | 11 个 OPSX change、批次总览、任务卡、Item、测试矩阵、验收报告交叉核对 | 状态、证据、命令和 blocker 一致；OpenSpec strict validation 通过 |
| G9 Canonical authority 决策 | G0-G8 全部通过 | 用户已显式批准；隔离 HOME 已执行 canonical 切换、post-cutover smoke 和 rollback | 受控切换通过；默认入口仍保留显式 flag，生产 rollout 另需真实环境证据 |

## 6. 推荐执行顺序与停止规则

推荐顺序：

`R-01 → R-02/R-03/R-04 → R-05/R-06 → R-07/R-08/R-09 → R-10 → R-11 → G9`

其中 R-02、R-03、R-04 可以在同一阶段并行开发，但必须分别通过 G2 的独立场景。R-05 和 R-06 可并行实现，但都必须依赖稳定的 canonical user/model/tool event。

遇到以下任一情况，立即停止切换推进并回到 shadow：

- 任意 `off` 策略探针发现 raw request/response 或敏感 marker。
- 任意 run 没有唯一 terminal evidence，或出现第二个 terminal seal。
- canonical store、checkpoint、artifact 或 finalizer 失败后仍返回成功。
- corruption 被静默当成 missing，或恢复过程覆盖原始 canonical 数据。
- child Agent 的 authority、approval、rollback 或 capture policy 与父 Agent 不一致且无显式记录。
- legacy projection 与 canonical projection 的差异无法分类为允许差异。
- 批次源文档、OpenSpec tasks、测试结果和验收报告出现互相矛盾的完成状态。

## 7. 最终收口命令集

在 G8 之前和 G8 之后各执行一次，并保存完整输出：

```powershell
$py = "D:\Anaconda\envs\py313\python.exe"

& $py -m pytest -q -p no:cacheprovider
& $py -m pytest -q -p no:cacheprovider --runxfail
openspec validate --changes --strict --no-interactive
git diff --check
```

另外必须执行并记录：

- 临时 HOME 下的真实 one-shot/list/latest/resume CLI smoke；
- fake provider 的 Anthropic/OpenAI 双分支；
- store append/seal/fsync、budget、retry、cancel、provider error、permission denial、corruption、partial tail、compaction 和 artifact fault matrix；
- secret marker 扫描和 `LLMCapturePolicy(mode="off")` 的端到端落盘检查；
- parent/child authority 与 rollback inheritance 检查；
- shadow parity 报告及允许差异清单。

本次已记录的结果：全量测试 `139 passed, 4 warnings`；`--runxfail` `139 passed, 4 warnings`；OpenSpec 严格校验 `22 passed, 0 failed`；`git diff --check` exit 0。G9 定向 smoke `test_cli_canonical_cutover_and_post_cutover_smoke_preserves_legacy_and_rollback` 通过。测试后保留用户原有 dirty worktree，未执行 commit、push、merge、release 或删除操作。

G9 的用户显式批准、隔离切换、post-cutover smoke 和 rollback 已完成；未验证的真实 Provider、多进程/多实例和生产长期运行风险不得被本地 smoke 误认为已覆盖。

## 8. 当前结论

本批次保持：

```text
authority = shadow/legacy
canonical_cutover = passed_in_isolated_home
acceptance = G0-G9 PASS (scoped)
next_action = optional production rollout after live-environment review
```

两份独立报告发现的问题已逐项纳入 R-01 至 R-11 并完成本地验证；G9 已在隔离 HOME 完成受控切换、post-cutover smoke 和 rollback，相关报告、测试矩阵与批次源文档已同步当前状态。默认入口仍使用 `shadow/legacy`，只有显式批准参数才启用 Canonical route。
