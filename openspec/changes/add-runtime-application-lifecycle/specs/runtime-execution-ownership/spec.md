## Purpose

规定 workspace owner、受管理执行句柄、取消传播和关闭顺序，确保 UI 层的取消请求与真实 OS 执行状态之间有可验证的边界。

## ADDED Requirements

### Requirement: workspace owner 锁必须跨入口一致

系统 MUST 根据规范化 workspace 身份建立 OS 级或等价的受保护 owner 锁；TUI 与 Application API MUST 使用同一个锁键和控制边界。锁 MUST 不依赖 UI 按钮、进程内布尔值或 PID 文件的偶然存在来保证互斥。

#### Scenario: 两个入口争用同一锁

- **WHEN** TUI 与受控 Application 进程同时尝试取得同一 workspace 的 root owner
- **THEN** 锁仲裁只授予一个 owner，另一个得到明确冲突结果，且退出或失败时不会释放不属于自己的 owner

#### Scenario: 锁键隔离不同 workspace

- **WHEN** 两个规范化 workspace 同时取得 owner
- **THEN** 每个 workspace 的锁状态独立，释放一个 owner 不会影响另一个 workspace 的 active run

### Requirement: child execution 必须继承 owner 树

每个 child Agent、受管理 shell 和派生执行句柄 MUST 记录 parent/root owner 身份，并只能由拥有该 owner 树的控制边界创建、取消和收回。Child 不得通过独立 root 注册绕过 workspace 互斥。

#### Scenario: child 在父 owner 树内启动

- **WHEN** active root run 创建一个 child Agent 或受管理 shell
- **THEN** child 继承 root/session/run/owner 身份并计入同一 owner 树，root 释放前不得被视为独立 workspace run

#### Scenario: 外部调用方不能收回他人 child

- **WHEN** 不属于 owner 树的调用方提交 child cancel 或 shutdown 请求
- **THEN** 请求被拒绝且 child 的执行和 owner 记录不被修改

### Requirement: 受管理 shell 的取消必须反映真实 OS 状态

系统 MUST 为受管理 shell 保存可控异步执行句柄和 owner 身份，持续 drain stdout/stderr，并区分优雅停止、强制终止、已退出和仍在运行。取消 asyncio task 或模型调用本身 MUST NOT 被当作 OS 子进程已经死亡。

#### Scenario: shell 取消后确认进程退出

- **WHEN** run 取消一个持有输出流的受管理 shell
- **THEN** 系统先执行有界优雅停止并 drain 输出，必要时执行明确的强制终止，最终状态包含可验证的 OS 退出结果

#### Scenario: 子进程仍存活时报告未完成

- **WHEN** shell 在取消超时后仍未退出
- **THEN** 控制面返回未完成/仍运行信息并保留 owner 记录，不伪造 cancelled-success 或释放仍被占用的锁

### Requirement: 取消必须传播到所有受管理执行层

公开 `run.cancel` MUST 向模型流、C02 交互等待、受管理 shell 和 owner 树内 child Agent 传播，并在每一层记录已请求、已确认或未完成的状态。取消传播 MUST 至多触发一次有效 dispatch/cancel 操作。

#### Scenario: 交互等待期间取消

- **WHEN** run 处于 waiting_interaction 且调用方提交合法 `run.cancel`
- **THEN** InteractionRequest 被转为 cancelled，等待解除，后到的回复不再触发工具执行

#### Scenario: child 与 shell 同时取消

- **WHEN** root run 同时管理模型流、child Agent 和 shell，并收到一次 cancel
- **THEN** 所有受管理层收到同一取消身份，重复传播不会产生第二次 dispatch，最终结果逐层反映确认或未完成状态

### Requirement: shutdown 必须按顺序收敛并报告超时

`shutdown` MUST 停止新的 run.start，取消并等待活跃执行，flush partial 与终态记录，关闭 MCP/store 等资源，最后释放 owner 锁。任一阶段超时 MUST 返回未完成信息并保留可恢复的状态，不得以静默强杀或伪成功替代证据。

#### Scenario: 无活跃执行时正常关闭

- **WHEN** Application 没有 active run 且调用 `shutdown`
- **THEN** 新 start 被关闭，控制记录完成 flush，MCP/store 按顺序关闭，owner 锁最终释放并返回成功关闭结果

#### Scenario: 活跃执行关闭超时

- **WHEN** shutdown 等待一个不能在时限内确认退出的 shell 或 child
- **THEN** API 返回未完成项及当前 owner/OS 状态，下一次恢复可以读取该状态，不能报告完整关闭
