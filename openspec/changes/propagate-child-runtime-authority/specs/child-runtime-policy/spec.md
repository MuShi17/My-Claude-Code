## Purpose

保证父子 Agent 的 runtime policy 一致、可追溯且可独立回滚。

## ADDED Requirements

### Requirement: Child inherits runtime policy

child Agent MUST 继承父 Agent 的 authority、approval、rollback、capture policy、store/sink 和 parent run relation，除非显式覆盖。

#### Scenario: Shadow parent starts child

- **WHEN** shadow parent 启动 skill-fork 或 sub-agent
- **THEN** child 使用相同 shadow/capture/rollback policy，且有独立 child run_id 和 parent_run_id

### Requirement: Policy override is explicit

child policy 覆盖 MUST 可在配置快照或事件诊断中追溯，不能隐式发生。

#### Scenario: Child requests a policy override

- **WHEN** child 显式指定不同 policy
- **THEN** override source 可查询，且仍受父级 authority gate 约束
