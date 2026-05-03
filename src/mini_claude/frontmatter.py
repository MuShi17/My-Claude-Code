"""Shared YAML frontmatter parser for memory and skills files.
Handles simple `key: value` pairs between `---` delimiters."""

from dataclasses import dataclass, field


@dataclass
class FrontmatterResult:
    """解析结果的数据类，包含元数据字典和正文内容。"""
    meta: dict[str, str] = field(default_factory=dict)
    body: str = ""


def parse_frontmatter(content: str) -> FrontmatterResult:
    """解析 frontmatter 格式的文本内容。

    支持的格式：
        ---
        key1: value1
        key2: value2
        ---
        正文内容

    Args:
        content: 待解析的文本内容

    Returns:
        FrontmatterResult: 包含解析后的元数据字典和正文内容
    """
    lines = content.split("\n")

    # 检查是否以 --- 开头，如果不是则整个内容作为正文返回
    if not lines or lines[0].strip() != "---":
        return FrontmatterResult(body=content)

    # 查找结束的 ---
    end_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break

    # 如果没有找到结束的 ---，则整个内容作为正文返回
    if end_idx == -1:
        return FrontmatterResult(body=content)

    # 解析元数据部分（--- 之间的内容）
    meta: dict[str, str] = {}
    for i in range(1, end_idx):
        colon_idx = lines[i].find(":")
        if colon_idx == -1:
            continue
        key = lines[i][:colon_idx].strip()
        value = lines[i][colon_idx + 1:].strip()
        if key:
            meta[key] = value

    # 提取正文内容（第二个 --- 之后的内容）
    body = "\n".join(lines[end_idx + 1:]).strip()
    return FrontmatterResult(meta=meta, body=body)


def format_frontmatter(meta: dict[str, str], body: str) -> str:
    """将元数据和正文格式化为 frontmatter 格式的字符串。

    Args:
        meta: 元数据字典，key 和 value 都是字符串
        body: 正文内容

    Returns:
        str: frontmatter 格式的完整字符串
    """
    lines = ["---"]
    for key, value in meta.items():
        lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    return "\n".join(lines)
