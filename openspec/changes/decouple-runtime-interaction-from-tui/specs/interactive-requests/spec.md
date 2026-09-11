## Purpose

规定人工交互请求（审批与提问）的身份绑定、状态机、异步等待与失效语义，并确保获得批准不会绕过既有安全规则。

## ADDED Requirements

### Requirement: 人工交互请求的身份与状态

系统 MUST 提供人工交互请求对象，覆盖审批（approval）与提问（question）两种语义，并 MUST 绑定 `request_id`、session/run 身份、`tool_call_id`、请求种类与被请求参数的摘要。回复若携带 session/run/tool 身份，runtime MUST 校验其与请求身份一致；不得仅凭相同参数摘要接受跨 run/tool 回复。请求 MUST 具有明确状态：`pending`、`resolved`、`expired`、`cancelled`，且状态转移 MUST 单向、终态唯一。提问的回答 MUST NOT 被当作工具授权。

#### Scenario: 提问回答不能授权工具

- **WHEN** 一个 question 请求被回答
- **THEN** 该回答只作为运行输入传递，不产生任何工具 dispatch 授权

#### Scenario: 参数被篡改的回复无效

- **WHEN** 回复携带的参数摘要与请求绑定的摘要不一致
- **THEN** 回复被拒绝，请求状态不变，且不触发工具执行

#### Scenario: 跨 run 或跨工具的回复无效

- **WHEN** 用属于另一 run 或另一 tool_call 的回复响应某请求
- **THEN** 回复被拒绝，且不触发工具执行

### Requirement: 等待人工输入不得阻塞控制面

等待交互期间，runtime MUST 保持可响应取消与状态查询：等待 MUST 基于可取消的异步等待对象，MUST NOT 使用阻塞式终端读取。runtime 内 MUST NOT 保留终端 `input` fallback；终端输入只能作为端口适配器实现存在。请求被取消或过期后到达的回复 MUST NOT 触发工具执行。

#### Scenario: 等待审批期间可取消

- **WHEN** 某 run 正在等待审批，调用方请求取消该 run
- **THEN** 取消被接受并生效，等待被解除，且该请求转为 `cancelled` 而不是继续等待

#### Scenario: 过期回复不触发执行

- **WHEN** 请求已过期后收到回复
- **THEN** 系统返回过期错误且不执行任何工具

#### Scenario: runtime 内不存在终端输入回退

- **WHEN** 检查 runtime 模块（`agent.py`、`interactions.py`、`tools.py`）的交互路径
- **THEN** 不存在直接调用终端 `input` 的分支；终端读取只出现在端口适配器实现中

### Requirement: 回复的幂等与冲突处理

同一请求的重复回复 MUST 幂等：相同回复返回原结果，MUST NOT 重复执行工具。与首次回复冲突的第二次回复 MUST 被明确拒绝。审批通过与取消、终态 MUST 在同一控制序列化边界内处理，MUST NOT 依赖前端禁用按钮保证一致性。

#### Scenario: 重复相同回复幂等

- **WHEN** 对同一请求提交两次完全相同的回复
- **THEN** 第二次返回与第一次相同的结果，且工具只被执行一次

#### Scenario: 冲突回复被拒绝

- **WHEN** 对同一请求先批准再拒绝（或反之）
- **THEN** 第二次回复被明确拒绝，请求状态保持首次结果

#### Scenario: 审批与取消竞争只有一个结果

- **WHEN** 审批回复与取消请求几乎同时到达
- **THEN** 系统只产生一个确定结果，且工具至多执行一次

### Requirement: 批准不绕过既有安全规则

获得一次审批 MUST NOT 使执行跳过既有保护：执行前 MUST 重新校验真实参数、当前 deny 策略与文件「先读后改」的 mtime 保护。权限模式（plan/dontAsk/deny 规则）语义 MUST 保持不变。

#### Scenario: 批准后被 deny 规则拦截

- **WHEN** 某调用在回复后命中当前 deny 规则
- **THEN** 执行被拒绝，工具不运行

#### Scenario: 批准后文件已被外部修改

- **WHEN** 审批通过但目标文件在批准与执行之间被外部修改
- **THEN** 先读后改保护生效，执行被拒绝并要求重新读取
