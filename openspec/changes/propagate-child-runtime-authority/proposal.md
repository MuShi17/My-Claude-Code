## Why

父 Agent 创建 child 时没有传递 runtime authority、approval、rollback 和 capture policy，可能导致父子 Agent 使用不同的事实源和隐私边界。

## What Changes

- 明确 child runtime policy 继承/覆盖规则。
- 传递 authority、approval、rollback、capture policy、store/sink 和 parent run identity。
- 增加 parent/child、nested child、取消和 rollback 测试。

## Impact

影响 `agent.py` skill/sub-agent 构造路径及生命周期测试。
