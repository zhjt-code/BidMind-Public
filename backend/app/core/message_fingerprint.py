"""消息指纹计算

将日志消息中的可变部分(数字、UUID、时间戳、IP等)替换为占位符,
然后取 MD5 哈希作为指纹。同指纹的日志即为"同类错误"。

核心设计:
  - normalize_message(): 保守归一化, 用于 UI 显示, 保留可读性
  - compute_fingerprint(): 激进归一化 + 截断, 用于分组, 最大化聚合效果
    额外处理: snake_case 标识符、key=value 值、管道间值、
    `#` 前通道名, 以及截断尾部详情(只保留第一句)
"""

import hashlib
import re
from typing import Optional

# 算法版本: 每次修改归一化规则时递增, 用于自动触发存量指纹重算
FINGERPRINT_ALGORITHM_VERSION = 3

# ── 第一阶段: 基础归一化 (用于显示 + 指纹计算的基础) ──

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # 长引号包裹的文本 (>=20字符, 如嵌入的文档内容/错误描述)
    (re.compile(r"'[^']{20,}'"), "'{...}'"),
    (re.compile(r'"[^"]{20,}"'), '"{...}"'),
    # Base64 长串 (>=32字符, 提高阈值避免误匹配长英文单词如 InvalidArgumentError)
    (re.compile(r"[A-Za-z0-9+/]{32,}={0,2}"), "{BASE64}"),
    # UUID (8-4-4-4-12 hex)
    (re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"), "{UUID}"),
    # ISO 8601 datetime
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "{TS}"),
    # 日期 YYYY-MM-DD
    (re.compile(r"\d{4}-\d{2}-\d{2}"), "{DATE}"),
    # IPv4
    (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "{IP}"),
    # 长十六进制串 (>=8位)
    (re.compile(r"\b[0-9a-fA-F]{8,}\b"), "{HEX}"),
    # 长字母数字混合串 (>=16字符, 常见于 token/key/session id)
    (re.compile(r"\b[A-Za-z0-9_-]{16,}\b"), "{TOKEN}"),
    # 文件路径中的变量段
    (re.compile(r"(?<=/)[0-9a-fA-F_-]{6,}(?=/)"), "{ID}"),
    # 绝对/相对文件路径
    (re.compile(r"""(?:[A-Z]:\\|/)[\w/\\._-]{10,}"""), "{PATH}"),
    # 数字序列
    (re.compile(r"\d+"), "{N}"),
]

# ── 第二阶段: 压缩重复占位符 ──

_COLLAPSE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(\{BASE64\}[\s,;]*){2,}"), "{BASE64}..."),
    (re.compile(r"(\{TOKEN\}[\s,;]*){2,}"), "{TOKEN}..."),
    (re.compile(r"(\{N\}[\s.,;:/-]*){3,}"), "{N}..."),
    (re.compile(r"(\{HEX\}[\s,;]*){2,}"), "{HEX}..."),
]

# ── 第三阶段: 仅用于指纹计算的激进归一化 (不影响 UI 显示) ──
# 注意: 这些模式运行在基础归一化之后, 文本中已包含 {N}, {TOKEN} 等占位符,
# 因此字符类需要兼容 {} 语法。

_FINGERPRINT_EXTRA_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # 所有引号字符串 (必须先于 snake_case, 防止 'RATE_LIMITED' 等被拆散)
    (re.compile(r"'[^']+'"), "'{...}'"),
    (re.compile(r'"[^"]+"'), '"{...}"'),
    # snake_case 标识符 (含下划线, 如 env_local, cloud_api, retry_count)
    (re.compile(r"\b[a-zA-Z][a-zA-Z0-9]*(?:_[a-zA-Z0-9]+)+\b"), "{ID}"),
    # `#` 前的通道/实例标识符 (含已替换的占位符如 {TOKEN})
    (re.compile(r"[^\s,;#]+(?=#)"), "{ID}"),
    # `|` 之间的值 (如 chat|model_name|, 兼容含 {N} 等占位符的文本)
    (re.compile(r"(?<=\|)[^\s|]+(?=\|)"), "{ID}"),
    # `=` 后的值 (排除句点, 以便后续句子截断能正确工作)
    (re.compile(r"(?<==)[^\s,;.]+"), "{V}"),
]

# 指纹尾部截断: 移除第一个 ". " 之后的内容(错误详情/堆栈摘要等),
# 只保留错误类型前缀用于分组
_FINGERPRINT_SENTENCE_CUT = re.compile(r"\.\s+.*$")

# 最终截断长度: 防御性兜底, 避免超长消息前缀
_FINGERPRINT_TRUNCATE_LEN = 200


def normalize_message(message: str) -> str:
    """将消息中的可变部分替换为占位符,保留结构性文本。用于 UI 显示。"""
    result = message
    for pattern, placeholder in _PATTERNS:
        result = pattern.sub(placeholder, result)
    for pattern, replacement in _COLLAPSE_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def compute_fingerprint(
    module: Optional[str],
    level: str,
    message: str,
) -> str:
    """计算消息指纹: module + level + 激进归一化 + 截断 → MD5。

    与 normalize_message (用于显示) 相比, 指纹计算额外做:
      1. 替换短标识符 (snake_case、通道名、key=value 值、管道间值)
      2. 截断至第一句 (移除 ". " 之后的错误详情)
      3. 兜底截断至 200 字符
    """
    normalized = normalize_message(message)
    for pattern, placeholder in _FINGERPRINT_EXTRA_PATTERNS:
        normalized = pattern.sub(placeholder, normalized)
    normalized = _FINGERPRINT_SENTENCE_CUT.sub("", normalized)
    normalized = normalized.rstrip(".")
    truncated = normalized[:_FINGERPRINT_TRUNCATE_LEN]
    raw = f"{module or ''}|{level}|{truncated}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()
