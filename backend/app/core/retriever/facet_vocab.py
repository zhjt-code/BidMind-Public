"""子面标签证据触发词表

职责: 维护"要求子面标签 -> 证据形态触发词"的闭集映射, 并提供标签翻译
不做: 检索、分面命中判定、复合要求拆分、LLM 调用、ORM 持久化

背景: 该触发词表原为 tender_extractor 的 `_COMPOUND_SPLIT_RULES` 私有
ClassVar, 只服务于招标要求复合拆分。 发现检索侧的
`build_evidence_facets` 拿到的 `sub_requirements` 常是这些 ASCII 标签
(如 "enterprise_perf")本身, 原样当分面查询无任何字面区分度。把词表抽为
共享叶子模块后, 检索侧可将标签翻译回中文触发词(如 perf_evidence ->
合同扫描/验收报告/竣工/发票/...), 与提取侧共用同一份证据形态词汇。
"""

from __future__ import annotations

# 分类 -> 子面标签 -> 该子面的证据形态触发词(闭集)。
# 迁移自 tender_extractor._COMPOUND_SPLIT_RULES, 内容保持字面一致。
FACET_TRIGGER_WORDS: dict[str, dict[str, list[str]]] = {
    "STAFF": {
        "personnel_cert": ["证书", "职称", "资质", "执照", "学历", "建造师"],
        "personnel_social": ["社保", "缴费", "劳动合同", "身份证"],
        "personnel_perf": ["业绩", "项目经验", "类似项目", "施工经验", "合同金额"],
        "personnel_aux": ["培训", "体检", "保险", "健康证明"],
        "personnel_mgmt": ["HSE", "安全管理", "组织架构", "特种作业", "持证上岗"],
    },
    "QUAL": {
        "enterprise_qual": ["营业执照", "资质等级", "ISO", "安全生产许可"],
        "personnel_cert_in_qual": ["项目经理", "建造师", "安全员", "注册"],
        "qual_system": ["质量管理体系", "环境管理", "职业健康", "体系认证"],
        "qual_financial": ["财务报表", "审计报告", "纳税证明", "资信证明"],
        "qual_credit": ["失信", "信用", "重大违法", "黑名单", "不良行为"],
        "qual_authorization": ["法定代表人", "授权委托", "委托代理人", "授权代表"],
    },
    "PERF": {
        "enterprise_perf": ["类似项目", "项目业绩", "施工业绩", "中标通知", "合同金额"],
        "personnel_perf_in_perf": ["人员业绩", "个人业绩", "项目经理业绩", "负责人业绩", "任职证明"],
        "perf_evidence": ["合同扫描", "验收报告", "竣工", "发票", "业绩证明材料", "付款凭证"],
    },
    "COMM": {
        "comm_bond": ["保证金", "担保", "银行保函", "履约保证"],
        "comm_price": ["报价", "价格", "下浮", "降点", "税率", "最高限价"],
        "comm_payment": ["付款", "结算", "预付款", "进度款", "质保金"],
        "comm_delivery": ["工期", "交付", "竣工日期", "完工", "进度计划"],
        "comm_contract": ["违约", "索赔", "保修", "质保期", "合同条款"],
    },
}

# 子面标签 -> 主体范围(personnel/enterprise/both/generic)。
# 迁移自 tender_extractor._FACET_SCOPE_MAP, 内容保持字面一致。
FACET_SUBJECT_SCOPE: dict[str, str] = {
    # STAFF
    "personnel_cert": "personnel",
    "personnel_social": "personnel",
    "personnel_perf": "personnel",
    "personnel_aux": "personnel",
    "personnel_mgmt": "personnel",
    # QUAL
    "enterprise_qual": "enterprise",
    "personnel_cert_in_qual": "personnel",
    "qual_system": "enterprise",
    "qual_financial": "enterprise",
    "qual_credit": "both",
    "qual_authorization": "personnel",
    # PERF
    "enterprise_perf": "enterprise",
    "personnel_perf_in_perf": "personnel",
    "perf_evidence": "generic",
    # COMM
    "comm_bond": "generic",
    "comm_price": "generic",
    "comm_payment": "generic",
    "comm_delivery": "generic",
    "comm_contract": "generic",
}

# 扁平映射: 子面标签 -> 触发词(标签在全分类下唯一, 构建期一次成型)。
FACET_LABEL_TO_WORDS: dict[str, tuple[str, ...]] = {
    label: tuple(words)
    for facets in FACET_TRIGGER_WORDS.values()
    for label, words in facets.items()
}


def translate_facet_label(label: str) -> list[str]:
    """把子面标签翻译为证据触发词; 非已知标签返回空列表。

    检索侧用于把 `sub_requirements` 里的 ASCII 标签(如 enterprise_perf)
    还原为可字面命中的中文触发词, 避免标签本身当无区分度的分面查询。
    """
    return list(FACET_LABEL_TO_WORDS.get((label or "").strip(), ()))
