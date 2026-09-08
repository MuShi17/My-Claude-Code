## ADDED Requirements

### Requirement: read_file SHALL 支持 Maka 语义的按行 offset/limit

`read_file` SHALL 接受必需的 `file_path` 和可选的 `offset`、`limit`。`offset` SHALL 是非负整数的 0-based 起始行索引，默认值为 0；`limit` SHALL 是正整数的最大行数，缺省时读取到文件末尾。无两个分页参数时，工具 SHALL 尝试返回完整文件结果，并受公共 16 MiB（16,777,216）canonical JSON UTF-8 字节上限约束。

#### Scenario: 无参数读取完整文件

- **WHEN** 模型调用 `read_file` 只提供 `file_path` 且最终带行号结果的 canonical JSON UTF-8 序列化不超过 16,777,216 字节
- **THEN** 工具返回从第一行到文件末尾的完整带行号正文

#### Scenario: offset 从零开始

- **WHEN** 模型调用 `read_file` 使用 `offset=0, limit=2`
- **THEN** 工具返回原文件第一、第二行，而不是从第二行或第零行之后开始

#### Scenario: limit 缺省读取到末尾

- **WHEN** 模型调用 `read_file` 使用合法 `offset` 但不提供 `limit`
- **THEN** 工具从 offset 行开始读取到文件末尾，不人为截取固定字符数

#### Scenario: 单页结果仍受公共上限约束

- **WHEN** 一个带分页参数的 `read_file` 页在添加行号后其 canonical JSON UTF-8 序列化超过 16,777,216 字节
- **THEN** 工具返回受控超限错误并提示缩小 `limit`，不返回头尾拼接的伪分页结果

### Requirement: read_file SHALL 保留原文件行号和确定的换行语义

工具 SHALL 在切片前按当前文件读取语义拆分行，在切片后使用原文件 1-based 行号格式渲染。实现 SHALL 对空文件、越界 offset、最后无换行、LF、CRLF 和 Unicode 内容给出稳定结果；Maka worker 不添加行号不改变本项目保留行号的公共工具合同。

#### Scenario: 分页行号对应原文件

- **WHEN** 文件至少有四行且调用 `offset=2, limit=2`
- **THEN** 返回第三、第四行，并显示原文件的 3、4 行号，而不是分页后的 1、2

#### Scenario: offset 超过末尾

- **WHEN** 合法 offset 大于文件行数且 limit 合法
- **THEN** 工具返回确定的空结果或项目约定的空页结果，不读取不存在的行，也不生成 archive ref

#### Scenario: CRLF 和最后无换行文件保持可解释

- **WHEN** 输入文件使用 CRLF 或最后一行没有换行符
- **THEN** 输出遵守冻结的换行处理规则，不丢失原文件行边界，且行号保持正确

#### Scenario: Unicode 行按字符内容返回

- **WHEN** 文件包含 Unicode 文本并调用合法分页
- **THEN** 工具按完整 Unicode 行返回，不因 UTF-8 字节边界把字符拆坏

### Requirement: read_file SHALL 严格校验分页参数并避免副作用

工具 MUST 拒绝 bool、负数、零 `limit`、浮点、无法解析的字符串和其他非法 offset/limit。参数错误 SHALL 在文件读取和 archive 写入之前返回，并保持稳定、可诊断的错误形状。

#### Scenario: 非法 offset 被拒绝

- **WHEN** `offset` 为负数、浮点、bool 或无法解析的字符串
- **THEN** 工具返回参数错误，不读取文件、不更新 read-file mtime 状态、不写入 ArtifactArchive

#### Scenario: 非法 limit 被拒绝

- **WHEN** `limit` 为 0、负数、浮点、bool 或无法解析的字符串
- **THEN** 工具返回参数错误，不读取文件、不更新 read-file mtime 状态、不写入 ArtifactArchive

#### Scenario: 合法边界值被接受

- **WHEN** `offset` 为 0 且 `limit` 为正整数，或只提供合法 offset
- **THEN** 工具按行执行分页，并保持先读后改状态的既有成功语义

### Requirement: read_file 分页 SHALL 与 ArchiveRead 单位明确分离

`read_file` 的 offset/limit 单位 SHALL 固定为行；通用 `ArchiveRead` 的文本页使用字符单位、二进制页使用字节单位。工具 schema、描述、错误和测试不得把两套同名参数解释为同一单位。

#### Scenario: 两套分页工具的单位不混淆

- **WHEN** 模型先调用 `read_file(offset=2, limit=2)`，随后调用 `ArchiveRead(read, offset=2, limit=2)`
- **THEN** 前者按第 3 行开始返回，后者按 ArchiveRead 声明的字符/字节单位返回，且各自 metadata 明确标注单位
