"""门禁打分引擎

职责: 基于采样文本与目录信号对文档做要素打分与verdict判定(纯函数，无IO)。
不做: 不读取文件、不访问数据库、不调用LLM、不做阻断决策。

判定策略(宁松勿严):
- 目录是一等公民信号: 完整招标/投标文件通常有目录，且目录中高概率
  聚集"投标人须知/评标办法/投标函"等强特征词。特征命中目录条目按1.5倍
  权重计分，目录结构本身另有结构加成。
- 无可信目录时只允许长文档且强特征跨多个采样页分布的兜底通道放行，
  防止评分表摘录靠词面累计穿透门禁。
- 前N页几乎无文本 → unreadable(疑似扫描件)，文案与suspect区分，避免误伤
  扫描版标书。
"""

from loguru import logger

from app.core.parser.doc_gate.contracts import DocGateResult
from app.core.parser.doc_gate.feature_rules import get_features_for_doc_type
from app.core.parser.doc_gate.sampler import GateSample
from app.core.parser.doc_gate.toc_detector import detect_gate_toc

# 采样页文本总量低于该值视为无法读取(疑似扫描件/图片型PDF)
UNREADABLE_MIN_CHARS = 150

# 要素得分达到该阈值判定pass; 需高于纯目录结构加成(15)，
# 使"任意带目录的非标书文档"不能仅凭目录放行
PASS_THRESHOLD = 30.0

# 目录结构加成: 识别到规范目录条目本身即是正规文档的结构信号
TOC_BONUS_SMALL = 10.0   # >=5 条目录条目
TOC_BONUS_LARGE = 15.0   # >=10 条目录条目
TOC_BONUS_MIN_ENTRIES = 5
TOC_BONUS_LARGE_ENTRIES = 10

# 特征命中目录条目时的权重倍数
TOC_HIT_MULTIPLIER = 1.5

# 篇幅怀疑理由的页数阈值(仅用于文案，不参与打分)
THIN_DOC_PAGE_COUNT = 10

# 无可信目录时的长文档兜底条件
LONG_DOCUMENT_MIN_PAGES = 30
LONG_DOCUMENT_MIN_STRONG_FEATURES = 2
LONG_DOCUMENT_MIN_STRONG_PAGES = 2

_DOC_TYPE_NAMES = {"tender": "招标", "bid": "投标"}


def evaluate_doc_gate(sample: GateSample, doc_type: str) -> DocGateResult:
    """对采样结果执行门禁打分与判定

    Args:
        sample: PDF采样结果
        doc_type: 文档类型(tender/bid)

    Returns:
        DocGateResult: 门禁判定结果
    """
    type_name = _DOC_TYPE_NAMES.get(doc_type, "招标/投标")

    # 1. 无文本判定(疑似扫描件)
    if sample.total_chars < UNREADABLE_MIN_CHARS:
        return DocGateResult(
            verdict="unreadable",
            score=0.0,
            doc_type=doc_type,
            reasons=[
                f"文档前{sample.sampled_pages}页几乎无法提取文字内容，"
                f"可能是扫描件或图片型PDF，系统暂时无法确认其是否为完整的{type_name}文件",
            ],
            sampled_pages=sample.sampled_pages,
            page_count=sample.page_count,
        )

    # 2. 目录信号提取(复用TocParser的目录行识别)
    toc_evidence = detect_gate_toc(sample.page_texts)
    toc_entries = toc_evidence.entries
    toc_text = (
        "\n".join(entry.title for entry in toc_entries)
        if toc_evidence.is_confident
        else ""
    )

    # 3. 要素打分(目录命中按1.5倍权重)
    score = 0.0
    hit_features: list[str] = []
    missing_strong: list[str] = []
    strong_hit_count = 0
    strong_hit_pages: set[int] = set()

    for feature in get_features_for_doc_type(doc_type):
        in_toc = bool(toc_text) and bool(feature.pattern.search(toc_text))
        matching_pages = {
            page_index
            for page_index, page_text in enumerate(sample.page_texts)
            if feature.pattern.search(page_text)
        }
        in_body = bool(matching_pages)
        if in_toc or in_body:
            multiplier = TOC_HIT_MULTIPLIER if in_toc else 1.0
            score += feature.weight * multiplier
            hit_features.append(
                f"{feature.label}(目录命中)" if in_toc else feature.label
            )
            if feature.strong and in_body:
                strong_hit_count += 1
                strong_hit_pages.update(matching_pages)
        elif feature.strong:
            missing_strong.append(feature.label)

    # 4. 目录结构加成
    if (
        toc_evidence.is_confident
        and len(toc_entries) >= TOC_BONUS_LARGE_ENTRIES
    ):
        score += TOC_BONUS_LARGE
    elif (
        toc_evidence.is_confident
        and len(toc_entries) >= TOC_BONUS_MIN_ENTRIES
    ):
        score += TOC_BONUS_SMALL

    score = min(round(score, 1), 100.0)

    # 5. 完整性双通道: 可信目录优先，无目录仅允许长文档跨页强特征兜底
    has_long_document_fallback = (
        sample.page_count >= LONG_DOCUMENT_MIN_PAGES
        and strong_hit_count >= LONG_DOCUMENT_MIN_STRONG_FEATURES
        and len(strong_hit_pages) >= LONG_DOCUMENT_MIN_STRONG_PAGES
    )
    has_complete_structure = (
        toc_evidence.is_confident or has_long_document_fallback
    )

    # 6. verdict与怀疑理由
    if score >= PASS_THRESHOLD and has_complete_structure:
        verdict = "pass"
        reasons: list[str] = []
    else:
        verdict = "suspect"
        reasons = _build_suspect_reasons(
            type_name=type_name,
            missing_strong=missing_strong,
            toc_entry_count=len(toc_entries),
            page_count=sample.page_count,
            has_confident_toc=toc_evidence.is_confident,
            has_long_document_fallback=has_long_document_fallback,
        )

    logger.info(
        "门禁判定: doc_type={} verdict={} score={} toc_entries={} "
        "toc_confident={} long_fallback={} hits={}",
        doc_type,
        verdict,
        score,
        len(toc_entries),
        toc_evidence.is_confident,
        has_long_document_fallback,
        len(hit_features),
    )

    return DocGateResult(
        verdict=verdict,
        score=score,
        doc_type=doc_type,
        hit_features=hit_features,
        missing_features=missing_strong,
        reasons=reasons,
        sampled_pages=sample.sampled_pages,
        page_count=sample.page_count,
        toc_entry_count=len(toc_entries),
        has_confident_toc=toc_evidence.is_confident,
        has_long_document_fallback=has_long_document_fallback,
    )


def _build_suspect_reasons(
    type_name: str,
    missing_strong: list[str],
    toc_entry_count: int,
    page_count: int,
    has_confident_toc: bool,
    has_long_document_fallback: bool,
) -> list[str]:
    """生成面向用户的怀疑理由(前端直接渲染)"""
    reasons: list[str] = []

    if missing_strong:
        labels = "、".join(f"\u201c{label}\u201d" for label in missing_strong[:3])
        reasons.append(f"未检测到{labels}等{type_name}文件的关键要素")
    else:
        reasons.append(
            f"文档虽命中部分{type_name}相关用语，但不足以证明文件结构完整"
        )

    if not has_confident_toc and not has_long_document_fallback:
        reasons.append(
            f"未识别到可信目录，且篇幅或关键要素跨页分布不足，"
            f"可能不是完整的{type_name}文件"
        )
    elif toc_entry_count == 0:
        reasons.append(f"未识别到可信目录，完整{type_name}文件通常包含目录")

    if 0 < page_count < THIN_DOC_PAGE_COUNT:
        reasons.append(
            f"文档仅{page_count}页，完整{type_name}文件通常篇幅更长、章节更完整"
        )

    return reasons
