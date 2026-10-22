"""
职责: 文档质检的领域契约——枚举、DocumentModel、证据、发现项、检查器产出
不做: 不解析 PDF、不访问数据库、不实现任何检查逻辑
"""

from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass, field
from typing import Any, Optional

# 规则集版本: 任何检查器判定语义变化都必须提升该版本(参与 run fingerprint)
RULESET_VERSION = "v1"

# bbox 归一化坐标系: 旋转后页面左上角为原点, 值域 0..1
BBox = tuple[float, float, float, float]


class QaCategory(str, enum.Enum):
    """检查类别(后端稳定枚举, 前端收敛为五组展示)"""

    STRUCTURE = "structure"          # Q1 结构
    TOC = "toc"                      # Q2 目录
    LAYOUT = "layout"                # Q3 版式(正文段落字体/字号离群)
    LANGUAGE = "language"            # Q4 语言(确定性子集)
    DUPLICATION = "duplication"      # Q5 重复
    NUMBERING = "numbering"          # Q6 编号
    CONSISTENCY = "consistency"      # Q7 一致性
    REFERENCE = "reference"          # Q8 引用
    HYGIENE = "hygiene"              # Q9 卫生
    INTEGRITY = "integrity"          # Q10 页面完整性


class QaSeverity(str, enum.Enum):
    """严重度: 文档影响程度, 不是法律后果分级"""

    P0 = "P0"  # 可能造成实质性错误(如金额/工期矛盾)
    P1 = "P1"  # 明显文档问题(如目录缺章、编号断档)
    P2 = "P2"  # 建议修改(如模板残留、疑似冗余)
    P3 = "P3"  # 优化提示(如标点混用、方向离群)


class QaCertainty(str, enum.Enum):
    """证据确定性(与严重度正交)"""

    CERTAIN = "certain"      # 确定性规则命中
    PROBABLE = "probable"    # 高置信启发
    POSSIBLE = "possible"    # 疑似, 需人工判断
    UNKNOWN = "unknown"      # 不可判定


class QaDisposition(str, enum.Enum):
    """人工处置状态"""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    FALSE_POSITIVE = "false_positive"
    IGNORED = "ignored"


class RunScope(str, enum.Enum):
    """QA 运行范围"""

    DOCUMENT = "document"    # 单文档(整本或单卷)
    ENVELOPE = "envelope"    # 投标信封(整本 1 卷或商务+技术 2 卷)收敛


class RunStatus(str, enum.Enum):
    """QA 运行状态"""

    RUNNING = "running"
    COMPLETED = "completed"
    DEGRADED = "degraded"    # 部分检查器失败, 结果可用但不完整
    FAILED = "failed"


class CheckerStatus(str, enum.Enum):
    """单检查器状态(fail-visible: 失败必须显形, 不得静默)"""

    OK = "ok"
    FAILED = "failed"
    DEFERRED = "deferred"    # 历史 run 合并兼容(V1 版式仅采集)
    SKIPPED = "skipped"


# ── DocumentModel ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class QaSpan:
    """fitz span: 同字体属性的连续字符段"""

    page: int
    text: str
    bbox: BBox
    font: str
    size: float
    flags: int
    color: int


@dataclass(frozen=True)
class QaLine:
    """fitz 文本行: 一行文字及其几何与样式片段"""

    page: int
    text: str
    bbox: BBox
    spans: tuple[QaSpan, ...] = ()
    rotated: bool = False


@dataclass(frozen=True)
class QaBlock:
    """fitz 文本块(约等于原始段落)"""

    page: int
    text: str
    bbox: BBox
    spans: tuple[QaSpan, ...] = ()
    lines: tuple[QaLine, ...] = ()


@dataclass(frozen=True)
class QaRegion:
    """版面区域及按位置落入其中的文件文字行"""

    page: int
    kind: str       # title/body/table/caption/furniture/toc/formula/visual (rules/regions.yaml)
    label: str      # 版面服务原始标签; 未落入任何区域的行归入 label 为空的正文区
    bbox: BBox
    order: int
    lines: tuple[QaLine, ...] = ()

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


@dataclass(frozen=True)
class QaPage:
    """页级事实"""

    page_num: int
    width: float
    height: float
    rotation: int
    char_count: int
    image_count: int
    table_count: int
    annotation_texts: tuple[str, ...] = ()
    # 无文件自带文字、页上却有图片或版面内容(扫描件/图片/文字转曲): 不在质检范围
    image_only: bool = False


@dataclass(frozen=True)
class QaHeading:
    """正文标题(heading_detector 产出)"""

    page: int
    level: int
    number: Optional[str]
    title: str
    bbox: Optional[BBox] = None
    kind: Optional[str] = None  # heading_detector 句式: cn_digit/paren/decimal/...

    @property
    def display(self) -> str:
        return f"{self.number} {self.title}".strip() if self.number else self.title


@dataclass(frozen=True)
class QaTocEntry:
    """目录页条目(含物理页解析结果)"""

    title: str
    printed_page: int
    physical_page: Optional[int]
    order_index: int


@dataclass(frozen=True)
class QaBookmark:
    """PDF 书签条目"""

    level: int
    title: str
    page: int


@dataclass(frozen=True)
class QaImage:
    """页内图片: 像素尺寸与显示框。低清放大判定用, 不参与文字检查。"""

    page: int
    pixel_width: int
    pixel_height: int
    bbox: BBox


@dataclass
class DocumentModel:
    """
    职责: 单份投标文档的质检输入模型(瞬态、构建后只读使用)
    不做: 不持久化自身、不承载检查结论
    """

    document_id: int
    bid_part: Optional[str]
    filename: str
    page_count: int
    pages: list[QaPage] = field(default_factory=list)
    blocks: list[QaBlock] = field(default_factory=list)
    headings: list[QaHeading] = field(default_factory=list)
    toc_entries: list[QaTocEntry] = field(default_factory=list)
    bookmarks: list[QaBookmark] = field(default_factory=list)
    images: list[QaImage] = field(default_factory=list)
    # 页号 -> 降噪后正文文本(来自 PdfParser, 页眉页脚/水印已剔除)
    body_texts: dict[int, str] = field(default_factory=dict)
    # 页号 -> 重建段落列表(检索通道, 可能已经 NFKC)
    paragraphs: dict[int, list[str]] = field(default_factory=dict)
    # 页号 -> fitz 原字形段落(可视通道, 字形检查专用)
    visual_paragraphs: dict[int, list[str]] = field(default_factory=dict)
    # 目录区页集合(点线目录与宽松目录同一口径)
    toc_zone: set[int] = field(default_factory=set)
    # 目录页坐标解析是否可靠(可靠时才做"标注页码 vs 物理页"比对)
    toc_resolution_reliable: bool = False
    toc_page_offset: Optional[int] = None
    # 版式统计(font|size -> 字符数直方图, layout 检查器基线)
    style_profile: dict[str, Any] = field(default_factory=dict)
    # 页号 -> 版面区域(仅有版面结果的页); 检查器经 region_text 取数, 无版面的页退回上面的旧来源
    regions: dict[int, list[QaRegion]] = field(default_factory=dict)
    # 映射表里没有的版面标签(已按正文处理) -> 出现次数
    unknown_region_labels: dict[str, int] = field(default_factory=dict)
    # 构建期降级说明(如超大文档跳过 span 级取证)
    degraded_reason: Optional[str] = None
    # 作者对象缓存(author_spans.AuthorView)。检查器经 ensure_author_view 读取; 未挂载时现算
    author_view: Any = None


# ── 证据与发现 ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EvidenceItem:
    """单条证据定位"""

    excerpt: str
    document_id: Optional[int] = None
    bid_part: Optional[str] = None
    page: Optional[int] = None
    bbox: Optional[BBox] = None
    role: str = "primary"  # primary / comparison

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "bid_part": self.bid_part,
            "page": self.page,
            "excerpt": self.excerpt,
            "bbox": list(self.bbox) if self.bbox else None,
            "role": self.role,
        }


@dataclass(frozen=True)
class Evidence:
    """发现项证据: single 单点 / pair 冲突配对 / sequence 序列"""

    kind: str
    items: tuple[EvidenceItem, ...]
    expected: Optional[str] = None
    actual: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "items": [item.to_dict() for item in self.items],
            "expected": self.expected,
            "actual": self.actual,
        }


@dataclass(frozen=True)
class Finding:
    """单条质检发现(语义红线: 禁止表达废标/不符合招标要求结论)"""

    rule_code: str
    category: QaCategory
    severity: QaSeverity
    certainty: QaCertainty
    title: str
    message: str
    evidence: Evidence
    suggestion: str = ""
    # 结构锚点: 参与 fingerprint, 优先用标题路径/槽位键/归一化文本哈希, 不依赖页码
    anchor: str = ""
    # 声称可从证据重算。为真时文本复核不能删除、也不能降级。不进 fingerprint。
    closed_measurement: bool = False

    def fingerprint(self, bid_part: Optional[str]) -> str:
        raw = "|".join([
            self.rule_code,
            bid_part or "",
            self.anchor or self.evidence.items[0].excerpt[:80] if self.evidence.items else "",
        ])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_code": self.rule_code,
            "category": self.category.value,
            "severity": self.severity.value,
            "certainty": self.certainty.value,
            "title": self.title,
            "message": self.message,
            "suggestion": self.suggestion,
            "evidence": self.evidence.to_dict(),
            "anchor": self.anchor,
        }


@dataclass
class CheckerOutcome:
    """单检查器产出"""

    code: str
    status: CheckerStatus
    findings: list[Finding] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class DocumentAnalysis:
    """单文档分析产出(analyzer 返回值)"""

    findings: list[Finding] = field(default_factory=list)
    checker_status: dict[str, str] = field(default_factory=dict)
    # 跨卷收敛消费的紧凑特征(实体槽位台账/段落哈希等), 必须可 JSON 序列化
    features: dict[str, Any] = field(default_factory=dict)
    degraded: bool = False


def normalize_for_hash(text: str) -> str:
    """段落哈希归一化: 去空白、统一全角括号逗号(重复检测与锚点共用)"""
    table = str.maketrans({"（": "(", "）": ")", "，": ",", "：": ":"})
    return "".join(text.split()).translate(table)


def paragraph_hash(text: str) -> str:
    return hashlib.sha256(normalize_for_hash(text).encode("utf-8")).hexdigest()
