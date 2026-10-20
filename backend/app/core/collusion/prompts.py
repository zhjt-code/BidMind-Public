"""串标审查 Prompt 模板

包含当前运行时用到的串标审查 Prompt：
  - COLLUSION_SYSTEM_PROMPT             全局角色设定 + 4条黄金规则 + 三级证据体系
  - COLLUSION_TENDER_FORMAT_PROMPT      S1a 招标格式要求提取
  - COLLUSION_EXTRACT_ROUTE_PROMPT      S1b 投标文件五路提取
  - COLLUSION_ENTITY/TEXT_PATTERN/BUSINESS_PROMPT  分组对比
  - COLLUSION_SINGLE_DIM_PROMPT         单维深搜
  - COLLUSION_PAIR_JUDGEMENT_PROMPT     文件对判定
  - COLLUSION_GROUP_ANALYSIS_PROMPT     群组研判
  - COLLUSION_PROJECT_SUMMARY_PROMPT    项目级综合（无群组路径）
  - COLLUSION_OVERALL_SYNTHESIS_PROMPT  项目级综合（有群组路径，含 groups_summary JSON 结构说明）
  - COLLUSION_INDUSTRY_SIGNAL/PROFILE_PROMPT_V2    行业画像

注: 旧的 COLLUSION_EXTRACT_PROMPT / COLLUSION_COMPARE_PROMPT / COLLUSION_SYNTHESIS_PROMPT /
COLLUSION_PRICING_ANALYSIS_PROMPT 已被运行时链路替换, 对应 seed 节点在
seed_llm_registry.DEPRECATED_NODE_KEYS 中 soft-deactivate, 模板已从本文件删除。
"""

# =============================================================================
# 全局系统 Prompt
# =============================================================================

COLLUSION_SYSTEM_PROMPT = """你是一位资深招投标监督审查专家，专门识别投标文件中潜在的串通投标（串标/围标）行为。

## 核心职责
通过多份投标文件的横向交叉对比，发现可能存在的串通行为证据。

## 黄金规则（禁止违反）

### 规则一：正常相似豁免原则（防误判）
以下情况导致的相似性属于正常现象，不得认定为串通证据：
- 招标文件统一要求的格式、模板、固定措辞
- 行业标准表述（ISO标准条文、法规原文、技术规范）
- 法律法规要求的固定内容（营业执照格式、资质证书固定项）
- 公开信息引用（招标文件原文、公开技术参数、行业报价基准）

### 规则二：区分度评估原则（按内容类型设定阈值）
不同内容类型的"异常相似"阈值不同：
- 企业简介/通用介绍：相似度<60%属正常，>=80%才可疑
- 技术方案核心内容：相似度<30%属正常，>=50%即可疑
- 关键人员信息：任何关键人员重叠（同名同证号）均视为异常
- 报价结构：结构相似属正常，但数值呈精确比例/差值则异常

### 规则三：证据链要求（三级证据体系）
- **一级证据**（单独可定性）：人员信息重叠、企业实质关联
- **二级证据**（≥2项交叉验证可定性）：技术方案文本雷同、报价关联、资质业绩雷同
- **三级证据**（辅助参考，不可独立定性）：文档格式一致、异常一致性、元数据相似

### 规则四：审慎判定原则
宁漏报三级证据，不错报一级证据。
一级证据必须有充分、具体、可核实的依据，不得凭推测认定。

## 输出要求
- 所有JSON字段必须如实填写，不确定的字段填null
- 发现的证据必须标注来源（页码/章节/具体原文）
- 风险等级判定必须与证据链规则严格一致
"""

# =============================================================================
# S1a：招标格式要求提取 Prompt（仅当有招标文件时使用）
# =============================================================================

COLLUSION_TENDER_FORMAT_PROMPT = """请从以下招标文件内容中提取统一格式要求，这些要求将作为"正常一致"的白名单，用于区分"招标规定导致的正常相同"和"可疑的串通行为"。

## 招标文件内容
{tender_text}

## 提取目标
提取招标文件中明确规定的、所有投标人必须遵守的统一格式要求，包括：
1. 目录结构要求（章节顺序、必须包含的内容项）
2. 投标函/承诺书固定措辞（要求原文引用的固定语句）
3. 报价表格式（表格结构、行列要求、单位要求）
4. 其他统一格式规定（装订、签章、纸张规格等）

## 输出格式（严格JSON，不输出任何其他文字）
{{
  "toc_requirements": ["章节1: xxx", "章节2: xxx"],
  "fixed_wording": ["投标函中必须包含的原文: xxx", "承诺书措辞: xxx"],
  "price_table_format": "报价表格式描述，如：一列为项目名称，一列为单价，一列为总价",
  "unified_format_rules": ["装订要求: xxx", "签章要求: xxx", "页码要求: xxx"]
}}
"""

# =============================================================================
# S1b：投标文件结构化信息提取 Prompt
# =============================================================================
COLLUSION_EXTRACT_ROUTE_PROMPT = """请从以下投标文件片段中提取与“{focus_label}”相关的结构化信息。

## 文件名称
{doc_name}

## 当前提取范围
- 路由: {route_name}
- 目标字段: {target_fields}
- 说明: {focus_instruction}

## 投标文件片段
{doc_text}

## 约束要求
- 严格依据当前片段提取，不得补充未出现的信息
- 当前路由无关字段必须返回 null 或 []
- 若同一值在图片证据与正文中都出现，优先保留更完整、更带唯一标识的信息
- `source_page` 尽量填写真实页码；没有页码则填 null
- 人员仍需遵守“真实中文姓名 + 明确岗位”的约束

## 输出格式（严格JSON，不输出任何其他文字）
{{
  "company_info": {{
    "company_name": "企业全称",
    "legal_representative": "法定代表人姓名",
    "registered_address": "注册地址",
    "contact_phone": "联系电话（主要电话）",
    "bank_account": "银行账户末4位或脱敏信息",
    "unified_social_credit_code": "统一社会信用代码",
    "source_page": "企业信息首次出现的页码数字，如 1"
  }},
  "personnel_list": [
    {{
      "name": "姓名",
      "role": "岗位/职务",
      "id_number": "身份证号后4位或null",
      "certificate_numbers": ["证书编号1"],
      "source_page": "首次出现的页码数字，如 3"
    }}
  ],
  "price_info": {{
    "total_price": "投标总价（含币种和单位）"
  }},
  "technical_highlights": ["技术方案核心特征1"],
  "qualifications": ["资质证书名称 编号 (第X页)"],
  "past_projects": ["业绩项目名称 合同方 时间 (第X页)"],
  "subcontractors": ["分包商/供应商名称"]
}}
"""


_EXTRACTION_ROUTE_HINTS = {
    "entity": "重点提取企业基本信息、关键人员、身份证尾号、证书编号；如果图片证据中出现同名人员或证书编号，也应纳入。",
    "pricing": "重点提取总价、分项报价、保证金、有效期；无关的企业/技术字段保持为空。",
    "qualification": "重点提取资质证书、类似业绩、合同、分包商或供应商名称；无关字段保持为空。",
    "technical": "仅提取能够体现方案差异性的技术/实施亮点摘要，不要抄录大段通用表述。",
    "evidence": "当前片段主要来自图片或证明材料，请优先提取人员证书、企业证照、业绩合同中的唯一标识信息。",
}


def build_collusion_extract_route_prompt(
    *,
    doc_name: str,
    doc_text: str,
    route_name: str,
    target_fields: list[str],
) -> str:
    """构建按路由聚焦的提取 Prompt (Registry 优先)."""
    from app.core.ai.prompt_registry import resolve_user_template
    focus_label = route_name
    focus_instruction = _EXTRACTION_ROUTE_HINTS.get(
        route_name,
        "仅提取当前路由相关字段，无关字段保持为空。",
    )
    template = resolve_user_template(
        "collusion.route_extract", COLLUSION_EXTRACT_ROUTE_PROMPT
    )
    return template.format(
        doc_name=doc_name,
        doc_text=doc_text,
        route_name=route_name,
        focus_label=focus_label,
        target_fields="、".join(target_fields),
        focus_instruction=focus_instruction,
    )

# =============================================================================
# S3：两文件深度对比 Prompt（8维度分析）
# =============================================================================
# =============================================================================
# S3 分组对比 Prompt（维度分组并行分析）
# =============================================================================

_GROUPED_PROMPT_HEADER = """两份投标文件的向量相似度为 {similarity_pct}%。
相似内容覆盖率: {coverage_pct}%（全文中有多少比例的段落与对方高度相似）
最长连续相似段: {max_continuous_span} 个段落（连续相似段越长，复制粘贴的可能性越大）

## score评分规则(必须遵守)
score反映两份文件在该维度的客观相似/重叠程度(0.0=无任何重叠, 1.0=完全相同),
与是否构成串标证据无关。即便相似源于行业惯例或招标模板,也必须如实给出反映重叠程度的score。
是否构成串标嫌疑通过evidence_level(证据级别)和certainty(确定性)来区分:
- 正常相似(模板/行业标准导致): score如实反映重叠度, evidence_level="third", certainty<0.3
- 可疑相似(个性化内容雷同): score如实反映重叠度, evidence_level按证据体系填写, certainty>=0.5
- 覆盖率很低(<5%)且无连续段: 大概率是散点撞车，certainty应偏低

## findings 输出红线（禁止违反）
某维度若得出"无串标线索"或"已豁免"的结论，必须遵守：
- findings 字段返回空数组 []，certainty<0.3
- 把分析过程、白名单命中、行业惯例豁免、模板导致一致等论证写入该维度同级的"risk_explanation"段落或留空 findings
- 严禁把以下反向定性陈述放入 findings：
  * "两份文件技术方案核心内容不同/无实质性雷同"
  * "已命中白名单不构成串标证据"
  * "属于招标文件模板/行业惯例/法规要求"
  * "未发现关联/未发现雷同/未发现可疑"
  * "差异显著/互不相同"

正确示例：
  d3_text: {{ "score": 0.0, "findings": [], "evidence_level": "third", "certainty": 0.0 }}
  风险说明: "D3 未发现雷同：高相似段落已被白名单（招标模板）覆盖。"

错误示例（禁止）：
  d3_text: {{ "findings": [{{ "summary": "技术方案核心内容不同，无实质性雷同" }}] }}

## 输出要求
- 每条finding必须包含summary/detail/quote_a/page_a/quote_b/page_b
- 使用真实文件名({doc_a_name}/{doc_b_name})和doc_id,禁止"文件A/B"占位
- 证据必须标注来源页码和原文
- 所有字符串字段(summary/detail/risk_explanation)使用自然语言,禁止Markdown语法(#标题/-列表/**粗体)

{tender_context}
"""

COLLUSION_ENTITY_PROMPT = _GROUPED_PROMPT_HEADER + """
## 分析任务: D1人员重叠 + D2企业关联

### 文档1: {doc_a_name} (doc_id={doc_a_id})
**结构化提取**:
{structured_a}

**文档原文(定向检索)**:
{context_a}

### 文档2: {doc_b_name} (doc_id={doc_b_id})
**结构化提取**:
{structured_b}

**文档原文(定向检索)**:
{context_b}

### D1人员重叠(一级证据)
对比两份文件的关键人员名单、身份证号、资格证书编号:
- 同一自然人(同名同证号)出现在两份投标中 → 一级证据
- 仅姓名相同但无证号验证 → 三级证据
- 资质证书编号跨标重复 → 一级证据

### D2企业关联(一级证据)
对比法定代表人、注册地址、联系电话、银行账户、统一社会信用代码:
- 法定代表人/控股股东相同 → 一级证据
- 注册地址/联系电话完全一致 → 一级证据
- 银行账户关联 → 一级证据
- 同一人担任两家公司关键岗位(结合D1) → 佐证企业关联

## 证据级别约束(D1/D2 必须遵守)
D1/D2 维度的 evidence_level 恒为 "first"(一级证据)。证据强弱通过 score 与 certainty 体现,不通过 evidence_level 调节:
- 同名同证号 / 法人相同 / 信用代码一致 等强命中: score>=0.7, certainty>=0.7
- 仅姓名相同未交叉验证 / 注册地址同楼栋等弱命中: score<0.5, certainty<0.5
- 无任何重叠: score=0, certainty=0

## 输出格式(严格JSON)
**重要 -- findings 字段约束**：必须是结构化对象数组，每个元素为完整 dict（包含 summary/detail 等键）。**严禁**将单个 finding 的字段拍平为 "key: value" 字符串列表（如 `["summary: ...", "detail: ...", "quote_a: ..."]` 是错误格式）。无发现时输出空数组 `[]`。
{{
  "d1_personnel": {{
    "score": 0.0至1.0,
    "findings": [
      {{
        "summary": "发现概述",
        "detail": "详细分析,含原文引用",
        "quote_a": "文档1原文摘录或null",
        "page_a": "文档1位置或null",
        "quote_b": "文档2原文摘录或null",
        "page_b": "文档2位置或null",
        "similarity": null
      }}
    ],
    "evidence_level": "first",
    "certainty": 0.0至1.0
  }},
  "d2_company": {{
    "score": 0.0至1.0,
    "findings": [同上结构],
    "evidence_level": "first",
    "certainty": 0.0至1.0
  }},
  "risk_explanation": "判定推理过程"
}}
"""

COLLUSION_TEXT_PATTERN_PROMPT = _GROUPED_PROMPT_HEADER + """
### 行业画像参考（来自 S1d，可能为空）
{industry_profile_context}

### 弱知识库提示（仅在命中通用白名单类别时填充）
{kb_context}

## 分析任务: D3文本雷同 + D6文档格式 + D7异常一致性

### 高相似度文本段落(向量预筛选结果)
{similar_chunks}

### D3技术方案文本雷同(二级证据)
评估高相似度段落中技术方案核心内容的雷同程度:
- 排除: 招标模板要求的固定内容
- 排除: 行业画像标识的标准化内容域(如有,见下方"行业画像"区域),这些内容即便高度相似也属行业常态,certainty不得超过0.2,evidence_level必须为"third"
- 聚焦: 行业画像标识的个性化内容域,这类内容相似才是真正可疑的
- 聚焦: 个性化技术描述、创意性表述、独特解决方案
- 设备清单/参数组合完全一致且非招标指定 → 二级证据
- 仅通用行业术语/行业标准管理方案相同 → 不计入
- 片段对标记了"疑似行业通用表述"(字面重合度低)的 → 大概率属于同义不同词的通用内容,不应作为D3证据

### D6文档格式(三级证据)
比较文档结构特征:
- 目录编排、章节顺序、表格格式超出招标模板要求的一致 → 三级证据
- 招标要求统一的格式 → 正常,不计入

### D7异常一致性(三级证据)
识别不太可能独立巧合的一致:
- 相同错别字/非标准表述 → 三级证据
- 相同内容遗漏模式 → 三级证据
- 相同排版错误(如页码跳跃、图表编号错误) → 三级证据

## findings 输出红线（D3/D6/D7 必须遵守，违反将被自动拦截）
1. 严禁使用以下推测词："假设/疑似/可能/或许/大概/估计/例如/尚不/尚需进一步/有待核实/不排除"。
   推测性观察请放入 risk_explanation 而非 findings。
2. **D7 必须给出具体证据字符**：
   - "相同错别字" 必须给出具体的错别字（quote_a 与 quote_b 中分别明示，且 quote_a != quote_b 反映差异）
   - "相同非标准表述" 必须给出具体表述原文及其规范写法
   - "相同排版错误" 必须给出具体编号或位置
   - quote_a 与 quote_b 完全相同（无差异字符）的"错别字/非标准表述"finding 不得提交
3. **D6 必须给出具体格式特征**：必须命名具体格式项（如"目录二级编号采用罗马数字"/"招投函第3段加粗"），不得仅写"格式雷同"。
4. 任一 finding 的 quote_a / quote_b 与 page_a / page_b 必须能在原文 chunk 中精确锚定；无法锚定的不得提交。
5. 若该维度无法举证具体差异/特征，必须 findings=[]、certainty<0.3，evidence_level="third"。

## 输出格式(严格JSON)
**重要 -- findings 字段约束**：必须是结构化对象数组，每个元素为完整 dict（包含 summary/detail 等键）。**严禁**将单个 finding 的字段拍平为 "key: value" 字符串列表（如 `["summary: ...", "detail: ...", "quote_a: ..."]` 是错误格式）。无发现时输出空数组 `[]`。
{{
  "d3_text": {{
    "score": 0.0至1.0,
    "findings": [
      {{
        "summary": "发现概述",
        "detail": "详细分析",
        "quote_a": "文档1原文摘录或null",
        "page_a": "文档1位置或null",
        "quote_b": "文档2原文摘录或null",
        "page_b": "文档2位置或null",
        "similarity": 0.0至1.0或null
      }}
    ],
    "evidence_level": "second|third",
    "certainty": 0.0至1.0
  }},
  "d6_format": {{
    "score": 0.0至1.0,
    "findings": [同上结构],
    "evidence_level": "third",
    "certainty": 0.0至1.0
  }},
  "d7_abnormal": {{
    "score": 0.0至1.0,
    "findings": [同上结构],
    "evidence_level": "third",
    "certainty": 0.0至1.0
  }},
  "risk_explanation": "判定推理过程"
}}
"""

COLLUSION_BUSINESS_PROMPT = _GROUPED_PROMPT_HEADER + """
{industry_profile_context}

## 分析任务: D4报价异常 + D5资质业绩

### 文档1: {doc_a_name} (doc_id={doc_a_id})
**结构化提取**:
{structured_a}

**文档原文(定向检索)**:
{context_a}

### 文档2: {doc_b_name} (doc_id={doc_b_id})
**结构化提取**:
{structured_b}

**文档原文(定向检索)**:
{context_b}

### D4报价异常关联(二级证据)
分析两份文件的报价数据:
- 总价/分项价呈精确比例(如A=0.95B)或固定差值 → 二级证据
- 一方报价明显偏高(陪标特征) → 二级证据
- 报价结构相似但具体数值合理差异 → 正常
- 行业画像标识的通用报价模式（如有，见上方"行业画像"区域）导致的结构相似属正常，certainty不得超过0.2
- 无报价数据时在findings说明"文档中未发现报价信息"

### D5资质业绩重叠(二级证据)
对比资质证书、业绩项目、分包商/供应商:
- 引用完全相同的业绩项目(同项目名+同合同方) → 二级证据
- 使用相同分包商/供应商 → 二级证据
- 同行业持有相同类型资质(但编号不同) → 正常
- 行业画像标识的通用资质/业绩类型（如有，见上方"行业画像"区域）重叠属正常，certainty不得超过0.2，evidence_level必须为"third"
- 无资质/业绩数据时在findings说明原因
- **空值豁免**: 双方字段值均为"无"、"不适用"、"N/A"、"/"、"-"或空白时，不计入重叠证据，该项score填0
- **法规/标准原文豁免**: 法律法规原文、国家/行业收费标准条文、公开技术规范原文的引用一致，属于合规引用而非个性化内容重叠，evidence_level降为"third"，certainty降至0.3以下
- **无招标文件模板提示**: 若本次分析无招标文件参照（见下方tender_context），对所有D4/D5中certainty>=0.5的finding，在detail末尾追加"（注意: 本次分析未提供招标文件，上述相似性可能源于招标模板统一要求，疑似固定模板）"

## findings 输出红线（D4/D5 必须遵守，违反将被自动拦截）
1. 严禁使用以下推测词："假设/疑似/可能/或许/大概/估计/例如/尚不/尚需进一步/有待核实/不排除"。
   推测性观察请放入 risk_explanation 而非 findings。
2. **D5 必须给出具体重叠项**：finding.detail 必须明示重叠的项目名/合同方/分包商名/资质证书号；不得仅写"业绩雷同"或"资质重合"。
3. **D4 必须给出具体数值**：finding.detail 必须包含双方报价数值与精确比例/差值；不得仅写"报价异常"。
4. 任一 finding 的 quote_a / quote_b 与 page_a / page_b 必须能在原文 chunk 中精确锚定；无法锚定的不得提交。
5. 若该维度无法举证具体重叠/数值，必须 findings=[]、certainty<0.3。

## 输出格式(严格JSON)
**重要 -- findings 字段约束**：必须是结构化对象数组，每个元素为完整 dict（包含 summary/detail 等键）。**严禁**将单个 finding 的字段拍平为 "key: value" 字符串列表（如 `["summary: ...", "detail: ...", "quote_a: ..."]` 是错误格式）。无发现时输出空数组 `[]`。
{{
  "d4_price": {{
    "score": 0.0至1.0,
    "findings": [
      {{
        "summary": "发现概述",
        "detail": "详细分析",
        "quote_a": "文档1原文摘录或null",
        "page_a": "文档1位置或null",
        "quote_b": "文档2原文摘录或null",
        "page_b": "文档2位置或null",
        "similarity": null
      }}
    ],
    "evidence_level": "second|third",
    "certainty": 0.0至1.0
  }},
  "d5_qualification": {{
    "score": 0.0至1.0,
    "findings": [同上结构],
    "evidence_level": "second|third",
    "certainty": 0.0至1.0
  }},
  "risk_explanation": "判定推理过程"
}}
"""

COLLUSION_SINGLE_DIM_PROMPT = """两份投标文件的向量相似度为 {similarity_pct}%。请仅分析以下维度:

## 维度: {dim_label}
{dim_instruction}

### 文档1: {doc_a_name} (doc_id={doc_a_id})
{context_a}

### 文档2: {doc_b_name} (doc_id={doc_b_id})
{context_b}

{tender_context}

## findings 输出红线（禁止违反）
若该维度未发现可疑串标线索，必须 findings=[]、certainty<0.3。
严禁把"未发现/不构成/无实质性/已豁免/属模板/属惯例"等反向定性陈述放入 findings，应改写到 risk_explanation。

## 输出格式(严格JSON)
**重要 -- findings 字段约束**：必须是结构化对象数组，每个元素为完整 dict（包含 summary/detail 等键）。**严禁**将单个 finding 的字段拍平为 "key: value" 字符串列表（如 `["summary: ...", "detail: ...", "quote_a: ..."]` 是错误格式）。无发现时输出空数组 `[]`。
{{
  "score": 0.0至1.0,
  "findings": [
    {{
      "summary": "发现概述",
      "detail": "详细分析",
      "quote_a": "文档1原文或null",
      "page_a": "文档1位置或null",
      "quote_b": "文档2原文或null",
      "page_b": "文档2位置或null",
      "similarity": null
    }}
  ],
  "evidence_level": "first|second|third",
  "certainty": 0.0至1.0,
  "risk_explanation": "判定说明"
}}
"""

# 深搜时各维度的分析指令
DIMENSION_INSTRUCTIONS: dict[str, str] = {
    "d1_personnel": "对比关键人员姓名、身份证号、资格证书编号,识别同一自然人跨标出现(一级证据)",
    "d2_company": "对比法定代表人、注册地址、联系电话、银行账户,识别企业实质关联(一级证据)",
    "d4_price": "分析报价是否呈固定比例/差值的数学规律,识别陪标特征(二级证据)",
    "d5_qualification": "对比资质证书、业绩项目、分包商,识别重叠(二级证据)",
}

# =============================================================================
# S4：综合研判 Prompt
# =============================================================================
COLLUSION_PAIR_JUDGEMENT_PROMPT = """请基于以下已整理的材料事实，对两份投标文件做串标嫌疑判定。

## 文件对
- 文件A: {doc_a_name} (doc_id={doc_a_id})
- 文件B: {doc_b_name} (doc_id={doc_b_id})
- 语义相似度: {similarity_pct}

## 材料事实
{pair_facts}

## 招标/行业上下文
{tender_context}
{industry_profile_context}

## 判定要求
1. 只能依据上面的材料事实判定，禁止臆造未出现的证据。
2. 若事实不足，应降低 certainty，必要时输出 `overall_risk="inconclusive"`。
3. `review_state` 不输出，由系统根据结果和流程决定。
4. 输出结构必须兼容现有 `LLMCompareResult`。
5. 所有字符串字段使用自然语言，禁止Markdown语法（# 标题、- 列表、** 粗体）。
6. findings 输出红线：若某维度未发现可疑串标线索，必须 findings=[]、certainty<0.3；
   严禁把"未发现/不构成/无实质性/已豁免/属模板/属惯例/核心内容不同"等反向定性
   陈述放入 findings 或 key_findings，应改写到 risk_explanation 段落。

## 输出格式（严格JSON，不输出任何其他文字）
**重要 -- findings / key_findings 字段约束**：必须是结构化对象数组，每个元素为完整 dict（包含 summary/detail 等键）。**严禁**将单个 finding 的字段拍平为 "key: value" 字符串列表（如 `["summary: ...", "detail: ...", "quote_a: ..."]` 是错误格式）。无发现时输出空数组 `[]`。
{{
  "overall_risk": "high|medium|low|none|inconclusive",
  "d1_personnel": {{ "score": 0.0, "findings": [], "evidence_level": "first", "certainty": 0.0 }},
  "d2_company": {{ "score": 0.0, "findings": [], "evidence_level": "first", "certainty": 0.0 }},
  "d3_text": {{ "score": 0.0, "findings": [], "evidence_level": "second", "certainty": 0.0 }},
  "d4_price": {{ "score": 0.0, "findings": [], "evidence_level": "second", "certainty": 0.0 }},
  "d5_qualification": {{ "score": 0.0, "findings": [], "evidence_level": "second", "certainty": 0.0 }},
  "d6_format": {{ "score": 0.0, "findings": [], "evidence_level": "third", "certainty": 0.0 }},
  "d7_abnormal": {{ "score": 0.0, "findings": [], "evidence_level": "third", "certainty": 0.0 }},
  "d8_metadata": {{ "score": 0.0, "findings": [], "evidence_level": "third", "certainty": 0.0 }},
  "key_findings": [],
  "risk_explanation": "风险判定说明"
}}
"""

COLLUSION_PROJECT_SUMMARY_PROMPT = """请基于以下文件对判定结果、群组结果和覆盖率信息，生成项目级串标综合结论的"研判推断段"。

## 文件对判定（JSON）
{pair_judgements}

## 嫌疑群组摘要
{groups_summary}

## 覆盖率与质量信息
{coverage_summary}

## 行业画像质量信息
{profile_quality_summary}

## 全局特征发现（跨文件 D1/D2/D4/D8 等原始信号，用于校准 pair/group 级别结论）
{global_findings_context}

## 任务要求
1. 只能依据提供的数据生成项目级结论，不得重复推断未给出的证据。
2. **summary 字段只输出综合"研判推断"**：跨证据关联、嫌疑模式判断、风险等级支持理由；
   **严禁**复述覆盖率数字、quality_flags 字面内容、行业画像置信度状态描述、白名单命中类别 --
   这些事实说明由系统侧统一拼装为独立的"事实段"，重复输出会被视为冗余而过滤。
3. `suspected_groups` 只能引用已给出的群组/文件对信息，禁止生成不存在的组合。
4. 所有字符串字段必须使用自然语言段落，禁止Markdown语法（# 标题、- 列表、** 粗体）。
5. 当无可疑证据时，summary 输出简短中性描述（如"未发现可支撑串标认定的异常证据"），
   不必拼凑可信度边界说明。

## 输出格式（严格JSON，不输出任何其他文字）
{{
  "overall_risk": "high|medium|low|none|inconclusive",
  "suspected_groups": [
    {{
      "document_ids": [101, 205],
      "document_names": ["文件名1", "文件名2"],
      "risk_level": "high|medium|low",
      "reason": "认定理由"
    }}
  ],
  "collusion_network": "串通网络描述",
  "bid_accompanying_pattern": "陪标模式分析",
  "summary": "综合研判推断段（仅含跨证据关联与嫌疑模式判断，禁止复述事实信号）",
  "investigation_suggestions": ["建议1", "建议2"]
}}
"""

COLLUSION_OVERALL_SYNTHESIS_PROMPT = """请基于以下文件对判定结果、嫌疑群组信息、覆盖率与全局发现，生成项目级串标综合结论（有群组场景）。

## 文件对判定（JSON 数组，已按风险排序）
{pair_judgements}

## 嫌疑群组摘要（JSON 字符串，含以下字段）
- `group_count`: 已识别的嫌疑群组数量
- `groups_summary`: 各群组一句话描述（含群组序号、风险级别、成员公司名）
- `ungrouped_pairs`: 未归入任何群组但仍存在风险的文件对（自然语言列表，无则为 "(无)"）
- `global_stats`: 全局统计 {{personnel_overlap_groups, company_edges, pricing_patterns, metadata_clusters}}

{groups_summary}

## 覆盖率与质量信息（JSON）
{coverage_summary}

## 行业画像质量信息（JSON）
{profile_quality_summary}

## 全局特征发现（跨文件 D1/D2/D4/D8 等原始信号，用于校准 pair/group 级别结论）
{global_findings_context}

## 任务要求
1. 只能依据提供的数据生成项目级结论，不得重复推断未给出的证据。
2. `suspected_groups` 必须从已给出的"嫌疑群组摘要"或文件对判定中遴选，严禁臆造新的成员组合；引用群组时使用真实公司名 + doc_id。
3. `collusion_network` 应基于"嫌疑群组摘要 + 全局特征发现"描述跨群组关联或独立群组的拓扑，限 3 句以内。
4. `bid_accompanying_pattern` 应基于"全局统计 + 报价模式"识别陪标特征，无数据支撑时填空字符串。
5. **summary 字段只输出综合"研判推断"**：跨证据关联、嫌疑模式判断、风险等级支持理由；
   **严禁**复述覆盖率数字、quality_flags 字面内容、行业画像置信度状态描述、白名单命中类别 --
   这些事实说明由系统侧统一拼装为独立的"事实段"，重复输出会被视为冗余而过滤。
6. 所有字符串字段必须使用自然语言段落，禁止 Markdown 语法（# 标题、- 列表、** 粗体）。

## 输出格式（严格JSON，不输出任何其他文字）
{{
  "overall_risk": "high|medium|low|none|inconclusive",
  "suspected_groups": [
    {{
      "document_ids": [101, 205],
      "document_names": ["文件名1", "文件名2"],
      "risk_level": "high|medium|low",
      "reason": "认定理由（引用群组序号或具体证据维度）"
    }}
  ],
  "collusion_network": "跨群组关联或独立群组的拓扑描述（限3句）",
  "bid_accompanying_pattern": "陪标模式分析（无数据支撑时填空字符串）",
  "summary": "综合研判推断段（仅含跨证据关联与嫌疑模式判断，禁止复述事实信号）",
  "investigation_suggestions": ["建议1", "建议2"]
}}
"""

# =============================================================================
# 辅助函数：构建 tender_context 和 similar_chunks 文本块
# =============================================================================

def build_tender_context(format_requirements: dict | None) -> str:
    """构建招标格式参照上下文，用于注入分组对比与单维搜索 prompt"""
    if not format_requirements:
        return (
            "## 招标格式参照\n"
            "本次分析未提供招标文件,无法自动排除模板导致的正常相似。"
            "请如实记录所有发现的重叠和相似,在findings中注明是否疑似行业惯例或模板内容。"
        )
    
    lines = ["## 招标格式参照（以下一致属于正常，不计入串通证据）"]
    
    toc = format_requirements.get("toc_requirements") or []
    if toc:
        lines.append(f"目录结构要求: {'; '.join(toc[:5])}")
    
    wording = format_requirements.get("fixed_wording") or []
    if wording:
        lines.append(f"固定措辞: {'; '.join(wording[:3])}")
    
    fmt_rules = format_requirements.get("unified_format_rules") or []
    if fmt_rules:
        lines.append(f"统一格式规定: {'; '.join(fmt_rules[:5])}")
    
    return "\n".join(lines)


def build_profile_business_context(profile: dict | None) -> str:
    """将行业画像格式化为可注入 D4/D5 分析 Prompt 的上下文文本

    与 build_profile_domain_context 侧重 D3 不同，本函数聚焦于
    资质/业绩通用性和报价模式的行业常态识别。
    """
    if not profile:
        return ""

    industry = profile.get("industry_type", "")
    confidence = str(profile.get("confidence", "unknown")).lower()
    if confidence not in {"high", "medium"} or not industry or industry == "unknown":
        return "### 行业画像状态: 当前材料不足以形成高可信行业画像，请不要依据预设行业惯例降低或提高 D4/D5 风险。"

    lines: list[str] = [f"### 行业画像（D4/D5参考）: {industry}"]

    quals = profile.get("common_qualification_types") or []
    if quals:
        lines.append(
            "\n**行业通用资质/业绩类型（以下类型重叠属正常，不应作为D5串标证据，certainty不得超过0.2）:**"
        )
        for item in quals:
            if isinstance(item, dict):
                lines.append(f"- {item.get('name', '')}: {item.get('reason', '')}")
            elif isinstance(item, str):
                lines.append(f"- {item}")

    pricing = profile.get("common_pricing_patterns") or []
    if pricing:
        lines.append(
            "\n**行业通用报价模式（以下模式导致的报价结构相似属正常，certainty不得超过0.2）:**"
        )
        for item in pricing:
            if isinstance(item, dict):
                lines.append(f"- {item.get('pattern', '')}: {item.get('reason', '')}")
            elif isinstance(item, str):
                lines.append(f"- {item}")

    return "\n".join(lines)


def build_similar_chunks_text(
    chunk_pairs: list[dict],
    max_pairs: int = 10,
    doc_a_id: int | None = None,
    doc_b_id: int | None = None,
    doc_a_name: str | None = None,
    doc_b_name: str | None = None,
) -> str:
    """构建高相似度段落对文本块

    Args:
        chunk_pairs: 每个元素包含 {score, text_a, text_b, page_a, page_b}
        max_pairs: 最多展示的对数
    """
    if not chunk_pairs:
        return "（向量预筛选未发现高相似度段落）"
    
    lines = [f"（共 {len(chunk_pairs)} 对高相似度段落，展示前 {min(len(chunk_pairs), max_pairs)} 对）"]
    
    label_a = doc_a_name or "文档1"
    label_b = doc_b_name or "文档2"

    for i, pair in enumerate(chunk_pairs[:max_pairs], 1):
        score = pair.get("score", 0)
        literal = pair.get("literal_score")
        boilerplate = pair.get("boilerplate_flag", False)
        text_a = (pair.get("text_a") or "")[:200]
        text_b = (pair.get("text_b") or "")[:200]
        page_a = pair.get("page_a")
        page_b = pair.get("page_b")
        
        loc_a = f"第{page_a}页" if page_a else "位置未知"
        loc_b = f"第{page_b}页" if page_b else "位置未知"
        
        meta_a = f"{loc_a}, doc_id={doc_a_id}" if doc_a_id is not None else loc_a
        meta_b = f"{loc_b}, doc_id={doc_b_id}" if doc_b_id is not None else loc_b

        sim_label = f"语义相似度: {score:.2f}"
        if literal is not None:
            sim_label += f" | 字面重合度: {literal:.2f}"
        if boilerplate:
            sim_label += " [疑似行业通用表述，非复制粘贴]"

        lines.append(
            f"\n[片段对{i}] {sim_label}\n"
            f"  {label_a}({meta_a}): {text_a}{'...' if len(pair.get('text_a',''))>200 else ''}\n"
            f"  {label_b}({meta_b}): {text_b}{'...' if len(pair.get('text_b',''))>200 else ''}"
        )
    
    return "\n".join(lines)


# =============================================================================
# S4 群组研判 Prompt（每个嫌疑群组1次LLM调用）
# =============================================================================

COLLUSION_GROUP_ANALYSIS_PROMPT = """请分析以下疑似串通群组的内部关系和角色分工。

## 重要规则
1. 所有涉及的公司必须使用doc_id和真实公司名（如"doc_id=101 <PERSON_1>建设公司"），严禁使用"文件A/B/C"、"投标人1/2/3"等占位词。
2. 角色判定必须有具体证据支撑。证据不足时标注"角色不明"，不要猜测。
3. collusion_strategy 限200字以内。
4. investigation_focus 最多5条。
5. 未发现的字段返回空字符串""或空数组[]，禁止返回null。
6. 所有字符串字段（collusion_strategy/key_evidence_summary/role_evidence/investigation_focus各项）必须使用自然语言，禁止Markdown语法（# 标题、- 列表、** 粗体）。

## 群组成员
{member_list}

## 成员间关联证据
{aggregated_evidence}

## 全局特征发现
{global_findings}

## 分析任务
1. **角色识别**: 每个成员在串通中的角色（主标方/陪标方/组织者/角色不明）
2. **串通策略推断**: 根据证据推断采用的串通策略
3. **证据强度评估**: 该群组的串通嫌疑程度

## 输出格式（严格JSON，不输出任何其他文字）
{{{{
  "group_risk": "high|medium|low",
  "roles": [
    {{{{
      "doc_id": 101,
      "company_name": "实际公司名称",
      "role": "主标方|陪标方|组织者|角色不明",
      "role_evidence": "一句话判定依据"
    }}}}
  ],
  "collusion_strategy": "推断的串通策略描述（限200字）",
  "key_evidence_summary": "最关键的2-3条证据概述",
  "investigation_focus": [
    "建议1：具体调查方向",
    "建议2：具体调查方向"
  ]
}}}}
"""


# =============================================================================
# S2 报价分析 Prompt（可选，>=3份有价格数据时调用）
# =============================================================================
# =============================================================================
# S4.2 全局综合 Prompt（替代现有 SYNTHESIS，全项目1次）
# =============================================================================
# =============================================================================
# S1d：行业画像生成 Prompt（材料驱动，两阶段）
# =============================================================================

COLLUSION_INDUSTRY_SIGNAL_PROMPT = """请仅基于以下上传材料内容，抽取能够识别项目行业/细分场景的信号。

## 项目材料
{project_context}

## 任务要求
1. 只能从提供的材料中提取信号，禁止使用任何预置行业背景或外部常识补全。
2. 每条信号必须说明来源类型与来源定位（页码/章节/文档）。
3. 若材料不足以支撑行业判断，可返回空信号，并把 confidence 设为 low 或 unknown。
4. 禁止直接输出行业结论，只输出“信号”。

## 输出格式（严格JSON，不输出任何其他文字）
{{
  "confidence": "high|medium|low|unknown",
  "summary": "信号提取摘要（1-2句话）",
  "signals": [
    {{
      "signal_type": "title|term|domain|qualification|pricing|equipment|process|standard",
      "value": "从材料中提取出的原始行业信号",
      "source": "tender|bid_extraction|chunk_heading|chunk_body",
      "source_refs": ["第12页《采购范围》", "投标文件A-技术方案"],
      "confidence": 0.0
    }}
  ]
}}
"""

COLLUSION_INDUSTRY_PROFILE_PROMPT_V2 = """请仅基于以下行业信号，归纳当前项目的行业画像。

## 行业信号（JSON）
{industry_signals}

## 任务要求
1. 只能依据上述信号归纳，不得引入材料之外的行业背景。
2. 若信号不足以判断具体行业，必须输出 `industry_type="unknown"` 且 `confidence="low"` 或 `"unknown"`。
3. `standardized_domains` / `creative_domains` / `common_qualification_types` / `common_pricing_patterns` 只能从当前信号支持的内容中归纳，信号不足时允许返回空数组。
4. 不允许为了凑数量臆造通用章节、资质或报价模式。

## 输出格式（严格JSON，不输出任何其他文字）
{{
  "industry_type": "行业名称 + 项目细分类型，或 unknown",
  "confidence": "high|medium|low|unknown",
  "summary": "基于材料信号的归纳说明（1-2句话）",
  "standardized_domains": [
    {{
      "domain": "标准化内容域名称",
      "reason": "为什么该内容域在当前项目中更可能属于通用/模板/标准化内容"
    }}
  ],
  "creative_domains": [
    {{
      "domain": "个性化内容域名称",
      "reason": "为什么该内容域在当前项目中应体现差异性"
    }}
  ],
  "common_qualification_types": [
    {{
      "name": "材料支持的通用资质/业绩类型",
      "reason": "归纳理由"
    }}
  ],
  "common_pricing_patterns": [
    {{
      "pattern": "材料支持的常见报价模式",
      "reason": "归纳理由"
    }}
  ],
  "source_mode": "material_only|insufficient"
}}
"""


def build_industry_signal_context(
    tender_text: str | None = None,
    bid_extractions: list[dict] | None = None,
) -> str:
    """兼容旧导入路径，实际实现位于 industry_profile_context.py。"""
    from app.core.collusion.industry_profile_context import (
        IndustryProfileContextInput,
        build_industry_signal_context as _build_context,
    )

    return _build_context(
        IndustryProfileContextInput(
            tender_text=tender_text,
            bid_extractions=bid_extractions or [],
        )
    )


def build_profile_domain_context(profile: dict | None) -> str:
    """将行业画像格式化为可注入 D3 分析 Prompt 的上下文文本"""
    if not profile:
        return ""

    confidence = str(profile.get("confidence", "unknown")).lower()
    industry = profile.get("industry_type", "")
    if confidence not in {"high", "medium"} or not industry or industry == "unknown":
        return "### 行业画像状态: 当前材料不足以形成高可信行业画像，请仅基于实际材料判断，禁止套用行业先验。"

    lines: list[str] = [f"### 行业画像: {industry}"]

    std = profile.get("standardized_domains") or []
    if std:
        lines.append(
            "\n**标准化内容域（以下内容相似属正常，certainty不得超过0.2）:**"
        )
        for item in std:
            if isinstance(item, dict):
                lines.append(f"- {item.get('domain', '')}: {item.get('reason', '')}")
            elif isinstance(item, str):
                lines.append(f"- {item}")

    creative = profile.get("creative_domains") or []
    if creative:
        lines.append(
            "\n**个性化内容域（以下内容相似即可疑，应重点分析）:**"
        )
        for item in creative:
            if isinstance(item, dict):
                lines.append(f"- {item.get('domain', '')}: {item.get('reason', '')}")
            elif isinstance(item, str):
                lines.append(f"- {item}")

    return "\n".join(lines)
