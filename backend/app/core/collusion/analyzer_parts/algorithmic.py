"""串标分析器算法兜底域纯函数

Task 8 Step 3：从 analyzer.py 迁出算法兜底（无 LLM）的 4 个纯函数 + 1 个常量。
主 analyzer.py 顶部 re-export 保持外部 import 路径零变化。

收纳范围：
- _algorithm_compare：低相似度对的 D1/D2/D4 纯算法对比（269 行）
- _algorithm_d4_score：D4 报价异常三规则（精确比例/等差/尾数）
- _META_CHECKS：PDF 元数据字段表
- _GENERIC_PDF_TOOL_BLACKLIST：通用 PDF 工具名黑名单（P1-3）
- _inject_d8_from_metadata：从元数据算法计算 D8
- _is_close_pdf_datetime：PDF 日期接近判定

设计原则：
- 全部为纯函数，无 self 依赖
- 依赖项：is_valid_person_name + 8 个 normalizers + parse_price（函数体内 lazy import）
"""

from __future__ import annotations

from app.core.collusion.normalizers import (
    address_building_kind,
    credit_code_kind,
    nearby_phone_kind,
    normalize_cert_code,
    normalize_company_name,
    normalize_credit_code,
    normalize_id_tail,
)
from app.schemas.collusion import is_valid_person_name

# P1-3: 通用 PDF 生成工具关键词黑名单（大小写不敏感子串匹配）。
# 命中后：
# - _inject_d8_from_metadata 中 creator_tool / producer 字段的 weight 折扣到 0.3
# - global_features._cluster_metadata 中相同 creator/producer 值跳过聚类
# 设计取舍：不彻底丢弃信号（保留极弱权重供报告参考），但避免"全员都是 Microsoft Word"
# 的情况错误升级 D8 finding 数与展示分数。
#
# 关键词必须是完整品牌词/产品名（如 "microsoft" / "acrobat" / "phantompdf"）,
# **禁止**收纳裸 "pdf" / "doc" 等通用类别词，否则会误伤"专用PDF引擎"
# 等中文产品名（含"PDF"字样但属于真实差异化工具）。
_GENERIC_PDF_TOOL_BLACKLIST: frozenset[str] = frozenset({
    "microsoft", "word", "office", "powerpoint", "excel",
    "adobe", "acrobat", "distiller", "indesign",
    "itext", "pdfkit", "reportlab",
    "wps", "kingsoft",
    "foxit", "phantompdf",
    "aspose", "pdfsharp", "ghostscript",
})


def _is_generic_pdf_tool(value: str | None) -> bool:
    """判断 PDF 元数据值（creator_tool / producer 等）是否为通用工具名。

    大小写不敏感的子串匹配；空值返回 False。
    """
    if not value:
        return False
    text = str(value).strip().lower()
    if not text:
        return False
    return any(keyword in text for keyword in _GENERIC_PDF_TOOL_BLACKLIST)


def _algorithm_compare(
    info_a: dict, info_b: dict,
    name_a: str = "文档1", name_b: str = "文档2",
) -> dict:
    """纯算法对比（低相似度对，D1/D2维度）

    不调用 LLM，仅比较结构化提取的人员和企业信息。
    输出结构化 findings 格式（与 LLM 输出对齐）。
    """
    d1_score = 0.0
    d1_findings: list[dict] = []
    d2_score = 0.0
    d2_findings: list[dict] = []

    # D1: 人员重叠
    personnel_a = info_a.get("personnel_list") or []
    personnel_b = info_b.get("personnel_list") or []

    matched_people: set[tuple[str, str]] = set()
    for pa in personnel_a:
        if not isinstance(pa, dict):
            continue
        name_a_person = (pa.get("name") or "").strip()
        if not name_a_person or not is_valid_person_name(name_a_person):
            continue
        certs_a = {
            normalize_cert_code(cert) for cert in (pa.get("certificate_numbers") or [])
            if normalize_cert_code(cert)
        }
        id_tail_a = normalize_id_tail(pa.get("id_number"))
        for pb in personnel_b:
            if not isinstance(pb, dict):
                continue
            name_b_person = (pb.get("name") or "").strip()
            if not name_b_person or not is_valid_person_name(name_b_person):
                continue
            certs_b = {
                normalize_cert_code(cert) for cert in (pb.get("certificate_numbers") or [])
                if normalize_cert_code(cert)
            }
            id_tail_b = normalize_id_tail(pb.get("id_number"))
            role_a = pa.get("role", "未知")
            role_b = pb.get("role", "未知")
            page_a = f"第{pa['source_page']}页" if pa.get("source_page") else None
            page_b = f"第{pb['source_page']}页" if pb.get("source_page") else None

            if certs_a and certs_b and (certs_a & certs_b):
                shared = ", ".join(sorted(certs_a & certs_b))
                key = ("cert", shared)
                if key not in matched_people:
                    matched_people.add(key)
                    d1_score = max(d1_score, 1.0)
                    d1_findings.append({
                        "summary": f"关键人员证书编号重叠：{shared}",
                        "detail": f"{name_a}与{name_b}存在相同证书编号 {shared}，属于高强度人员重叠信号。",
                        "quote_a": f"{name_a_person}，{role_a}",
                        "page_a": page_a,
                        "quote_b": f"{name_b_person}，{role_b}",
                        "page_b": page_b,
                        "similarity": None,
                    })
                continue

            if id_tail_a and id_tail_b and id_tail_a == id_tail_b and name_a_person == name_b_person:
                key = ("id", f"{name_a_person}|{id_tail_a}")
                if key not in matched_people:
                    matched_people.add(key)
                    d1_score = max(d1_score, 0.95)
                    d1_findings.append({
                        "summary": f"关键人员身份证尾号重叠：{name_a_person}",
                        "detail": f"{name_a_person} 在两份文件中的身份证尾号同为 {id_tail_a}，存在较强同人重叠信号。",
                        "quote_a": f"{name_a_person}，{role_a}",
                        "page_a": page_a,
                        "quote_b": f"{name_b_person}，{role_b}",
                        "page_b": page_b,
                        "similarity": None,
                    })
                continue

            if name_a_person == name_b_person:
                key = ("name", name_a_person)
                if key not in matched_people:
                    matched_people.add(key)
                    d1_score = max(d1_score, 0.6)
                    d1_findings.append({
                        "summary": f"关键人员重叠：{name_a_person}",
                        "detail": f"同一自然人'{name_a_person}'同时出现在两份投标文件中。{name_a}中担任{role_a}，{name_b}中担任{role_b}。",
                        "quote_a": f"{name_a_person}，{role_a}",
                        "page_a": page_a,
                        "quote_b": f"{name_b_person}，{role_b}",
                        "page_b": page_b,
                        "similarity": None,
                    })

    # D2: 企业关联
    comp_a = info_a.get("company_info") or {}
    comp_b = info_b.get("company_info") or {}

    if isinstance(comp_a, dict) and isinstance(comp_b, dict):
        page_a_comp = f"第{comp_a['source_page']}页" if comp_a.get("source_page") else None
        page_b_comp = f"第{comp_b['source_page']}页" if comp_b.get("source_page") else None

        lr_a = (comp_a.get("legal_representative") or "").strip()
        lr_b = (comp_b.get("legal_representative") or "").strip()
        if (
            lr_a
            and lr_b
            and lr_a == lr_b
            and is_valid_person_name(lr_a)
            and is_valid_person_name(lr_b)
        ):
            d2_score = 1.0
            d2_findings.append({
                "summary": f"法定代表人相同：{lr_a}",
                "detail": (
                    f"{name_a}和{name_b}的法定代表人分别为'{lr_a}'与'{lr_b}'，"
                    "构成企业实质关联的直接证据。"
                ),
                "quote_a": f"法定代表人：{lr_a}",
                "page_a": page_a_comp,
                "quote_b": f"法定代表人：{lr_b}",
                "page_b": page_b_comp,
                "similarity": None,
            })

        # D2: 按精确情形生成精确叙述（消除"或"字硬编码）
        credit_kind = credit_code_kind(
            comp_a.get("unified_social_credit_code"),
            comp_b.get("unified_social_credit_code"),
        )
        if credit_kind == "exact":
            credit_norm = normalize_credit_code(
                comp_a.get("unified_social_credit_code")
            )
            d2_score = max(d2_score, 1.0)
            d2_findings.append({
                "summary": f"统一社会信用代码完全一致：{credit_norm}",
                "detail": (
                    f"{name_a}与{name_b}的统一社会信用代码完全相同（{credit_norm}），"
                    "构成企业实质关联的直接证据。"
                ),
                "quote_a": f"统一社会信用代码：{comp_a.get('unified_social_credit_code')}",
                "page_a": page_a_comp,
                "quote_b": f"统一社会信用代码：{comp_b.get('unified_social_credit_code')}",
                "page_b": page_b_comp,
                "similarity": None,
            })
        elif credit_kind == "prefix":
            credit_a_norm = normalize_credit_code(
                comp_a.get("unified_social_credit_code")
            )
            credit_b_norm = normalize_credit_code(
                comp_b.get("unified_social_credit_code")
            )
            d2_score = max(d2_score, 0.8)
            d2_findings.append({
                "summary": f"统一社会信用代码登记前缀一致（前 8 位 {credit_a_norm[:8]}）",
                "detail": (
                    f"{name_a}与{name_b}的统一社会信用代码前 8 位相同"
                    f"（{credit_a_norm[:8]} == {credit_b_norm[:8]}），"
                    "登记机关与主管部门一致，可能存在企业关联。"
                ),
                "quote_a": f"统一社会信用代码：{comp_a.get('unified_social_credit_code')}",
                "page_a": page_a_comp,
                "quote_b": f"统一社会信用代码：{comp_b.get('unified_social_credit_code')}",
                "page_b": page_b_comp,
                "similarity": None,
            })

        # D2: 联系电话精确情形
        phone_kind = nearby_phone_kind(
            comp_a.get("contact_phone"),
            comp_b.get("contact_phone"),
        )
        if phone_kind == "exact":
            d2_score = max(d2_score, 0.9)
            d2_findings.append({
                "summary": f"联系电话完全相同：{comp_a.get('contact_phone')}",
                "detail": (
                    f"{name_a}和{name_b}登记的联系电话完全相同"
                    f"（{comp_a.get('contact_phone')}），属于企业关联异常信号。"
                ),
                "quote_a": f"联系电话：{comp_a.get('contact_phone')}",
                "page_a": page_a_comp,
                "quote_b": f"联系电话：{comp_b.get('contact_phone')}",
                "page_b": page_b_comp,
                "similarity": None,
            })
        elif phone_kind == "fuzzy":
            d2_score = max(d2_score, 0.6)
            d2_findings.append({
                "summary": (
                    f"联系电话号段相邻：{comp_a.get('contact_phone')} / "
                    f"{comp_b.get('contact_phone')}"
                ),
                "detail": (
                    f"{name_a}和{name_b}登记的联系电话前 10 位相同、末位差 ≤2"
                    f"（{comp_a.get('contact_phone')} / {comp_b.get('contact_phone')}），"
                    "号段连号属于企业关联异常信号。"
                ),
                "quote_a": f"联系电话：{comp_a.get('contact_phone')}",
                "page_a": page_a_comp,
                "quote_b": f"联系电话：{comp_b.get('contact_phone')}",
                "page_b": page_b_comp,
                "similarity": None,
            })

        # D2: 注册地址精确情形
        addr_kind = address_building_kind(
            comp_a.get("registered_address"),
            comp_b.get("registered_address"),
        )
        if addr_kind == "exact":
            d2_score = max(d2_score, 0.6)
            d2_findings.append({
                "summary": "注册地址归一化后完全相同",
                "detail": (
                    f"{name_a}和{name_b}的注册地址归一化后指向同一地址主体"
                    f"（{comp_a.get('registered_address')}），可能存在企业关联。"
                ),
                "quote_a": f"注册地址：{comp_a.get('registered_address')}",
                "page_a": page_a_comp,
                "quote_b": f"注册地址：{comp_b.get('registered_address')}",
                "page_b": page_b_comp,
                "similarity": None,
            })

        if (
            normalize_company_name(comp_a.get("company_name"))
            and normalize_company_name(comp_a.get("company_name")) == normalize_company_name(comp_b.get("company_name"))
        ):
            d2_score = max(d2_score, 0.5)
            d2_findings.append({
                "summary": "企业名称核心词高度一致",
                "detail": f"{name_a}和{name_b}去除企业后缀后的核心名称高度一致，需结合其他证据进一步核查。",
                "quote_a": f"企业名称：{comp_a.get('company_name')}",
                "page_a": page_a_comp,
                "quote_b": f"企业名称：{comp_b.get('company_name')}",
                "page_b": page_b_comp,
                "similarity": None,
            })

    # P1-1: D4 报价异常算法兜底。在低相似度路径补充"精确比例 / 等差 / 尾数一致"
    # 三条硬规则，仅在两侧 total_price 都能解析为正数时发声，否则 score=0 不污染。
    d4_score, d4_findings = _algorithm_d4_score(info_a, info_b, name_a, name_b)

    overall = "none"
    if d1_score >= 0.5 or d2_score >= 0.7:
        overall = "high"
    elif d1_score > 0 or d2_score > 0 or d4_score >= 0.7:
        overall = "medium"
    elif d4_score > 0:
        overall = "low"

    d4_payload = (
        {"score": d4_score, "findings": d4_findings, "evidence_level": "second", "certainty": 0.75}
        if d4_score > 0
        else None
    )

    return {
        "overall_risk": overall,
        "d1_personnel": {"score": d1_score, "findings": d1_findings, "evidence_level": "first", "certainty": 0.9},
        "d2_company": {"score": d2_score, "findings": d2_findings, "evidence_level": "first", "certainty": 0.9},
        "d3_text": None,
        "d4_price": d4_payload,
        "d5_qualification": None,
        "d6_format": None,
        "d7_abnormal": None,
        "d8_metadata": None,
        "key_findings": d1_findings + d2_findings + (d4_findings if d4_findings else []),
        "risk_explanation": (
            "两份文件内容差异较大，串通风险较低，核查维度：人员重叠(D1)、企业关联(D2)"
            + ("、报价异常(D4)" if d4_score > 0 else "")
        ),
    }


def _algorithm_d4_score(
    info_a: dict, info_b: dict, name_a: str, name_b: str,
) -> tuple[float, list[dict]]:
    """P1-1: D4 报价异常算法兜底。

    返回 (score, findings)。三条硬规则：
      - 尾数完全一致（末两位）：score 0.5
      - 精确比例（0.85–0.99）：score 0.8（典型陪标特征）
      - 完全相等（极端可疑）：score 1.0
    仅当两侧 total_price 都能解析为正数时生效。
    """
    from app.core.collusion.global_features import parse_price

    price_info_a = info_a.get("price_info") or {}
    price_info_b = info_b.get("price_info") or {}
    if not isinstance(price_info_a, dict) or not isinstance(price_info_b, dict):
        return 0.0, []

    pa = parse_price(price_info_a.get("total_price"))
    pb = parse_price(price_info_b.get("total_price"))
    if pa is None or pb is None or pa <= 0 or pb <= 0:
        return 0.0, []

    findings: list[dict] = []
    score = 0.0

    # 完全相等
    if abs(pa - pb) < 1e-6:
        score = max(score, 1.0)
        findings.append({
            "summary": f"报价完全相同：{pa:.2f}",
            "detail": f"{name_a}与{name_b}的投标总价完全相等，属于极端可疑的报价关联信号。",
            "quote_a": f"总价：{price_info_a.get('total_price')}",
            "page_a": None,
            "quote_b": f"总价：{price_info_b.get('total_price')}",
            "page_b": None,
            "similarity": 1.0,
        })
    else:
        # 精确比例
        lo, hi = min(pa, pb), max(pa, pb)
        ratio = lo / hi
        if 0.85 <= ratio < 1.0:
            score = max(score, 0.8)
            findings.append({
                "summary": f"报价呈精确比例：{ratio:.3f}",
                "detail": (
                    f"{name_a}报价 {pa:.2f}，{name_b}报价 {pb:.2f}，"
                    f"比例 {ratio:.3f} 落在陪标典型区间 [0.85, 1.0)，需重点核查。"
                ),
                "quote_a": f"总价：{price_info_a.get('total_price')}",
                "page_a": None,
                "quote_b": f"总价：{price_info_b.get('total_price')}",
                "page_b": None,
                "similarity": None,
            })

    # 尾数一致（末两位）——尾数为 00 的整数报价极其常见，不具备可疑性，排除避免误报
    tail = int(round(pa)) % 100
    if tail != 0 and tail == int(round(pb)) % 100 and abs(pa - pb) >= 1.0:
        score = max(score, 0.5)
        findings.append({
            "summary": f"报价末两位尾数一致：{tail:02d}",
            "detail": (
                f"{name_a}总价 {pa:.2f} 与 {name_b}总价 {pb:.2f} 的末两位均为 "
                f"{tail:02d}，报价习惯高度雷同。"
            ),
            "quote_a": f"总价：{price_info_a.get('total_price')}",
            "page_a": None,
            "quote_b": f"总价：{price_info_b.get('total_price')}",
            "page_b": None,
            "similarity": None,
        })

    return score, findings


# P1-3: producer 权重从 0.2 降到 0.1; 配合 _GENERIC_PDF_TOOL_BLACKLIST 折扣,
# 避免"Microsoft Word / Adobe Acrobat 等通用工具值在多家投标中全员一致"造成
# 的 D8 误报。creator_tool 权重保持 0.35 但同样会受工具名黑名单折扣。
_META_CHECKS: list[tuple[str, float, str, str, str]] = [
    ("creator_tool", 0.35, "PDF生成工具", "Creator",
     "两份PDF文件的创建工具字段完全一致（{val}），暗示可能使用同一软件环境生成。"),
    ("author", 0.25, "PDF作者字段", "Author",
     "两份PDF文件的Author元数据均为'{val}'，暗示可能由同一人或同一台电脑创建。"),
    ("producer", 0.1, "PDF生成器", "Producer",
     "两份PDF文件的Producer字段一致（{val}），暗示生成软件与环境高度一致。"),
    ("keywords", 0.1, "PDF关键词", "Keywords",
     "两份PDF文件的Keywords字段一致（{val}），属于元数据层面的辅助一致性信号。"),
    ("subject", 0.1, "PDF主题", "Subject",
     "两份PDF文件的Subject字段一致（{val}），属于元数据层面的辅助一致性信号。"),
]

# P1-3: 命中工具名黑名单时, creator_tool / producer 的 weight 折扣系数。
# 不直接置零, 保留极弱信号供报告体现"双方都用 Word 制作"。
_GENERIC_PDF_TOOL_DISCOUNT: float = 0.3
_GENERIC_PDF_TOOL_FIELDS: frozenset[str] = frozenset({"creator_tool", "producer"})


def build_skipped_pair_baseline(
    info_a: dict,
    info_b: dict,
    name_a: str = "文档1",
    name_b: str = "文档2",
) -> dict | None:
    """C1: 为智能筛对跳过的文件对产出零成本算法基线对比结果。

    职责: 复用 _algorithm_compare + _inject_d8_from_metadata 对一对未进入
        LLM 深度对比的低相似文件做 D1/D2/D4/D8 结构化字段比对，命中任一维度
        时返回带 findings 的结果；用于补齐"跳过对完全不分析、一级证据存在
        整类漏检面"的防御缺口。
    不做: LLM 调用、向量计算、落库、图聚类、规范化（normalize_compare_result
        由调用方在落库前执行以补齐 pair_id/pair_ref 与 evidences 结构）。

    Args:
        info_a/info_b: CollusionExtraction 转 dict 后的结构化提取字段，
            缺失字段时各维度自然返回 score=0；任一侧为空 dict 视为提取失败。
        name_a/name_b: 用于 finding detail 的文档显示名。

    Returns:
        无任一维度命中（D1/D2/D4/D8 score 全部为 0）时返回 None，调用方应
        保留原 inconclusive 占位 + 新 quality_flag，禁止把"未命中"标记为
        "无风险"（遵循 Golden Rule 4：宁漏报三级、不错判一级）。
    """
    if not isinstance(info_a, dict) or not isinstance(info_b, dict):
        return None
    if not info_a or not info_b:
        return None

    result = _algorithm_compare(info_a, info_b, name_a, name_b)
    _inject_d8_from_metadata(result, info_a, info_b)

    def _has_hits(dim: dict | None) -> bool:
        if not isinstance(dim, dict):
            return False
        score = dim.get("score") or 0
        findings = dim.get("findings") or []
        return bool(score and score > 0) or bool(findings)

    has_signal = any(
        _has_hits(result.get(key))
        for key in ("d1_personnel", "d2_company", "d4_price", "d8_metadata")
    )
    return result if has_signal else None


def _inject_d8_from_metadata(compare_result: dict, info_a: dict, info_b: dict) -> None:
    """从结构化提取的元数据中算法计算 D8 维度

    P1-3 行为变更：
    - creator_tool / producer 字段命中 _GENERIC_PDF_TOOL_BLACKLIST 时,
      该项 weight 乘 _GENERIC_PDF_TOOL_DISCOUNT (0.3) 后再累加;
    - 同时在 detail 末尾追加"通用 PDF 工具，弱信号"标注, 便于前端识别。
    """
    meta_a = info_a.get("metadata_info") or {}
    meta_b = info_b.get("metadata_info") or {}
    if not isinstance(meta_a, dict) or not isinstance(meta_b, dict):
        return

    score = 0.0
    findings: list[dict] = []
    for field, weight, label, tag, detail_tpl in _META_CHECKS:
        val_a = meta_a.get(field)
        val_b = meta_b.get(field)
        if val_a and val_b and val_a == val_b:
            applied_weight = weight
            tool_note = ""
            if field in _GENERIC_PDF_TOOL_FIELDS and _is_generic_pdf_tool(str(val_a)):
                applied_weight = weight * _GENERIC_PDF_TOOL_DISCOUNT
                tool_note = "（命中通用 PDF 工具白名单，已折扣为弱信号）"
            score += applied_weight
            findings.append({
                "summary": f"{label}相同：{val_a}{tool_note}",
                "detail": detail_tpl.format(val=val_a) + tool_note,
                "quote_a": f"{tag}: {val_a}",
                "page_a": "PDF元数据",
                "quote_b": f"{tag}: {val_b}",
                "page_b": "PDF元数据",
                "similarity": None,
            })

    for field, tag, label in (
        ("creation_date", "CreationDate", "创建时间"),
        ("mod_date", "ModDate", "修改时间"),
    ):
        val_a = meta_a.get(field)
        val_b = meta_b.get(field)
        if val_a and val_b and _is_close_pdf_datetime(str(val_a), str(val_b)):
            score += 0.1
            findings.append({
                "summary": f"{label}接近",
                "detail": f"两份PDF文件的{label}非常接近（{val_a} / {val_b}），属于元数据层面的辅助一致性信号。",
                "quote_a": f"{tag}: {val_a}",
                "page_a": "PDF元数据",
                "quote_b": f"{tag}: {val_b}",
                "page_b": "PDF元数据",
                "similarity": None,
            })

    if score > 0:
        dynamic_certainty = min(0.3 + len(findings) * 0.12, 0.85)
        compare_result["d8_metadata"] = {
            "score": min(score, 1.0),
            "findings": findings,
            "evidence_level": "third",
            "certainty": round(dynamic_certainty, 2),
        }


def _is_close_pdf_datetime(left: str, right: str) -> bool:
    """判断两个 PDF 日期字段是否足够接近。"""
    def _normalize(value: str) -> str:
        return (
            value.replace("D:", "")
            .replace("'", "")
            .replace("+08:00", "")
            .replace("Z", "")
            .strip()
        )

    left_norm = _normalize(left)
    right_norm = _normalize(right)
    if not left_norm or not right_norm:
        return False
    # 精确到分钟或至少前 10 位一致（同日同批次）
    return left_norm[:12] == right_norm[:12] or left_norm[:10] == right_norm[:10]
