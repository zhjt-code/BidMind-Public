"""复合招标要求分解器

将复合型招标要求(如"项目经理、技术负责人、安全负责人均需提供
身份证和社保缴费证明")分解为多个子查询,提升语义检索的召回率。

分解策略:
1. 规则优先: 基于中文并列结构(顿号、和、及)拆分主体和客体
2. 笛卡尔积: 主体 x 客体 生成子查询
3. 安全上限: 子查询数不超过 MAX_SUB_QUERIES
"""

import re

from loguru import logger

MAX_SUB_QUERIES = 20


# 需要"提供/提交/出具"的动词,用于定位客体列表的起始位置
# 补充"包含/配备/配置/落实/列支"类动作表达——QHSE"须包含A、B…及G等材料"
# 与安全措施"配备…、落实…"句式此前无法定位客体段,整句退化为单条长查询。
# "配置"仅接受带须/需/应前缀的动词用法,避免误匹配"人员配置"等名词。
_ACTION_VERBS = re.compile(
    r"(须?提供|须?提交|须?出具|须?具备|须?持有|须?取得|需提供|需提交|应提供|应提交"
    r"|[须需应]?包含|[须需应]配置|配备|落实|列支)"
)

# 中文并列分隔符(顿号优先,再匹配"和/及/与",但后者要求后跟 >=2 字)
_CN_ENUM_SEP = re.compile(r"[、]")
_CN_CONJ = re.compile(r"(?:和|及|与)(?=\S{2,})")

# 依据状语句式。"以A、B为依据/为准"里的 A、B 是证据载体本身,
# 但该句式不带"提供/提交"类动词, 客体抽取此前完全看不到它
# (如"同类项目业绩（以项目合同、结算发票为依据）"漏掉发票)。
_EVIDENCE_BASIS = re.compile(r"以(.{2,60}?)[为作](?:依据|准|凭证|凭据|证明|据)")
# 时间基准而非证据材料: "以合同签订日期为准"这类项不构成可检索的材料名
_TIME_BASIS_SUFFIXES = ("日", "日期", "时间", "时", "年", "月", "季度")

# 全/半角括号内的补充说明(如"近3年内（以合同签订日期为准）")
_PAREN_CONTENT = re.compile(r"[（(][^()]*[)）]")

# 无区分度的泛化主体词。作为关键词会命中全文档,
# 导致全局图片检索按页码序被文档前部无关扫描件占满。
GENERIC_SUBJECT_TERMS = frozenset({
    "投标人", "招标人", "投标方", "招标方", "甲方", "乙方",
    "供应商", "应答人", "采购人", "中标人", "委托方", "受托方",
})


def _split_enum(text: str) -> list[str]:
    """按顿号拆分,再处理末尾的"和/及/与"连接词"""
    parts = _CN_ENUM_SEP.split(text)
    result: list[str] = []
    for p in parts:
        sub = _CN_CONJ.split(p)
        for s in sub:
            s = s.strip()
            if len(s) >= 2:
                result.append(s)
    return result


def _extract_subjects(description: str) -> list[str]:
    """提取并列主体(动作执行者)

    匹配模式: "A、B、C均需/须/应..."
    """
    m = re.search(
        r"([\u4e00-\u9fff\w、，,和及与]+?)[均都全]?(?:须|需|应|必须)",
        description,
    )
    if not m:
        return []
    raw = m.group(1).strip()
    subjects = _split_enum(raw)
    return [s for s in subjects if 2 <= len(s) <= 20]


def _strip_paren_content(text: str) -> str:
    """剥离全/半角括号内的补充说明，保留主干短语。"""
    prev = None
    while prev != text:
        prev = text
        text = _PAREN_CONTENT.sub("", text)
    return text.strip()


def _extract_basis_objects(description: str) -> list[str]:
    """提取依据状语中的证据载体。

 "以A、B为依据/为准"里的 A、B 是要求认可的证据材料本身, 但该句式不带
 "提供/提交"动词, _extract_objects 看不到它(如"业绩以项目合同、结算发票
 为依据"漏掉发票)。剥离时间基准项(以合同签订日期为准), 只留材料名。
 """
    objects: list[str] = []
    seen: set[str] = set()
    for m in _EVIDENCE_BASIS.finditer(description):
        for candidate in _split_enum(m.group(1)):
            candidate = _strip_paren_content(candidate)
            if not (2 <= len(candidate) <= 40) or candidate in seen:
                continue
            if candidate.endswith(_TIME_BASIS_SUFFIXES):
                continue
            seen.add(candidate)
            objects.append(candidate)
    return objects


def _extract_objects(description: str) -> list[str]:
    """提取并列客体(需要提供的材料/证件)

 匹配模式: "提供A、B和C" 或 "提交A及B": 改为多动词匹配——复合要求常含多个"需提供/须提交"段,
 仅取首个动词会漏掉后续高价值客体(如"需提供合同或验收证明等材料")。
 客体候选先剥离括号补充说明再做长度过滤,避免长插入语顶爆上限。: 追加依据状语中的证据载体(以A、B为依据), 该句式无动词。
 """
    objects: list[str] = []
    seen: set[str] = set()
    for m in _ACTION_VERBS.finditer(description):
        after = description[m.end():].strip()
        # 取到句号/分号/逗号/句末
        end_m = re.search(r"[。；;,，]", after)
        if end_m:
            after = after[: end_m.start()]
        for candidate in _split_enum(after):
            candidate = _strip_paren_content(candidate)
            if 2 <= len(candidate) <= 40 and candidate not in seen:
                seen.add(candidate)
                objects.append(candidate)
    for candidate in _extract_basis_objects(description):
        if candidate not in seen:
            seen.add(candidate)
            objects.append(candidate)
    return objects


# 按语言边界(虚词/连接词/标点)切分, 替代固定长度窗口。
# 固定 2-6 字窗口把"结算发票为依据"(7字)整体丢弃, 又把降级路径切成
# "结算发票为"这类跨词碎片; 改为在虚词处断开, 让核心名词自然浮现。
_PARTICLE_SPLIT = re.compile(
    r"(?:为(?:依据|准|凭证|凭据)?|以|按|须|需|应|的|等|或者|或|和|及|与|之|其|"
    r"至少|不(?:低于|少于|高于|超过))|[^\u4e00-\u9fffA-Za-z0-9]+"
)


def _split_on_particles(phrase: str) -> list[str]:
    """在虚词与标点处断开, 保留 2-10 字的实义片段。"""
    return [p for p in _PARTICLE_SPLIT.split(phrase) if 2 <= len(p) <= 10]


def _tokenize_for_matching(phrase: str) -> list[str]:
    """将长短语切分为适合 content.contains 匹配的短词。: 短语(<=6字)保留整词; 更长的先按语言边界切分保留实义片段,
 切不出片段时(无虚词的长专有名词, 如"质量管理体系认证证书")保留整词——
 contains 对长词同样有效, 整体丢弃才是召回单点。
 """
    if len(phrase) <= 6:
        return [phrase]
    parts = _split_on_particles(phrase)
    return parts if parts else [phrase]


_THEME_SYNONYMS: dict[str, list[str]] = {
    "失信": ["失信被执行人", "信用中国", "信用记录", "未被列入"],
    "信用": ["信用中国", "失信被执行人", "信用记录"],
    "重大违法": ["重大违法记录", "违法失信", "黑名单", "不良行为"],
    "黑名单": ["重大违法", "不良行为", "失信被执行人"],
    "不良行为": ["不良行为记录", "黑名单", "重大违法"],
    "法定代表人": ["法人代表", "单位负责人", "授权代表"],
    "授权委托": ["授权委托书", "委托代理人", "法定代表人证明"],
    "委托代理人": ["授权委托书", "授权代表", "法定代表人"],
    "纳税证明": ["完税证明", "纳税", "税务"],
    "资信证明": ["资信", "银行资信", "资信等级"],
    "审计报告": ["审计", "财务审计", "年度审计"],
    "财务报表": ["财务", "资产负债表", "利润表"],
}

_FORM_TITLE_SUFFIXES = ("情况表", "汇总表", "明细表", "清单", "一览表", "统计表", "表")
_PARTICLE_CHARS = "的"


def _normalize_form_title(text: str) -> str:
    """归一化短表名,用于生成稳定检索锚点。"""
    return re.sub(r"\s+", "", text).translate(str.maketrans("", "", _PARTICLE_CHARS))


def _looks_like_form_title(description: str) -> bool:
    """判断是否是短表名/清单类要求。"""
    compact = _normalize_form_title(description)
    return 4 <= len(compact) <= 40 and any(suffix in compact for suffix in _FORM_TITLE_SUFFIXES)


def _extract_form_title_terms(description: str) -> list[str]:
    """提取表名类要求的短锚点。

    这类要求常以完整表名出现,语义检索和目录匹配容易被助词差异稀释。
    """
    if not _looks_like_form_title(description):
        return []

    compact = _normalize_form_title(description)
    terms: list[str] = []

    if compact != description and compact:
        terms.append(compact)

    # 按连接词切出主题面,如"正在施工"、"新承接"。
    for part in re.split(r"[和及与、，,]+", compact):
        part = part.strip()
        if 2 <= len(part) <= 12:
            for suffix in _FORM_TITLE_SUFFIXES:
                if part.endswith(suffix) and len(part) > len(suffix) + 1:
                    part = part[: -len(suffix)]
                    break
            if 2 <= len(part) <= 12:
                terms.append(part)
            if part.endswith("项目") and len(part) > 4:
                terms.append(part[:-2])

    # 保留后缀短表名,如"项目情况表"、"情况表"。
    for suffix in _FORM_TITLE_SUFFIXES:
        if suffix not in compact:
            continue
        if len(suffix) >= 2:
            terms.append(suffix)
        project_suffix = f"项目{suffix}"
        if project_suffix in compact:
            terms.append(project_suffix)
        prefix = compact[: compact.rfind(suffix)]
        if prefix:
            tail = re.split(r"[和及与、，,]+", prefix)[-1].strip()
            if tail:
                terms.append(f"{tail}{suffix}")

    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        if term and term not in seen and len(term) >= 2:
            seen.add(term)
            result.append(term)
    return result[:8]


def _expand_theme_synonyms(description: str) -> list[str]:
    """根据主题同义词表生成额外检索短语。

    如描述中命中了某个主题触发词，则把该触发词的同义扩展词追加为
    额外子查询短语，帮助向量检索覆盖表述变体。
    """
    extras: list[str] = []
    for trigger, synonyms in _THEME_SYNONYMS.items():
        if trigger in description:
            for syn in synonyms:
                if syn not in description:
                    extras.append(f"{description[:30]} {syn}")
    return extras[:8]


class QueryDecomposer:
    """复合招标要求分解器

    示例:
        >>> decomposer = QueryDecomposer()
        >>> queries = decomposer.decompose(
        ...     "项目经理、技术负责人、安全负责人均需提供身份证和社保缴费证明"
        ... )
        >>> # ['项目经理 身份证', '项目经理 社保缴费证明',
        >>> #  '技术负责人 身份证', '技术负责人 社保缴费证明',
        >>> #  '安全负责人 身份证', '安全负责人 社保缴费证明']
    """

    def decompose(self, description: str) -> list[str]:
        """将复合要求分解为子查询列表

        Args:
            description: 招标要求原文

        Returns:
            list[str]: 子查询列表,至少包含原始描述
        """
        subjects = _extract_subjects(description)
        objects = _extract_objects(description)

        # 主体 x 客体 笛卡尔积
        if subjects and objects:
            product = [f"{s} {o}" for s in subjects for o in objects]
            if len(product) <= MAX_SUB_QUERIES:
                logger.info(
                    f"要求分解: {len(subjects)}主体 x {len(objects)}客体 "
                    f"= {len(product)}个子查询"
                )
                return self._append_theme_expansions(product, description)

        # 仅有并列客体
        if objects and len(objects) > 1:
            base = description[:30]
            queries = [f"{base} {o}" for o in objects]
            logger.info(f"要求分解(仅客体): {len(queries)}个子查询")
            return self._append_theme_expansions(queries, description)

        # 仅有并列主体
        if subjects and len(subjects) > 1:
            # 用整条要求 + 每个主体做子查询
            queries = [f"{s} {description[len(''.join(subjects)):]}" for s in subjects]
            cleaned = [q.strip() for q in queries if len(q.strip()) >= 4]
            if cleaned:
                logger.info(f"要求分解(仅主体): {len(cleaned)}个子查询")
                return self._append_theme_expansions(cleaned, description)

        base = [description]
        base.extend(term for term in _extract_form_title_terms(description) if term not in base)
        return self._append_theme_expansions(base, description)

    @staticmethod
    def _append_theme_expansions(
        queries: list[str], description: str
    ) -> list[str]:
        """在子查询列表后追加主题同义扩展，总量不超过上限。"""
        extras = _expand_theme_synonyms(description)
        if not extras:
            return queries
        seen = set(queries)
        for ex in extras:
            if ex not in seen and len(queries) < MAX_SUB_QUERIES:
                queries.append(ex)
                seen.add(ex)
        if extras:
            logger.info(f"主题同义扩展: +{len(queries) - len(seen) + len(extras)}个子查询")
        return queries

    def extract_keywords(self, description: str) -> list[str]:
        """从要求描述中提取关键词,用于图片证据全文搜索: 泛化主体(投标人/甲方等)无区分度,必须剔除;
 剔除后为空时强制走名词降级提取(此前该路径被泛化主体短路)。
 长客体短语切分为可字面匹配的短词。

 Args:
 description: 招标要求原文

 Returns:
 list[str]: 关键词列表
 """
        raw_terms: list[str] = []
        raw_terms.extend(_extract_subjects(description))
        raw_terms.extend(_extract_objects(description))
        raw_terms.extend(_extract_form_title_terms(description))

        keywords: list[str] = []
        for term in raw_terms:
            if term in GENERIC_SUBJECT_TERMS:
                continue
            keywords.extend(
                t for t in _tokenize_for_matching(term)
                if t not in GENERIC_SUBJECT_TERMS
            )

        if not keywords:
            # 降级: 按语言边界切分名词性短语(: 废除 2-6 字贪婪切片,
            # 该切片把"结算发票为依据"切成"结算发票为"跨词碎片)。
            stop_words = {
                "招标", "投标", "要求", "提供", "提交",
                "出具", "具备", "须", "需", "应", "必须", "的",
                "和", "及", "与", "等", "或者", "以及",
            } | GENERIC_SUBJECT_TERMS
            tokens = _split_on_particles(description)
            keywords = [t for t in tokens if t not in stop_words]

        for trigger, synonyms in _THEME_SYNONYMS.items():
            if trigger in description:
                keywords.extend(syn for syn in synonyms if syn not in keywords)

        # 去重保序
        seen: set[str] = set()
        result: list[str] = []
        for kw in keywords:
            if kw not in seen:
                seen.add(kw)
                result.append(kw)
        return result[:20]


# 模块级单例
_decomposer: QueryDecomposer | None = None


def get_query_decomposer() -> QueryDecomposer:
    """获取全局分解器实例"""
    global _decomposer
    if _decomposer is None:
        _decomposer = QueryDecomposer()
    return _decomposer
