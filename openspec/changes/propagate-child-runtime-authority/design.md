## Decisions

1. child 默认继承父 Agent 的 authority、approval、rollback、capture policy、store 和 sink。
2. 只有显式 child 配置可以覆盖继承值，覆盖必须进入事件/诊断 metadata。
3. parent_run_id 与 child run_id 必须保持独立，不能扁平化事件。
4. child 不能绕过父级 Canonical gate，也不能隐式创建不同路径的 store。
