"""类别证据先验：要求类别到证据形态短语的闭集映射

职责: 维护"要求类别 -> 证据形态先验短语"的闭集映射,并提供基于先验的
 查询增强与检索饥饿检测
不做: 向量计算、检索结果排序、LLM调用

设计背景:
招标要求是"要求方措辞"(需提供/不低于),投标响应是"响应方形态"
(合同扫描件/验收意见),单一向量查询跨不过该视角错位。类别枚举是
稳定闭集(6类),以"类别 -> 投标文件中证据通常长什么样"的先验短语
补偿错位,替代逐案扩充的开放同义词表。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from loguru import logger
from sqlalchemy import func, or_, select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# 各类别在投标文件中的典型证据形态短语(闭集,与 RequirementCategory 对齐)
# 短语用途: ① 子查询增强(向量检索) ② 图片证据关键词过滤(字面包含)
# ③ 检索饥饿候选探测。每类不超过8个,优先选区分度高的形态词。
CATEGORY_EVIDENCE_PRIORS: dict[str, tuple[str, ...]] = {
    "QUAL": (
        "营业执照", "资质证书", "资格证书", "认证证书",
        "许可证", "信用证明", "审计报告",
    ),
    "TECH": (
        "技术方案", "实施方案", "技术参数", "检测报告",
        "测试报告", "技术规格",
    ),
    "COMM": (
        "报价表", "报价单", "承诺函", "付款方式",
        "保证金", "发票",
    ),
    "PERF": (
        "业绩", "合同", "验收证明", "验收意见",
        "验收报告", "中标通知书", "完工证明", "发票",  # 发票补缺
    ),
    "STAFF": (
        "身份证", "学历证书", "学位证书", "职称证书",
        "执业资格", "社保", "简历",
    ),
    "FORMAT": (
        "签字", "盖章", "签章", "授权委托书", "法定代表人",
    ),
}

# RequirementCategory 的 value(如 "performance") -> name(如 "PERF")
_VALUE_TO_NAME = {
    "qualification": "QUAL",
    "technical": "TECH",
    "commercial": "COMM",
    "performance": "PERF",
    "staffing": "STAFF",
    "format": "FORMAT",
}

# 查询增强时最多追加的先验子查询数(控制向量编码成本)
MAX_PRIOR_QUERIES = 4


@dataclass(frozen=True)
class StarvationFinding:
    """检索饥饿检测结果。"""

    uncovered_pages: tuple[int, ...]
    candidate_page_count: int
    phrases_used: tuple[str, ...]


def get_prior_phrases(category: object) -> list[str]:
    """获取类别对应的证据形态先验短语。

    Args:
        category: RequirementCategory 枚举、其 name("PERF")或 value("performance");
            None 或未知类别返回空列表(全链路退化为无先验行为)

    Returns:
        list[str]: 先验短语列表
    """
    if category is None:
        return []
    key = getattr(category, "name", None)
    if key is None:
        raw = str(category)
        key = raw.upper() if raw.upper() in CATEGORY_EVIDENCE_PRIORS else _VALUE_TO_NAME.get(raw.lower())
    return list(CATEGORY_EVIDENCE_PRIORS.get(key or "", ()))


def augment_queries_with_priors(
    queries: list[str],
    description: str,
    category: object,
    max_added: int = MAX_PRIOR_QUERIES,
    max_total: int = 20,
) -> list[str]:
    """以类别先验短语追加检索子查询,补偿要求方与响应方的措辞错位。

    生成形如 "{要求前30字} {先验短语}" 的子查询,与既有主题扩展格式一致。

    Args:
        queries: 既有子查询列表
        description: 招标要求原文
        category: 要求类别
        max_added: 最多追加的先验子查询数
        max_total: 子查询总数上限(与 MAX_SUB_QUERIES 对齐)

    Returns:
        list[str]: 增强后的子查询列表
    """
    phrases = get_prior_phrases(category)
    if not phrases:
        return queries

    base = description[:30]
    out = list(queries)
    seen = set(out)
    added = 0
    for phrase in phrases:
        if added >= max_added or len(out) >= max_total:
            break
        query = f"{base} {phrase}"
        if query not in seen:
            out.append(query)
            seen.add(query)
            added += 1
    if added:
        logger.debug(f"先验查询增强: category={category}, +{added}个子查询")
    return out


def augment_keywords_with_priors(
    keywords: list[str],
    category: object,
) -> list[str]:
    """以类别先验短语扩充关键词列表(用于图片证据字面过滤)。"""
    phrases = get_prior_phrases(category)
    if not phrases:
        return keywords
    out = list(keywords)
    seen = set(out)
    for phrase in phrases:
        if phrase not in seen:
            out.append(phrase)
            seen.add(phrase)
    return out


def _evaluate_starvation(
    candidate_pages: set[int],
    covered_pages: set[int],
    phrases: list[str],
    min_uncovered_pages: int = 2,
    candidate_total: Optional[int] = None,
    total_doc_pages: int = 0,
    max_candidate_ratio: float = 1.0,
) -> Optional[StarvationFinding]:
    """纯决策: 候选证据页与上下文覆盖页比对,判定是否检索饥饿。

    要素2(占比护栏): 当候选证据页占全文档比例过高时,说明所用短语在本文档
    无区分度(如遍布全文的盖章/签字),"大量未覆盖页"是类别级与要求级的粒度
    错配造成的虚高,而非真实漏检,直接抑制以避免系统性误报。

    Args:
        candidate_total: 候选页真实计数(不受明细 limit 截断);None 时退化为
            len(candidate_pages)。用于占比护栏分子。
        total_doc_pages: 全文档总页数(占比分母);0 表示未知,跳过占比护栏。
        max_candidate_ratio: 候选占比上限;>=1.0 表示关闭占比护栏。
    """
    effective_total = (
        candidate_total if candidate_total is not None else len(candidate_pages)
    )
    if (
        total_doc_pages > 0
        and 0 < max_candidate_ratio < 1.0
        and effective_total / total_doc_pages > max_candidate_ratio
    ):
        return None
    uncovered = sorted(candidate_pages - covered_pages)
    if len(uncovered) < min_uncovered_pages:
        return None
    return StarvationFinding(
        uncovered_pages=tuple(uncovered),
        candidate_page_count=effective_total,
        phrases_used=tuple(phrases[:5]),
    )


async def detect_retrieval_starvation(
    db: "AsyncSession",
    document_id: int,
    category: object,
    covered_pages: set[int],
    requirement_keywords: Optional[list[str]] = None,
    min_uncovered_pages: Optional[int] = None,
    candidate_limit: Optional[int] = None,
    max_candidate_ratio: Optional[float] = None,
) -> Optional[StarvationFinding]:
    """检测"文档存在【本要求相关】证据特征内容但未进入检索上下文"的饥饿状态。

    作为否决项落"未找到响应"前的最后防线: 即使未来出现新的检索失效
    模式,失败也会显式可见(标记转人工复核),而非伪装成业务结论。

    候选范围限定证据承载型分块(图片证据/条款/表格),避免普通段落中
    复述招标条款造成的误报。

    粒度错配修复:
    - 要素1(要求级约束): 候选页须同时命中"本要求关键词",把类别级证据池
      收窄到要求级,根除"同类别但与本要求无关材料页"造成的差集虚高。
      关键词为空(提取退化)时跳过该约束,改由占比护栏兜底。
    - 要素2(占比护栏): 候选占全文档比例过高(无区分度短语,如遍布全文的盖章)
      时抑制告警,在 _evaluate_starvation 内执行。

    Args:
        db: 数据库会话
        document_id: 投标文档ID
        category: 要求类别
        covered_pages: 已进入检索上下文的页码集合
        requirement_keywords: 当前要求的特异关键词(用于要素1收窄候选)
        min_uncovered_pages: 判定饥饿的最少未覆盖页数(None 时取配置)
        candidate_limit: 候选页明细探测上限(None 时取配置)
        max_candidate_ratio: 候选占比上限(None 时取配置)

    Returns:
        Optional[StarvationFinding]: 饥饿检测结果,未饥饿返回 None
    """
    phrases = get_prior_phrases(category)
    if not phrases:
        return None

    from app.core.config import settings
    from app.models.chunk import Chunk

    if min_uncovered_pages is None:
        min_uncovered_pages = settings.RETRIEVAL_STARVATION_MIN_UNCOVERED_PAGES
    if candidate_limit is None:
        candidate_limit = settings.RETRIEVAL_STARVATION_CANDIDATE_LIMIT
    if max_candidate_ratio is None:
        max_candidate_ratio = settings.RETRIEVAL_STARVATION_MAX_CANDIDATE_RATIO

    # 候选条件: 证据承载型分块 + 含类别先验短语
    conditions = [
        Chunk.document_id == document_id,
        Chunk.page_num.isnot(None),
        Chunk.chunk_type.in_(
            ("image_evidence", "image_annotation", "clause",
             "table_row_with_header", "table")
        ),
        or_(*[Chunk.content.contains(p) for p in phrases[:8]]),
    ]
    # 要素1: 叠加本要求关键词约束(收窄到要求级); 为空时退化为仅类别短语
    kw_terms = [k for k in (requirement_keywords or []) if k and len(k) >= 2][:8]
    if kw_terms:
        conditions.append(or_(*[Chunk.content.contains(k) for k in kw_terms]))

    # 候选页真实计数(不受明细 limit 截断), 供占比护栏分子
    candidate_total = int(
        (
            await db.execute(
                select(func.count(func.distinct(Chunk.page_num))).where(*conditions)
            )
        ).scalar()
        or 0
    )
    # 全文档总页数, 作为占比分母
    total_doc_pages = int(
        (
            await db.execute(
                select(func.count(func.distinct(Chunk.page_num))).where(
                    Chunk.document_id == document_id,
                    Chunk.page_num.isnot(None),
                )
            )
        ).scalar()
        or 0
    )
    # 候选页码明细(用于 relevance_note 展示与差集计算)
    page_rows = await db.execute(
        select(Chunk.page_num).where(*conditions).distinct().limit(candidate_limit)
    )
    candidate_pages = {int(p) for (p,) in page_rows.all() if p is not None}

    return _evaluate_starvation(
        candidate_pages,
        covered_pages,
        phrases,
        min_uncovered_pages=min_uncovered_pages,
        candidate_total=candidate_total,
        total_doc_pages=total_doc_pages,
        max_candidate_ratio=max_candidate_ratio,
    )


# 饥饿标记前缀: 前端 relevance_note 原样展示给用户, 措辞必须非技术化;
# 同时该前缀承担机器判定功能(quality_flags 注入 / 确定性未响应弃权),
# 判定一律走 is_starvation_note(), 禁止散落 startswith 词面判断。
STARVATION_NOTE_PREFIX = "[建议人工复核]"
# 存量数据库 relevance_note 仍以旧技术前缀开头, 判定必须新旧兼容
_LEGACY_STARVATION_PREFIXES = ("[检索饥饿]",)


def is_starvation_note(text: str | None) -> bool:
    """判定文本是否为检索饥饿标记(机器信号单源, 新旧前缀兼容)。"""
    value = (text or "").strip()
    return value.startswith((STARVATION_NOTE_PREFIX, *_LEGACY_STARVATION_PREFIXES))


def format_starvation_note(finding: StarvationFinding) -> str:
    """生成检索饥饿标记文本(写入 BidResponse.relevance_note, <=490字符)。

    面向用户展示, 禁止出现"检索/上下文/chunk"等技术词(用户反馈: 原
    "[检索饥饿]"表述对非技术用户不友好)。
    """
    pages = ",".join(str(p) for p in finding.uncovered_pages[:20])
    phrases = "/".join(finding.phrases_used[:3])
    note = (
        f"{STARVATION_NOTE_PREFIX} 投标文件第{pages}页可能包含与本要求相关的"
        f"材料(如{phrases}等), 但系统未能完整核对这些页面, "
        f"本条结论可能存在遗漏, 请人工核对上述页码后确认"
    )
    return note[:490]
