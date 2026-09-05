## 1. Schema 与连接

- [x] 1.1 设计并实现 `runtime_events`、session ordinal、partial snapshot、run state、LLM capture 表、索引和 schema version，并用新库/重开测试验证自动初始化
- [x] 1.2 实现单逻辑 writer 的连接、事务、busy/locked、close/reopen 和 migration 边界，并用临时目录与 fault hook 验证无隐式数据删除
- [x] 1.3 将 C04 RuntimeEvent canonical bytes、digest、identity 和 refs 映射到数据库行，并用 round-trip/corruption fixture 验证严格 decode

## 2. Append 与封存

- [x] 2.1 实现事务内 ordinal 分配、按 ordinal 读取和 session/turn/run 索引，并用同 timestamp、多 run、child run fixture 验证顺序唯一
- [x] 2.2 实现 exact-ID/payload idempotency 与 conflict error，并用重复提交/冲突提交 fixture 验证不产生额外事实
- [x] 2.3 实现 terminal seal、唯一终态、sealed-run rejection 和 partial snapshot bounded API，并用 terminal/partial crash fixture 验证状态机
- [x] 2.4 实现 immutable prefix/high-water/digest 读取，并用追加前后 prefix fixture 验证历史 prefix 不变且读取不产生写入

## 3. 故障与交付 Gate

- [x] 3.1 为 validation、I/O、commit、locked、schema、corruption、sealed 和 idempotency conflict 提供 typed errors，并用 fault injection 验证错误可分类
- [x] 3.2 将 SQLite sink 接入 C04 EventSink 协议但保持 C06 前不改变工具执行 authority，并运行 C02 store contract suite 验证
- [x] 3.3 运行完整离线测试和 `openspec validate --changes --strict`，确认 C05 的实现只依赖 `sqlite3` 且保留所有 legacy 文件
