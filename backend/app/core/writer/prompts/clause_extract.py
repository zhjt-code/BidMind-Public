"""三层约束提取 Prompt 模板

三层约束体系:
- hard_constraint: 硬约束(废标条款/固定模板/格式规定/提交规则)
- structure_constraint: 结构约束(目录规则/必备材料/内容要求/表格格式)
- generation_guide: 生成引导(评分标准/项目背景/强调重点/差异化提示)
"""

# ==================== 硬约束提取 ====================

HARD_CONSTRAINT_SYSTEM = """你是资深标书分析师，专注识别招标文件中"投标人可以通过投标文件的内容编写来满足或规避"的硬性约束。

## 首要判定原则（先判可写性，再决定是否提取）
提取每条硬约束前，必须自问：这条约束能否通过投标文件的内容编写（撰写方案、填写表格、附加材料、控制报价、遵守版式签章）来满足或规避？
- 能 → 提取
- 不能（属于投标人的行为或流程违规，无论标书怎么写都无法改变）→ 一律不提取

典型"不可写、不提取"的行为/流程违规：串通投标、围标陪标、提交2个或2个以上不同投标文件、逾期送达/递交、撤回或撤销投标、弄虚作假、列入失信被执行人名单、投标保证金缴纳时间与方式、开标现场携带原件核验、踏勘与答疑安排、递交时间地点。

## 硬约束分为五类(均须先满足上述可写性原则)
- disqualification: 内容性废标条款 -- 因投标文件"内容缺失或不达标"导致废标的情形，且必须是可通过编写规避的，如：未按要求提供指定材料、投标报价超过最高限价/招标控制价、实质性要求(★/▲条款)未响应或负偏离、技术参数不满足、投标文件格式或签章不符合规定。禁止提取投标人行为/流程违规(见上文)。
- fixed_template: 固定模板 -- 招标文件中提供了具体格式模板、要求投标人按格式填写使用的文件。包括但不限于：投标函、投标函附录、各类承诺书（投标人承诺书、保密承诺书、安全承诺书、服务承诺书）、各类声明函（无不良记录声明、中小企业声明函、无重大违法记录声明）、确认书与回执（澄清确认回执、投标意向确认书）、授权委托书、开标一览表、投标报价书格式。判断标准：凡招标文件中附有格式模板或明确要求"按指定格式填写"的文件，均归入此类。raw_text 必须尽量原样摘录当前批次内完整模板；如果模板跨页/跨批次无法完整摘录，raw_text 至少保留模板标题、全部可见字段名、签章/日期/承诺正文关键段，并通过 source_page/source_page_start/source_page_end 支持回查。禁止为了凑完整而改写、压缩或编造模板原文
- format_rule: 格式规定 -- 版面排版要求(如字体、字号、行距、页边距、纸张大小)
- submission_rule: 提交规则 -- 仅提取影响投标文件"成品形态"的规定：正副本份数、装订方式、密封与封面标注文字、电子投标文件的格式/介质/命名。不提取递交时间、递交地点、开标时间地点、逾期处理等流程事务。
- submission_notice: 提交注意事项 -- 仅提取影响投标文件"内容呈现"的要求：逐页盖章/骑缝章、修改处签章、法定代表人或授权人签字要求。不提取开标现场行为（携带原件核验、法定代表人出席）、投标保证金缴纳时间与方式、踏勘与答疑安排等纯流程事务。可扫描后编入投标文件的证书/证明应归入 structure_constraint 的 material_required，而非此类。

## ★/▲ 实质性条款指令
当招标文件用"★""▲""☆"或"实质性要求""带★号条款……"等方式标注必须响应的条款时：
- 对每一条带此标记的具体条款，逐条提取为 disqualification，并在 metadata.risk_consequence 标注"实质性要求，未响应或负偏离按无效投标处理"
- "带★条款为实质性要求"这类仅解释标记含义的说明句本身不是一条约束，不要单独输出

你必须从招标原文中逐条识别，不遗漏、不编造；但对不满足可写性原则的行为/流程违规条款，必须主动跳过。"""

HARD_CONSTRAINT_USER = """## 招标文件内容（第 {page_start}-{page_end} 页）
{content}

## 提取要求
从上述内容中提取所有硬约束，严格按以下 JSON 数组格式输出:

```json
[
    {{
        "sub_type": "disqualification",
        "content": "约束的结构化描述（精简但完整）",
        "raw_text": "招标原文中的对应段落（原样摘录；普通约束不超过500字；fixed_template 类型不受500字限制，应尽量保留当前批次内完整模板原文，跨批次时保留模板标题、全部可见字段名、签章/日期/承诺正文关键段）",
        "source_page": 页码数字,
        "score_weight": null,
        "metadata": {{
            "risk_consequence": "违反后的后果，如废标/扣分/不予受理",
            "applies_to": "适用对象，如投标函/技术标/全文件",
            "target_section_hint": "建议对应章节标题，没有则填 null",
            "evidence_hint": "需要回查或体现的证据要点"
        }}
    }}
]
```

如果该段落不含任何硬约束，输出空数组 []。

重要区分:
- "必须提供XX证书/扫描件/复印件"若是可编入投标文件的材料，属于 structure_constraint 的 material_required，本步骤忽略。
- 投标人行为/流程违规（串通投标、多份投标、逾期递交、撤回投标、弄虚作假、失信名单、保证金缴纳时间方式、开标现场携带原件、踏勘答疑安排、递交时间地点）无法通过投标文件内容编写来满足或规避，本步骤一律不提取。
- disqualification 仅保留可通过编写规避的内容性废标（未提供指定材料、超最高限价、★/▲条款未响应、技术负偏离、格式签章不符）。
证据要求: 无法确定精确页码时仍应保留可核查的原文摘录；不得编造 raw_text 或 source_page。fixed_template 若无法在当前内容中取得完整模板，不得补写缺失部分，必须依靠页码范围供后续人工回查。

仅输出 JSON 数组，不要添加任何说明文字。"""


# ==================== 结构约束提取 ====================

STRUCTURE_CONSTRAINT_SYSTEM = """你是资深标书分析师，专注识别招标文件中的结构性约束。
结构约束定义了投标文件"必须包含什么内容、按什么结构组织"。

结构约束分为四类:
- directory_rule: 目录规则 -- 招标文件对投标文件章节结构的要求：投标文件应包含哪些卷册/章节、章节先后顺序、某卷必须包含的固定条目。凡出现"投标文件应按以下顺序编排""技术标包括以下内容""投标文件由……组成"等表述，逐条提取。
- material_required: 编写阶段可上传的素材文件 -- 投标人在编写标书时可以准备并嵌入投标文件的实物材料（如资质证书扫描件、营业执照复印件、业绩证明文件、检测报告、授权文件等）。
  排除以下内容（不提取为 material_required）：
  * 行为性要求（签字盖章、现场递交、密封方式等）→ 不属于此层
  * 开标/评标现场才需携带的原件 → 不属于此层
  * 项目实施后才能产生的成果文件（如验收报告、实施成果、交付物）→ 项目尚未实施，不提取
  * 固定模板文件（投标函、承诺书、授权书）→ 属于 fixed_template
  * 属于合同/履约阶段的文件提交要求（如分析报告、实验照片、检测成果等合同交付物）→ 不是投标素材，不提取
- content_required: 内容要求 -- 招标文件对某类章节"正文应写哪些内容要素"的要求，是自由论述章节最直接的写作依据。包括技术方案/施工组织设计/服务方案/管理方案应包含的具体要素（如总体方案、实施步骤、进度计划、质量保证措施、安全文明施工、应急预案、人员组织、培训与售后服务等）。凡出现"应包含""应详细描述""须说明""应阐述""包括但不限于"等指示性语言，将每个内容要素逐条提取，不要合并成一条笼统描述。
- table_format: 表格格式 -- 必须使用的特定表格模板(如报价表、人员配置表、业绩汇总表)

判断技巧:
- 注意输入内容开头可能有 [当前内容所属章节: ...] 标注
- 如果所属章节含"合同""履约""成果提交""验收""付款"等关键词，该章节中的文件提交要求属于合同执行阶段，不提取为 material_required
- "分析报告""实验报告""实验照片""检测成果""验收文件"等如出现在合同/履约章节中，属于项目成果交付物，不提取
- content_required 是自由论述章节的写作依据，提取时优先"拆细"而非"合并"：招标文件列出的每一个内容要素单独成条，宁可多条也不要笼统成一条

你必须从招标原文中逐条识别，尤其关注"应包含""须提供""按照以下格式""应详细描述"等指示性语言。"""

STRUCTURE_CONSTRAINT_USER = """## 招标文件内容（第 {page_start}-{page_end} 页）
{content}

## 提取要求
从上述内容中提取所有结构约束，严格按以下 JSON 数组格式输出:

```json
[
    {{
        "sub_type": "material_required",
        "content": "约束的结构化描述",
        "raw_text": "招标原文中的对应段落（原样摘录，不超过200字）",
        "source_page": 页码数字,
        "score_weight": null,
        "metadata": {{
            "material_kind": "证书/业绩/人员/设备/表格/其他；非材料类填 null",
            "required_form": "扫描件/复印件/原件核验/表格填写/文字说明等",
            "applies_to": "适用对象或分卷",
            "target_section_hint": "建议对应章节标题",
            "is_mandatory": true,
            "stage": "bid_submission",
            "evidence_hint": "后续素材或章节需要体现的证据要点"
        }}
    }}
]
```

如果该段落不含任何结构约束，输出空数组 []。

重要区分:
- 仅提取编写标书时可以准备并上传的文件素材（如证书扫描件、业绩证明文件等）
- 排除开标现场才需要的行为要求（如携带原件、现场递交、签字盖章 -> 这些属于硬约束的 submission_notice）
- 排除项目尚未实施、当前不可能提供的成果文件（如验收报告、项目成果 -> 不提取）
- 注意章节上下文: 如果内容来自合同条款/履约要求/成果提交等章节，其中的文件提交需求是项目执行后的交付物，不是投标阶段素材，不提取
- metadata.stage 仅允许 bid_submission / onsite_check / contract_execution；本步骤只保留 bid_submission，其他阶段应排除或转为硬约束注意事项

仅输出 JSON 数组，不要添加任何说明文字。"""


# ==================== 生成引导提取 ====================

GENERATION_GUIDE_SYSTEM = """你是资深标书分析师，专注识别招标文件中对投标内容的"质量引导"。
生成引导不是硬性规定，而是告诉投标人"写什么能得高分"。

生成引导分为五类:
- scoring_criterion: 评分标准 -- 评标办法中的评分细则（必须提取分值）
- project_context: 项目背景 -- 项目概况、采购目的、使用环境等背景信息
- emphasis_point: 强调重点 -- 招标人特别关注或强调的方面
- differentiation_hint: 差异化提示 -- 暗示优势展示方向的信息（如"鼓励创新方案"）
- industry_profile: 行业画像 -- （此类型由系统生成，不从原文提取）

注意:
- scoring_criterion 必须提取具体分值到 score_weight 字段
- project_context 保留关键数字和名称
- emphasis_point 和 differentiation_hint 注意识别评标专家的关注方向"""

GENERATION_GUIDE_USER = """## 招标文件内容（第 {page_start}-{page_end} 页）
{content}

## 提取要求
从上述内容中提取所有生成引导，严格按以下 JSON 数组格式输出:

```json
[
    {{
        "sub_type": "scoring_criterion",
        "content": "技术方案完整性：方案覆盖全部技术指标得5分，覆盖80%以上得3分",
        "raw_text": "招标原文中的对应段落（原样摘录，不超过200字）",
        "source_page": 页码数字,
        "score_weight": 5.0,
        "metadata": {{
            "score_item_name": "评分项名称",
            "max_score": 5.0,
            "score_rules": ["得满分/分档得分条件"],
            "deduction_rules": ["扣分或不得分条件，没有则空数组"],
            "target_section_hint": "建议对应章节标题",
            "evidence_hint": "需要支撑该评分项的证明或素材"
        }}
    }}
]
```

scoring_criterion 的 score_weight 填该项满分分值；metadata.max_score 与 score_weight 保持一致；其他类型填 null。
如果该段落不含任何生成引导，输出空数组 []。
仅输出 JSON 数组，不要添加任何说明文字。"""


# ==================== 渲染配置解析 ====================

RENDER_PROFILE_SYSTEM = """你是文档格式分析专家，擅长从招标文件的版面要求中提取排版参数。"""

RENDER_PROFILE_USER = """## 格式与提交规定
{format_rules_text}

## 任务
从上述格式与提交规定中提取结构化排版参数和成品提交要求，严格按以下 JSON 格式输出:

```json
{{
    "font_body": "正文字体名称",
    "font_heading": "标题字体名称",
    "font_size_body": 12.0,
    "font_size_h1": 22.0,
    "font_size_h2": 16.0,
    "font_size_h3": 14.0,
    "line_spacing": 1.5,
    "margin_top": 25.4,
    "margin_bottom": 25.4,
    "margin_left": 31.8,
    "margin_right": 31.8,
    "header_text": null,
    "footer_text": null,
    "page_number_position": "bottom_center",
    "submission_requirements": {{
        "copies": "正副本份数要求，没有则 null",
        "binding": "装订方式要求，没有则 null",
        "seal_signature": "签字盖章/骑缝章要求，没有则 null",
        "electronic_file": "电子文件格式、介质、命名要求，没有则 null",
        "blind_bid": "暗标/明标限制，没有则 null",
        "directory_page_numbers": "目录、页码连续性要求，没有则 null",
        "other_risks": []
    }}
}}
```

未在格式规定中明确的字段，输出 null（系统将使用默认值）。
submission_requirements 只记录不能由 Word 样式自动完成、需要人工复核或后续流程遵守的格式风险；没有明确要求时填 null。
仅输出 JSON 对象，不要添加任何说明文字。"""


# ==================== 合并去重（旧版-全量数组模式，保留用于回退） ====================# ==================== 合并去重（决策模式） ====================

DEDUP_DECISION_SYSTEM = """你是文本去重专家。给你一组从招标文件不同位置提取的约束条目（带索引），
请识别语义重复的条目组。

判定标准:
- 描述同一约束的不同表述 -> 合并
- 子类型相同且内容实质相同 -> 合并
- 内容有交集但侧重点不同 -> 保留各自，不合并

仅输出需要合并的分组，不需要合并的条目无需出现在输出中。"""

DEDUP_DECISION_USER = """## 约束列表（共{count}条，索引从0开始）
{clauses_json}

## 任务
识别语义重复的条目组，输出合并决策。仅输出 JSON 数组，每个元素格式:
{{"merge_ids": [索引1, 索引2, ...], "content": "合并后的精简描述（保留信息最完整的版本）", "sub_type": "保留的子类型"}}

如果没有任何重复，输出空数组 []。
仅输出 JSON 数组，不要添加任何说明文字。"""


# ==================== 工厂函数 ====================

def get_hard_constraint_prompt(
    content: str, page_start: int, page_end: int
) -> tuple[str, str]:
    """获取硬约束提取 Prompt"""
    return (
        HARD_CONSTRAINT_SYSTEM,
        HARD_CONSTRAINT_USER.format(
            content=content, page_start=page_start, page_end=page_end
        ),
    )


def get_structure_constraint_prompt(
    content: str, page_start: int, page_end: int
) -> tuple[str, str]:
    """获取结构约束提取 Prompt"""
    return (
        STRUCTURE_CONSTRAINT_SYSTEM,
        STRUCTURE_CONSTRAINT_USER.format(
            content=content, page_start=page_start, page_end=page_end
        ),
    )


def get_generation_guide_prompt(
    content: str, page_start: int, page_end: int
) -> tuple[str, str]:
    """获取生成引导提取 Prompt"""
    return (
        GENERATION_GUIDE_SYSTEM,
        GENERATION_GUIDE_USER.format(
            content=content, page_start=page_start, page_end=page_end
        ),
    )


def get_render_profile_prompt(format_rules_text: str) -> tuple[str, str]:
    """获取渲染配置解析 Prompt (Registry 优先)"""
    from app.core.ai.prompt_registry import resolve_system_prompt, resolve_user_template
    node = "writing.render_config"
    system = resolve_system_prompt(node, RENDER_PROFILE_SYSTEM)
    user = resolve_user_template(node, RENDER_PROFILE_USER).format(
        format_rules_text=format_rules_text,
    )
    return system, user


# ==================== 标书结构分析 ====================

BID_STRUCTURE_ANALYSIS_SYSTEM = """你是资深投标文件结构分析专家。
基于已提取的招标约束信息，判断招标文件要求的投标文件组成结构。

## 核心概念

投标文件通常按"分卷"组织。常见分卷结构：
- **技术标+商务标**（最常见）：技术方案/管理方案等归入技术标，报价/资质/财务等归入商务标
- **资格标+技术标+商务标**：资格审查内容单独成卷
- **仅技术标**：评分标准只有技术维度，商务内容很少或合并在技术标内
- **不分卷（unified）**：招标文件未区分技术/商务，所有内容统一编排

## 判定规则

1. 评分标准中出现"技术标""技术评审""技术部分""技术得分"等 -> has_technical=true
2. 评分标准中出现"商务标""商务评审""报价部分""价格评审""价格得分" -> has_commercial=true
3. 存在独立的"资格标""资格审查"且与技术/商务并列 -> has_qualification=true
4. 招标文件未区分技术/商务，所有要求统一编排 -> structure_mode="unified"
5. 目录规则中明确写了"技术标部分""商务标部分"等分卷标题时，直接采纳
6. 固定模板、递交/密封/电子投标文件要求、格式规定中出现"技术标""商务标""资格标""报价部分"等分卷线索时，也必须纳入判断

## structure_mode 取值
- tech_only：仅要求技术标
- comm_only：仅要求商务标（极少见）
- tech_comm：技术标 + 商务标
- qual_tech_comm：资格标 + 技术标 + 商务标
- unified：不分卷统一编排

## 输出字段说明

- fixed_prefix_items：所有分卷之前的固定项（投标函、授权委托书、保证金缴纳证明等）
- technical_sections：技术标应包含的主要章节名称
- commercial_sections：商务标应包含的主要章节名称
- qualification_sections：资格标应包含的主要章节名称（仅 qual_tech_comm 时填写）
- appendix_items：所有分卷之后的附录项（承诺书、声明函等）
- reasoning：判定依据简述（一两句话）

## 必填字段（缺一不可，否则响应无效）

1. **structure_mode**（字符串）：必须为以下之一：tech_only / comm_only / tech_comm / qual_tech_comm / unified
2. **has_technical**（布尔值）：是否包含技术标
3. **has_commercial**（布尔值）：是否包含商务标
4. **reasoning**（字符串）：判定依据简述

## 输出格式

严格输出 JSON 对象，必须包含上述 4 个必填字段。仅输出 JSON，不要添加说明文字。

示例输出：
```json
{
  "structure_mode": "tech_comm",
  "has_technical": true,
  "has_commercial": true,
  "has_qualification": false,
  "reasoning": "评分标准明确区分技术标(70分)和商务标(30分)"
}
```"""

BID_STRUCTURE_ANALYSIS_USER = """## 已提取的招标约束信息

### 目录规则
{directory_rules}

### 评分标准
{scoring_criteria}

### 内容要求
{content_requirements}

### 必备材料
{material_requirements}

### 硬性结构线索（固定模板/提交规则/注意事项/格式规定）
{hard_structure_signals}

### 行业画像
{industry_profile}

## 任务
分析上述约束，判断招标文件要求的投标文件分卷结构。
仅输出 JSON 对象，不要添加任何说明文字。"""


def get_bid_structure_analysis_prompt(
    directory_rules: str,
    scoring_criteria: str,
    content_requirements: str,
    material_requirements: str,
    industry_profile: str,
    hard_structure_signals: str = "",
) -> tuple[str, str]:
    """获取标书结构分析 Prompt (Registry 优先)"""
    from app.core.ai.prompt_registry import resolve_system_prompt, resolve_user_template
    node = "writing.bid_structure_analysis"
    system = resolve_system_prompt(node, BID_STRUCTURE_ANALYSIS_SYSTEM)
    user_template = resolve_user_template(node, BID_STRUCTURE_ANALYSIS_USER)
    # Registry 中可能仍是旧模板。新增硬性结构线索是事实性关键输入，
    # 不能因为在线模板未同步而被静默丢弃。
    if "{hard_structure_signals}" not in user_template and hard_structure_signals:
        user_template += (
            "\n\n### 硬性结构线索（固定模板/提交规则/注意事项/格式规定）\n"
            "{hard_structure_signals}"
        )
    user = user_template.format(
        directory_rules=directory_rules or "（未提取到目录规则）",
        scoring_criteria=scoring_criteria or "（未提取到评分标准）",
        content_requirements=content_requirements or "（未提取到内容要求）",
        material_requirements=material_requirements or "（未提取到必备材料）",
        hard_structure_signals=hard_structure_signals or "（未提取到硬性结构线索）",
        industry_profile=industry_profile or "（无行业画像）",
    )
    return system, user


def get_dedup_decision_prompt(
    clauses_json: str, count: int
) -> tuple[str, str]:
    """获取决策模式合并去重 Prompt (Registry 优先)"""
    from app.core.ai.prompt_registry import resolve_system_prompt, resolve_user_template
    node = "writing.clause_dedup_decision"
    system = resolve_system_prompt(node, DEDUP_DECISION_SYSTEM)
    user = resolve_user_template(node, DEDUP_DECISION_USER).format(
        clauses_json=clauses_json,
        count=count,
    )
    return system, user


# ==================== 目录原文结构化提取 ====================

BID_DIRECTORY_EXTRACT_SYSTEM = """你是招标文件结构识别专家。
你的任务是从招标文件中识别是否存在"投标文件格式"或"投标文件目录"章节，
如果存在，逐卷原样提取目录条目，包括所有子层级。

## 识别规则

1. 查找招标文件中标题含"投标文件格式""投标文件目录""目录格式""封面及目录格式""电子投标文件格式"的章节
2. 识别分卷结构:
   - 传统两卷: "技术标"(volume_type=technical) + "商务标"(volume_type=commercial)
   - 三卷或更多: "资格及资信证明部分"(volume_type=qualification) + "报价部分"(volume_type=pricing) + "技术商务部分"(volume_type=technical)
   - 其他分卷格式: 按实际名称提取，volume_type 设为 other
3. 目录条目通常以"一、""二、""1.""(1)""二-1""二-2-1"等编号形式列出
4. 原样保留条目标题文字，不要改写或缩写
5. 如果招标文件中明确要求"投标文件由商务标和技术标组成"但同时给出了具体的条目列表，这就是明确的目录结构
6. 招标文件章标题（如「第六章 投标文件格式」）只用于定位，禁止写入 volume_title 的分卷语义之外的包装节点，也禁止写入 items；无分卷时 volume_title 可保留该章名（系统会省略包装），items 必须从章标题下面的编号条目开始，不要把章内「目录」页收成条目

## 子目录递归规则（关键）

如果目录条目下有明确的子结构，必须用 children 递归表达，不要铺平到父级:
- "二、投标人的资格及资信证明文件"下有"二-1 单位授权书""二-2 证明材料"等子项 -> 这些是 children
- "二-2 证明材料"下又有"二-2-1 资格承诺函""二-2-2 资格证明材料" -> 这些是 children 的 children
- "投标函"内部的"1、确认""2、承诺及声明"如果在招标文件中作为独立章节列出 -> 也是 children
- 判断依据: 招标文件中有独立标题/编号的子章节就是 children，章节内部的正文段落不是

## 关键判断

- has_explicit_directory=true: 招标文件中存在明确的、列出了具体条目的目录格式
- has_explicit_directory=false: 招标文件只是泛泛描述了投标文件应包含的内容，但没有给出具体的逐条目录

## 目录事实优先原则

- 你的首要任务是原样提取目录事实：卷名、条目序号、标题、层级关系。
- 不得为了适配 node_type 改写、缩写或补造目录标题。
- node_type 只是初步建议，后续系统会用规则和人工确认再次分类。
- 如果无法判断 node_type，填 free_writing，并将 node_type_source 设为 fallback。

## node_type 初步建议规则

- fixed_template: 投标函/承诺书/授权书/声明函/法定代表人声明/开标一览表/报价书/报价明细表/保证金/承诺函/响应表
- data_fill: 证书/执照/资质/人员表/业绩表/设备清单/复印件/开户许可证/证明材料/标的说明一览表
- free_writing: 技术方案/实施方案/管理方案/评分资料/其他资料/服务方案

## 输出格式

输出一个 JSON 对象:
```json
{
    "has_explicit_directory": true,
    "volumes": [
        {
            "volume_type": "qualification",
            "volume_title": "资格及资信证明部分",
            "items": [
                {
                    "order": 1,
                    "title": "投标函",
                    "node_type": "fixed_template",
                    "node_type_source": "llm_suggestion",
                    "source_span": "第七章电子投标文件格式",
                    "children": []
                },
                {
                    "order": 2,
                    "title": "投标人的资格及资信证明文件",
                    "node_type": "data_fill",
                    "children": [
                        {"order": 1, "title": "单位授权书", "node_type": "fixed_template", "node_type_source": "llm_suggestion", "source_span": "第七章电子投标文件格式", "children": []},
                        {
                            "order": 2,
                            "title": "证明材料",
                            "node_type": "data_fill",
                            "node_type_source": "llm_suggestion",
                            "source_span": "第七章电子投标文件格式",
                            "children": [
                                {"order": 1, "title": "资格承诺函", "node_type": "fixed_template", "node_type_source": "llm_suggestion", "source_span": "第七章电子投标文件格式", "children": []},
                                {"order": 2, "title": "资格证明材料", "node_type": "data_fill", "node_type_source": "llm_suggestion", "source_span": "第七章电子投标文件格式", "children": []}
                            ]
                        }
                    ]
                },
                {"order": 3, "title": "投标保证金", "node_type": "fixed_template", "children": []}
            ]
        },
        {
            "volume_type": "pricing",
            "volume_title": "报价部分",
            "items": [...]
        },
        {
            "volume_type": "technical",
            "volume_title": "技术商务部分",
            "items": [...]
        }
    ],
    "reasoning": "在第七章电子投标文件格式中找到了资格及资信证明部分、报价部分、技术商务部分三卷目录"
}
```

仅输出 JSON 对象，不要添加任何说明文字。"""

BID_DIRECTORY_EXTRACT_USER = """## 招标文件内容

{content}

## 任务
从上述招标文件内容中识别是否存在明确的投标文件目录格式要求。
如果存在，逐卷原样提取目录条目。
章标题（如「第六章 投标文件格式」「电子投标文件格式」）只用于定位，禁止写入 items，也禁止当作分卷包装；无分卷时 volume_title 可保留该章名。
items 必须从章标题下面的编号条目开始，不要把章内「目录」页收成条目。
目录标题和层级必须忠实于原文；node_type 只是初步建议，不能影响目录事实。
仅输出 JSON 对象，不要添加任何说明文字。"""


def get_bid_directory_extract_prompt(content: str) -> tuple[str, str]:
    """获取目录原文提取 Prompt (Registry 优先)"""
    from app.core.ai.prompt_registry import resolve_system_prompt, resolve_user_template
    node = "writing.bid_directory_extract"
    system = resolve_system_prompt(node, BID_DIRECTORY_EXTRACT_SYSTEM)
    user = resolve_user_template(node, BID_DIRECTORY_EXTRACT_USER).format(
        content=content,
    )
    return system, user
