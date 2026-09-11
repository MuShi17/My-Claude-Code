## 1. 前置契约与验证边界

- [ ] 1.1 读取并记录 C02 handoff（`tasks §6.2`）中 `OutputPort`、`InteractionPort`、`InteractionRegistry`、`RuntimeEventEmitter`、`DurableToolBoundary` 和 `SQLiteRuntimeStore` 的可复用接口，确认本 Change 不重写 canonical 事件事实源
- [ ] 1.2 冻结 Application command envelope 的字段、scope 类型、参数摘要白名单、命令冲突错误和 queued/running/waiting_interaction/cancelling/终态转移表，并为每项对应到四份 C03 spec
- [ ] 1.3 通过临时目录实验确定 workspace 控制记录的文件/表布局、现有 session store 的兼容读取路径和 schema 迁移编号，形成不覆盖旧事实的迁移记录
- [ ] 1.4 建立 mock、离线 provider/worker、真实本地 Python 子进程、CLI/TUI consumer 和 Harbor 只读契约核对的证据分类；未完成 D(C03) 前不得修改产品代码

## 2. Application 控制面

- [ ] 2.1 新增 `src/mini_claude/application.py` 的结构化请求/响应、错误和生命周期类型，要求严格调用方显式提供 `ProjectContext`，并拒绝通过当前目录补推 workspace
- [ ] 2.2 实现 `session.create`/`session.list` 的 workspace 绑定和 canonical session 只读适配，验证返回稳定 session 身份且列表不跨 workspace 泄露
- [ ] 2.3 实现 `run.start` 的命令接受、root owner 申请、run 记录提交和 Agent supervisor dispatch 顺序，验证 commit 成功前不调用模型/工具
- [ ] 2.4 实现 `run.status` 结构化查询和集中 lifecycle guard，验证每个 run 只有一个终态且未确认的取消/关闭不被报告为成功
- [ ] 2.5 实现 `interaction.respond` 的 session/run/tool/request 身份校验和 C02 `InteractionRegistry` 委托，验证过期、错绑和重复回复不会触发工具
- [ ] 2.6 实现所有副作用命令的 command identity、scope、params digest、原结果回读和 digest 冲突拒绝，验证重复 `run.start`/`run.cancel` 最多一次有效 dispatch
- [ ] 2.7 实现 `shutdown` 的关闭门、活跃执行查询、阶段结果和幂等响应，为后续 owner supervisor 提供“未完成则不释放”接口

## 3. Workspace owner 与执行监督

- [ ] 3.1 新增 `src/mini_claude/workspace_lock.py` 的统一锁工厂和平台后端，以规范化 `workspace_id` 为键，使用 OS 持有句柄而不是 PID 文件存在性
- [ ] 3.2 实现 root owner token、owner metadata 和 child owner tree 的创建/授权/释放校验，验证 foreign caller 不能取消或释放他人的 child
- [ ] 3.3 将模型 task、InteractionRegistry 等待、child Agent 和 managed shell 注册到同一 root supervisor，并为每个 execution 保存 parent/root/owner 身份
- [ ] 3.4 为 `tools.py` 的受管理 shell 接入异步 execution handle，绑定 `ProjectContext.tool_cwd` 和 owner，并持续 drain stdout/stderr
- [ ] 3.5 实现 shell 的有界优雅停止、必要的强制终止、return code/仍运行观察和超时证据，明确区分 asyncio task 取消与 OS 进程退出
- [ ] 3.6 实现一次性取消传播：模型、交互、child、shell 共用 cancel identity，重复传播不重复 dispatch，交互取消后迟到回复不能继续执行工具
- [ ] 3.7 按“禁止新 start → cancel/wait → flush partial/terminal → close MCP/store → release owner”实现 shutdown 顺序，并验证超时仍存活时保留 owner 和可恢复状态

## 4. 控制持久化与恢复

- [ ] 4.1 在已确定布局中增加版本化 session/run/command/owner/pending-interaction 控制记录及索引，保留稳定身份、摘要、状态和迁移诊断字段
- [ ] 4.2 实现 command accepted、run/interaction 状态和 dispatch intent 的原子提交边界；用 fault injection 验证提交前崩溃无隐藏 dispatch、提交后响应丢失重试不重复执行
- [ ] 4.3 实现参数摘要和敏感字段白名单，验证控制库、异常和诊断不保存 API key、完整 provider 配置或原始秘密载荷
- [ ] 4.4 实现旧 session 与 partial 数据的只读解析/显式迁移，保留未知字段和原文件；迁移失败时返回诊断且不删除、覆盖或静默重建旧数据
- [ ] 4.5 实现重启恢复分类：accepted 未 dispatch 或未完成 run 标为 `interrupted`，结果证据不足标为 `uncertain`，并禁止自动重放副作用工具
- [ ] 4.6 分离 Unknown tool 名称错误与 unknown/uncertain tool result 的记录和恢复路径，分别提供状态、错误码和测试断言

## 5. CLI/TUI Application 接线

- [ ] 5.1 修改 `__main__.py`/入口工厂，使 `ProjectContext`、runtime store、`TerminalOutputPort` 和 `TerminalInteractionPort` 注入 Application，删除入口对 Agent `_aborted`/`_output_buffer` 的控制依赖
- [ ] 5.2 将 one-shot prompt 转换为 `session.create`/`run.start`/`run.status` 观察链，验证输出和交互仍由 C02 ports 关联到同一 session/run
- [ ] 5.3 将 REPL 多轮、`resume`、历史 session 选择和既有 REPL 命令转换为 Application API，验证同一 ProjectContext/session 历史可读且不自动重放不确定工具
- [ ] 5.4 将首次 SIGINT 转为公开 `run.cancel`、后续退出转为既有 shutdown/退出码路径，保持 flags、permission mode、EOF、审批和拒绝语义
- [ ] 5.5 验证 TUI 与受控 Application 共同使用 workspace owner、命令身份和 status/cancel；同 workspace 第二入口只返回 owner-conflict/已有结果，不产生第二个 root dispatch
- [ ] 5.6 增加 Harbor consumer 的离线、只读契约核对说明和检查，不接入 stdio wire、Electron、Windows 安装包或付费 benchmark 作为 C03 验收

## 6. 分层测试与实现验收

- [ ] 6.1 新增 Application API focused tests，覆盖 session、run、interaction、status、shutdown、错误身份和终态唯一性
- [ ] 6.2 增加 command idempotency/并发测试，覆盖相同命令重试、摘要冲突、cancel 与 terminal race 以及 commit fault injection
- [ ] 6.3 增加真实多进程 workspace lock 测试，覆盖同 workspace 争用、不同 workspace 隔离、错误释放和存活 child 阻止提前释放
- [ ] 6.4 增加 owner tree/cancellation tests，覆盖交互等待、model/child/shell 同时取消、重复传播和迟到 reply
- [ ] 6.5 增加真实本地 Python subprocess tests，覆盖 stdout/stderr drain、优雅停止、强制终止、return code、超时仍存活和 lock 保留
- [ ] 6.6 增加控制记录 schema/migration/recovery tests，覆盖旧 session/partial、秘密负向断言、interrupted/uncertain 和未知工具两条路径
- [ ] 6.7 增加离线 CLI/TUI consumer tests，覆盖 one-shot、REPL、resume、flags、EOF、SIGINT、C02 事件和 owner-conflict；明确不把替身结果写成真实部署证据
- [ ] 6.8 运行 C03 focused tests、既有 C02 canonical/runtime regression、编译检查和 OpenSpec strict validate；分开记录本地替身、真实子进程和 consumer 证据，不以单一全绿结论覆盖缺口

## 7. Gate、写回与交付边界

- [ ] 7.1 在实现前完成独立 openspec-designer 对 proposal/design/specs/tasks 的对抗性审查，并由主 Agent 验证其调用链证据、范围和替代方案，不自动接受未经验证的建议
- [ ] 7.2 在实现前完成独立 test-strategy-agent 的测试策略和缺口审查；按结果补齐最小验证，不把角色结论当作主 Agent 最终接受
- [ ] 7.3 冻结实现 diff，复核工作树中的 AGENTS.md、`src/pyproject.toml` 等既有用户改动未被误纳入，并完成 D(C03) 的 scope、validation、acceptance 和风险记录
- [ ] 7.4 将实现结果、测试命令/结果、未解决风险、artifact/diff digest 和 implementation/delivery authorization 分别写回 C03 registry、I06、I09B 和批次总览
- [ ] 7.5 仅在用户另行授权后执行 C03 的 commit/push/MR/发布/部署；本 Change 的 OpenSpec 工件完成或测试通过不自动触发这些动作
