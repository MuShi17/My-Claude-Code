## Purpose

规定 session/run/command/pending-interaction 控制记录的版本、原子边界、迁移和重启恢复，保护 canonical 事实并避免不确定副作用被自动重放。

## ADDED Requirements

### Requirement: 控制记录必须版本化且不保存秘密

系统 MUST 以明确 schema 版本保存 session、run、command、owner 和 pending-interaction 控制记录；命令和交互记录 MUST 保存必要的稳定身份与参数摘要，但 MUST NOT 保存 API key、完整配置、未受限的内部状态或原始秘密载荷。

#### Scenario: 记录可按版本读取

- **WHEN** 打开一个已知版本的控制记录
- **THEN** 系统能按其版本解析 session/run/command 身份和状态，并为未知版本返回可诊断的不支持结果

#### Scenario: 敏感字段被排除

- **WHEN** command 或 pending-interaction 记录被写入并读回
- **THEN** 记录只包含受限参数摘要和必要元数据，不包含 API key、完整 Provider 配置或原始敏感输入

### Requirement: 接受命令与 run 开启必须原子落盘后再调度

对于可能产生执行副作用的 `run.start`、`run.cancel` 和 `interaction.respond`，系统 MUST 在同一受保护控制边界内完成命令幂等记录、必要的 run/interaction 状态变更和提交点；只有提交点成功后才允许 dispatch。提交前崩溃 MUST 不产生无法追踪的执行，重复提交 MUST 不产生第二次副作用。

#### Scenario: commit 前崩溃不启动 run

- **WHEN** 在命令记录或 run 开启提交前注入进程崩溃
- **THEN** 重启后不存在已执行但无控制记录的 run，原命令可按未接受或可安全重试状态处理，不发生隐藏 dispatch

#### Scenario: commit 后重试不重复执行

- **WHEN** 命令提交成功后调用方因响应丢失再次提交同一 command_id 与摘要
- **THEN** 系统返回原 run/终态记录，工具或模型 dispatch 不重复发生

### Requirement: 旧 session 与 partial 数据必须可迁移或安全失败

系统 MUST 支持现有旧 session 和 canonical partial 数据的只读兼容与显式 schema 迁移；迁移 MUST 保留可读事实和未知字段，失败时 MUST 保留旧数据并返回可诊断错误，不得通过删除数据库、覆盖旧记录或丢弃事实解决兼容问题。

#### Scenario: 旧 session 读取并迁移

- **WHEN** 启动后读取一个旧版本 session 并请求 `session.list` 或 resume
- **THEN** 系统按迁移规则返回可读 session，记录迁移版本和结果，历史 canonical/partial 内容不被改写为伪造新事实

#### Scenario: 迁移失败保留旧数据

- **WHEN** 旧记录缺少必需字段或迁移步骤失败
- **THEN** 系统返回明确迁移错误，旧文件/记录仍可读取，且不会通过删库或自动重建覆盖原数据

### Requirement: 重启恢复必须区分中断与不确定结果且禁止自动重放

重启时系统 MUST 将未执行的 accepted command、未完成 run 和未确认的工具结果按记录证据分别标为 interrupted 或 uncertain；未知/不确定工具结果 MUST 保留其不确定性。系统 MUST NOT 自动重新执行可能产生副作用的工具，也 MUST NOT 把未知工具名错误和未知工具结果混为一个重试语义。

#### Scenario: accepted 但未 dispatch 的命令恢复

- **WHEN** 进程在命令已接受但 dispatch 尚未确认时崩溃，随后重新启动
- **THEN** 恢复结果标识该命令为 interrupted 或可安全处理的未执行状态，不自动调用原工具

#### Scenario: 工具结果不确定时不重放

- **WHEN** 重启发现工具调用记录存在但结果提交不完整或未知
- **THEN** 系统保留 uncertain 结果并要求显式人工/上层决策，不自动再次执行该工具

#### Scenario: 未知工具名与未知结果分开

- **WHEN** 模型请求一个未知工具名，或恢复一个未知/不确定的已请求工具结果
- **THEN** 前者返回 Unknown tool 查询失败且无重试路径，后者进入 uncertain 恢复语义；两者的记录、状态和测试断言彼此区分
