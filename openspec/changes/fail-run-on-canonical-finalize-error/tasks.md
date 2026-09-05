## 1. Implementation

- [x] 1.1 将 canonical finalize/flush/close failure 转换为 controlled run failure
- [x] 1.2 区分 provider 原始异常、canonical failure 和 shadow diagnostic，并保留错误原因

## 2. Verification

- [x] 2.1 注入 append/seal/fsync/finalizer 故障验证唯一 failure terminal
- [x] 2.2 验证 CLI/Agent 调用方收到非成功状态且 canonical 数据不被删除或覆盖
