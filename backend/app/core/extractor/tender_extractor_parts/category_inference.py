"""招标要求分类（category）文本推断（追加）。

职责: 从要求描述与关键词推断六类 category(QUAL/TECH/COMM/PERF/STAFF/FORMAT)，
 低置信时返回 None。
不做: 不判定 review_relevance、不调用 LLM、不访问 DB、不改写 description、
 不做"推不出就给默认分类"的兜底（保持无法识别即失败，交人工复核）。

分层设计（顺序即优先级，后一层仅在前一层未命中时生效）:
1. 业务实体层: 资质/业绩/人员/报价/技术/格式的实体名词，语义最强。
2. 程序性词表层: 复用 `noise_rules.yaml` 的 `by_category`——那是项目对
 "该词汇归属哪一类"的既有约定，避免另建一套词表造成漂移(教训)。
 仅在第 1 层无结果时参与，保证既有推断结果逐字不变。

第 2 层只覆盖 `noise_rules.yaml` 中**已显式归类**的词汇；`global` 段的招标程序
与投标人纪律词汇(开标流程/异议渠道/串通投标等)项目未赋类别，此处同样不赋，
以免污染确认页分类分组。
"""

from __future__ import annotations

from app.core.extractor.tools.noise_classifier import load_noise_rules

# 业务实体关键词（第 1 层）。顺序即优先级：实体特异性由高到低，FORMAT 兜底最后。
_BUSINESS_ENTITY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("QUAL", ("资质", "营业执照", "证书", "法人", "ISO", "认证")),
    ("PERF", ("业绩", "类似项目", "合同金额", "项目经验")),
    ("STAFF", ("项目经理", "负责人", "人员", "任职", "学历", "职称")),
    ("COMM", ("报价", "价格", "保证金", "付款", "税率", "交付")),
    ("TECH", ("技术", "测试", "参数", "方案", "验收", "标准")),
    ("FORMAT", ("签章", "盖章", "格式", "份数", "装订")),
)

# 第 2 层参与的类别顺序（与第 1 层保持一致，FORMAT 最后，避免版面词抢占实体类）。
_NOISE_VOCAB_CATEGORY_ORDER: tuple[str, ...] = (
    "QUAL", "PERF", "STAFF", "COMM", "TECH", "FORMAT",
)

# `noise_rules.yaml` 中按类别归类的降级级别（两级都代表"该词汇属于此类别"）。
_NOISE_VOCAB_LEVELS: tuple[str, ...] = ("procedural", "informational")

# 递交方式补充词（第 2 层）。依据提取 Prompt 的既有约定——
# "物理形态与递交方式"归 FORMAT——补齐 `noise_rules.yaml` 未收录的递交类表述。
_SUBMISSION_FORMAT_KEYWORDS: tuple[str, ...] = (
    "递交", "密封", "封标", "封标要求", "投标文件送达", "文件送达",
)


def build_inference_text(description: str | None, keywords: list[str] | None) -> str:
    """把描述与关键词拼成用于匹配的文本。"""
    parts = [str(description or "")]
    parts.extend(str(kw) for kw in (keywords or []))
    return " ".join(parts)


def _match_business_entity(text: str) -> str | None:
    """第 1 层：业务实体关键词匹配。"""
    for category, entity_keywords in _BUSINESS_ENTITY_RULES:
        if any(keyword in text for keyword in entity_keywords):
            return category
    return None


def _collect_categorized_noise_vocab() -> dict[str, tuple[str, ...]]:
    """从 `noise_rules.yaml` 的 `by_category` 汇总"词汇 -> 类别"归属。

    配置缺失或结构异常时返回空字典，使第 2 层静默失效而不影响第 1 层。
    """
    by_category = load_noise_rules().get("by_category")
    if not isinstance(by_category, dict):
        return {}

    collected: dict[str, tuple[str, ...]] = {}
    for category in _NOISE_VOCAB_CATEGORY_ORDER:
        category_rules = by_category.get(category)
        if not isinstance(category_rules, dict):
            continue
        vocab: list[str] = []
        for level in _NOISE_VOCAB_LEVELS:
            level_keywords = category_rules.get(level) or []
            if isinstance(level_keywords, list):
                vocab.extend(str(kw) for kw in level_keywords if str(kw).strip())
        if vocab:
            collected[category] = tuple(vocab)
    return collected


def _match_procedural_vocab(text: str) -> str | None:
    """第 2 层：已归类的程序性/递交类词汇匹配。"""
    for category, vocab in _collect_categorized_noise_vocab().items():
        if any(keyword in text for keyword in vocab):
            return category
    if any(keyword in text for keyword in _SUBMISSION_FORMAT_KEYWORDS):
        return "FORMAT"
    return None


def infer_requirement_category(
    description: str | None,
    keywords: list[str] | None = None,
) -> str | None:
    """推断要求分类，低置信返回 None（调用方据此保留失败语义）。"""
    text = build_inference_text(description, keywords)
    if not text.strip():
        return None
    return _match_business_entity(text) or _match_procedural_vocab(text)
