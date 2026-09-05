## Context

C01～C10 分别提供契约、夹具、legacy correctness、domain/shadow sink、SQLite、Agent Loop、run lifecycle、projection、artifact/capture 和 canonical resume。C11 只验证和编排这些能力，不再发明事件语义。批次要求 final Gate 不执行 commit/push/merge/release/数据删除。

## Goals / Non-Goals

**Goals:**

- 以固定 scenarios、stable comparator、fault hooks、临时 HOME 和真实 CLI smoke 证明完整路径。
- 以 blocker/gap 记录驱动 authority 切换，提供可重复 rollback。
- 证实旧数据保护、privacy policy、canonical fail-closed 和诊断 fail-open 边界。

**Non-Goals:**

- 不通过放宽 comparator、删除 golden、忽略 fault 或手工改用户数据来达成 parity。
- 不进行发布流程、git 操作、远程 API 验证或清理旧数据。
- 不在 C11 修改 C01～C10 的 frozen event semantics；发现语义缺口必须回到相应 change。

## Decisions

1. **Scenario catalog 是单一验收入口。** 复用 C02 fixture builder，固定双 provider、permission/tool、child/terminal、storage/recovery、privacy 和 CLI cases；避免每层自定义不兼容的 smoke。
2. **稳定字段 comparator 分级。** identity关系、kind、status、ordinal、refs、tool pairing 是 blocker-sensitive；time/random/provider metadata 是允许/诊断字段，分类规则版本化。
3. **临时 HOME 隔离。** CLI smoke 将 HOME、工作目录、配置和 fake provider 全部指向临时目录，结束后由测试 harness 清理，不触碰用户文件。
4. **Authority 由 flag + gate 保护。** `legacy`、`shadow`、`canonical` 路由显式配置，canonical 只有在 blocker 清零时可启用；rollback 不删除任一存储。
5. **Gap report 可审计。** 每项 mismatch 包含 scenario、stable diff、分类、影响、关联 task/test 和 owner；golden 更新必须保留 before/after evidence。

## Risks / Trade-offs

- [Parity 过严造成 provider 合法差异误报] → 只比较 C01/C08 定义的 stable semantic fields，provider wire metadata 单独展示。
- [Parity 过松掩盖实际丢事件] → 对 tool pairing、terminal、ordinal、refs、redaction 和 recovery 分类设置 blocker 级断言。
- [临时 smoke 与真实安装差异] → 使用项目真实 CLI 入口和隔离 HOME，另记录依赖探测结果；不调用真实外部 API。
- [切换后 rollback 不完整] → 切换前/后验证旧文件和 runtime.sqlite hash/可读性，路由 flag 之外禁止 destructive 操作。

## Migration Plan

先在 shadow authority 下跑全 catalog，闭合 blocker；再在临时 HOME 启用 canonical-first，重跑同一 catalog 和 resume smoke；若失败切回 legacy route 并保留所有数据。正式 authority 变更需由用户另行授权，不由本 change 自动执行。
