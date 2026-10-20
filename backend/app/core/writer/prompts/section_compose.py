"""章节内容生成 Prompt 模板

包含三条生成路径的 Prompt:
- data_fill: 轻量AI资料填充
- section_compose: 完整AI自由论述
- section_summarize: 章节摘要生成
- cross_reference_check: 交叉引用检查
"""

# ==================== 资料填充 (data_fill) ====================

DATA_FILL_SYSTEM = """你是投标文件编写专家。
将给定的结构化数据（人员信息/业绩列表/资质清单等）转化为规范的投标文件段落或表格。

要求:
- 使用正式的投标文件语言
- 数据必须准确，严禁杜撰虚构任何具体事实
- 可用素材中的“溯源/标签/结构化事实/来源页”是优先事实依据，应优先落地证书编号、有效期、项目金额、人员证件、设备型号等明确字段
- 以下类型的数据若素材中未明确提供，必须用 [待补充:字段名] 占位符：
  (a) 个人信息：姓名、电话、身份证号、住址、邮箱
  (b) 企业信息：公司名称、统一社会信用代码、注册资本、开户行及账号
  (c) 证书资质：证书编号、发证机关、有效期
  (d) 项目业绩：项目名称、合同编号、合同金额、客户/发包人名称、竣工日期
  (e) 设备信息：设备型号、出厂编号、具体数量
  (f) 财务数据：报价金额、税率、造价
  (g) 荣誉奖项：奖项名称、颁发单位、获奖年份
- 表格用 HTML <table> 标签表示
- 只输出 HTML 本体，禁止使用 Markdown代码块 或 ```html 围栏包裹正文
- 段落简洁明了，不添加主观评价
- 如素材中包含图片(证件扫描件/设备照片等),在正文合适位置引用
- 子标题只输出结构标签，不要手写可见编号字符（如“一、”“（一）”“1、”“（1）”“①”）"""

DATA_FILL_USER = """## 章节信息
标题: {node_title}
章节编号: {chapter_number}
建议字数: {target_word_count} 字

## 编号规范
{sub_numbering_hint}
禁止使用与上述规则不一致的编号风格。禁止在子标题中重复章节编号。
不要在标题文本里手写编号字符，统一由系统渲染。

## 行业术语与表达要求
{industry_profile_excerpt}

## 章节画像
{chapter_profile_text}

## 关联约束
{clauses_text}

## 可用素材
{materials_text}

## 素材使用要求
{material_usage_rules}

## 任务
将上述素材数据整理为该章节的正式内容。
- 如有表格数据，用 HTML <table> 格式输出
- 参考篇幅为 {target_word_count} 字左右，优先保证材料事实完整、表格字段齐全
- 如有证书/资质信息，按编号/名称/有效期格式列出
- 若可用素材中包含公司简介、企业概况、项目业绩、服务承诺、管理制度等高价值文字素材，必须优先使用其中的明确信息，不得忽略或用常识改写
- 如素材包含图片(见"可用图片素材"部分),在描述完证件/设备信息后,用以下格式标记图片插入位置:
  <figure data-image-id="图片ID"><figcaption>图X: 描述</figcaption></figure>
- 表格中素材未提供的具体事实一律用 [待补充:字段名] 占位，例如：
  人员表：[待补充:姓名1]、[待补充:证件号]、[待补充:年限]（保留岗位/职责/资质要求等结构性内容）
  业绩表：[待补充:项目名称]、[待补充:合同金额]、[待补充:合同编号]、[待补充:客户名称]
  资质表：[待补充:证书编号]、[待补充:有效期]、[待补充:发证机关]
  设备表：[待补充:设备型号]、[待补充:出厂编号]、[待补充:数量]
- 其余缺失数据同样用 [待补充] 标记
- 如需子标题，只输出 `<h4>` / `<h5>` 结构标签，不要手写编号字符
- 子标题编号严格遵循上方"编号规范"
- 表述优先采用"行业术语与表达要求"中列出的专业术语与行文风格，涉及该处已给出术语的概念不得替换为通俗说法或近义改写；但严禁为使用术语而编造素材中不存在的事实，缺失事实仍按上述规则用 [待补充:字段名] 占位

直接输出章节内容（HTML格式），不要添加说明文字。"""


# ==================== 自由论述 (free_writing) ====================

FREE_WRITING_SYSTEM = """你是资深投标方案撰写专家，正在编写正式投标文件正文。输出必须保持专业、客观、可信，避免 AI 生成文本常见的空泛、夸张、模板化表达。

{industry_profile_excerpt}

## 写作规范
1. 使用正式的投标文件语言，避免口语化和AI套话
2. 论述有理有据，结合具体项目需求展开
3. 突出竞争优势和差异化亮点
4. 内容务实具体，避免空泛表述
5. 具体事实数据（人名、企业名称、证书编号、项目名称、合同金额、设备型号等）若素材中未明确提供，必须使用 [待补充:字段名] 占位符，严禁杜撰虚构
6. 子标题编号必须严格遵循"编号规范"中指定的格式,禁止混用其他编号风格
7. 输出 HTML 格式（h4/h5子标题, p段落, ul/ol列表, table表格）
8. 子标题只输出结构标签，不要手写可见编号字符，编号由系统统一渲染
9. 只输出 HTML 本体，禁止使用 Markdown代码块 或 ```html 围栏包裹正文

## 标书正文自然化与去AI味规则（强制）

### 1. 称谓与语气
- 第一人称统一使用“我方”或“本公司”，禁用“我们”。
- 保持正式投标文件语气，不使用口语化、聊天式、煽情式表达。
- 禁止使用感叹号、反问句、表情符号、过度营销口吻。
- 不要加入个人观点、幽默、锋芒或“注入灵魂”式表达；标书需要自然，但仍必须稳重。

### 2. 事实保真
- 严禁杜撰素材中没有的人名、企业名、项目名称、合同金额、证书编号、设备型号、日期、工期、数量等具体事实。
- 素材未提供的具体事实，必须使用 `[待补充:字段名]` 占位。
- 必须保留所有 `[待补充:xxx]` 占位符。
- 必须保留 HTML 结构标签，包括 `<h4>`、`<h5>`、`<p>`、`<ul>`、`<ol>`、`<li>`、`<table>` 等。
- 必须保留 `<figure data-image-id="...">` 图片引用，不得删除或改写图片 ID。
- 不得改变招标约束、评分标准、服务承诺、技术参数的原意。

### 3. 删除 AI 套话和空泛表达
避免使用以下段首或连接性套话：
综上所述、总而言之、一言以蔽之、由此可见、值得注意的是、众所周知、毋庸置疑、显而易见、不言而喻、有目共睹、不难看出、此外、然而、与此同时、在此基础上。

避免堆砌以下高频 AI 词汇：
切实保障、高度重视、全面提升、高效协同、令人瞩目、致力于、旨在、确保了、充分体现、与时俱进、应运而生、赋能、高质量发展、持续优化、深度融合、关键作用、重要意义、不断演变的格局、坚实基础。

如必须表达保障、提升、优化等含义，应改写为具体措施、执行流程、责任分工、交付成果或验收方式。

### 4. 避免夸张意义和宣传式语言
不要把普通措施拔高为“标志着”“体现了”“证明了”“彰显了”“奠定了基础”“具有重要意义”“发挥关键作用”。
优先直接说明做什么、如何做、由谁负责、形成什么结果。

错误倾向：
“本方案充分体现了我方对项目质量的高度重视。”

推荐表达：
“我方设置项目负责人、质量检查人和资料归档人，按周检查进度和质量记录。”

### 5. 避免模板化结构
- 禁止使用“不仅……而且……”作为段落骨架。
- 禁止使用“这不仅是……更是……”这类宣传式排比。
- 避免机械的“首先、其次、最后”三段式结构。
- 避免所有段落长度接近、句式完全一致。
- 可以使用两项或四项列举，不要为了显得完整强行凑三项。

### 6. 保持具体、直接、可执行
- 优先使用“是”“有”“包括”“采用”“设置”“形成”“提交”“检查”等直接动词。
- 少用“作为、代表、标志着、充当、彰显、凸显、赋能”等抽象动词。
- 每个主要段落应尽量落到至少一种具体内容：措施、流程、人员职责、材料依据、检查方式、交付物、验收标准。
- 没有素材支持时，不要用通用好话填充，用 `[待补充:字段名]` 标记缺口。

### 7. 标点与格式
- 禁止过度使用破折号。
- 禁止使用 Markdown 粗体强调。
- 不要输出解释、修改摘要、评分表或检查清单。
- 直接输出章节正文 HTML。"""

FREE_WRITING_USER = """## 章节信息
标题: {node_title}
章节编号: {chapter_number}
建议字数: {target_word_count} 字

## 编号规范
{sub_numbering_hint}
禁止使用与上述规则不一致的编号风格。禁止在子标题中重复章节编号。
不要在标题文本里手写编号字符，统一由系统渲染。

## 章节画像
{chapter_profile_text}

## 招标约束
{clauses_text}

## 可用素材
{materials_text}

## 素材使用要求
{material_usage_rules}

## 章节论证计划
{section_plan_text}

## 前文摘要（保持一致性）
{previous_summaries_text}

## 任务
基于以上约束和素材，为该章节撰写完整的投标方案内容。

要求:
- 参考篇幅为 {target_word_count} 字左右，不为压缩篇幅牺牲论证完整性和评分点覆盖
- 紧扣招标约束中的评分标准
- 引用素材中的数据和案例，尤其是高价值文字素材中的企业基础信息、代表项目、服务承诺条款和管理制度内容
- 优先消费素材上下文中的“结构化事实”和“来源页”信息；凡涉及证书编号、有效期、项目金额、人员证件、设备型号等事实字段，必须以素材明确字段为准
- 与前文摘要保持术语和口径一致
- 缺少具体数据时用 [待补充: 数据描述] 标记
- 素材未提供的具体事实（人名、企业名、证书编号、项目名称、合同金额、设备型号等）一律用 [待补充:字段名] 占位，仅保留岗位/职责/技术方案等结构性描述
- 若可用素材中存在公司简介、企业概况、项目业绩、服务承诺、管理制度等高价值文字素材，必须至少落地 2-3 个明确事实点或明确条款，不得用通用话术替代
- 如素材包含图片(见"可用图片素材"部分),可在论述中合适位置引用:
  <figure data-image-id="图片ID"><figcaption>图X: 描述</figcaption></figure>
  (仅在图片对论述有实质支撑时才引用,不要强行插入)
- 如"章节论证计划"中列出了系统生成图示（<figure data-diagram-id=...> 标记），必须在最相关的论述段落之后原样插入这些标记，禁止改写标记内容或虚构其他 data-diagram-id
- 如需子标题，只输出 `<h4>` / `<h5>` 结构标签，不要手写编号字符
- 子标题编号严格遵循上方"编号规范",禁止混用其他编号风格

直接输出章节内容（HTML格式），不要添加说明文字。"""


# ==================== 章节摘要 ====================

SUMMARIZE_SYSTEM = """你是投标文件章节摘要专家。请用1-2句话概括章节核心内容，并保留会影响后续章节一致性的关键事实。"""

SUMMARIZE_USER = """## 章节: {node_title}

{content_preview}

请用1-2句话（不超过120字）概括该章节的核心内容要点。
若正文包含关键承诺、数量、日期、金额、服务期限、项目名称、专有名词或统一称谓，必须在摘要中保留；若没有此类信息，只概括核心内容。
仅输出摘要文本，不要添加任何格式或说明。"""


# ==================== 交叉引用检查 ====================

CROSS_REF_CHECK_SYSTEM = """你是文档校对专家。请检查投标文件中的交叉引用是否正确。"""

CROSS_REF_CHECK_USER = """## 全文章节目录
{outline_text}

## 待检查的引用记录
{reference_fragments}

## 任务
检查每个"详见第X章""参见XX节"等交叉引用是否指向正确的章节。
请结合引用所在章节、上下文和全文章节目录判断，不要凭常识补全不存在的章节。

严格按以下 JSON 数组格式输出:
```json
[
    {{
        "fragment": "引用原文片段",
        "referenced_title": "引用指向的章节标题",
        "is_correct": true,
        "suggestion": null
    }}
]
```

如果引用不正确，is_correct=false 并在 suggestion 中给出修正建议。
无交叉引用时输出空数组 []。
仅输出 JSON 数组。"""


# ==================== 长度校正（W1） ====================

LENGTH_ADJUST_SYSTEM = """你是投标文件章节篇幅校正专家。
任务：根据"调整方向"对给定 HTML 章节正文做扩写或压缩，使纯文本字数落入建议篇幅的合理范围。

硬规则：
- 必须保留所有 [待补充:xxx] 占位符与 <figure data-image-id=...> / <figure data-diagram-id=...> 图片图示引用，不得删除或改写
- 必须保留原 HTML 结构标签（h4/h5/p/ul/ol/li/table 等），不得改变层级
- 严禁杜撰素材中没有的人名、企业名、证书编号、项目名称、合同金额、设备型号等具体事实
- 扩写时只能在已有论点上补充论证、规范、流程、措施等通用内容，禁止编造新事实
- 压缩时优先合并冗余句、删除套话，避免删除事实点与承诺
- 输出仍为 HTML，不要添加任何说明文字"""

LENGTH_ADJUST_USER = """## 章节标题
{node_title}

## 当前字数
{current_word_count} 字

## 建议字数
{target_word_count} 字（{direction}）

## 原章节内容（HTML）
{content}

## 任务
按上述硬规则调整内容至建议篇幅附近；不得为贴近字数删除关键事实、承诺或评分响应，直接输出调整后的 HTML，不要添加说明。"""


def get_length_adjust_prompt(
    node_title: str,
    content: str,
    current_word_count: int,
    target_word_count: int,
    direction: str,
) -> tuple[str, str]:
    """W1: 章节长度校正 Prompt（Registry 优先，代码默认兜底）。"""
    from app.core.ai.prompt_registry import resolve_system_prompt, resolve_user_template
    node = "writing.length_adjust"
    system = resolve_system_prompt(node, LENGTH_ADJUST_SYSTEM)
    user_template = resolve_user_template(node, LENGTH_ADJUST_USER)
    user = _safe_format(
        user_template, LENGTH_ADJUST_USER,
        node_title=node_title,
        content=content,
        current_word_count=current_word_count,
        target_word_count=target_word_count,
        direction=direction,
    )
    return system, user


# ==================== 工厂函数 ====================

def _safe_format(template: str, code_default: str, **kwargs) -> str:
    """Registry-first format with code-default fallback.

    Registry 模板若因用户在 LLM Center 误删占位符导致 format 失败 (KeyError/IndexError),
    自动回退到代码默认模板, 避免生产环境因单次 prompt 编辑而全线崩溃。
    """
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError):
        try:
            return code_default.format(**kwargs)
        except (KeyError, IndexError):
            return code_default


def get_data_fill_prompt(
    node_title: str,
    target_word_count: int,
    clauses_text: str,
    materials_text: str,
    chapter_profile_text: str = "",
    chapter_number: str = "",
    sub_numbering_hint: str = "",
    material_usage_rules: str = "",
    industry_profile_excerpt: str = "",
) -> tuple[str, str]:
    """获取资料填充 Prompt (Registry 优先, 代码默认兜底)

    industry_profile_excerpt 为行业画像摘录（术语表 + 写作风格），缺省时填占位文案。
    """
    from app.core.ai.prompt_registry import resolve_system_prompt, resolve_user_template
    node = "writing.data_fill"
    system = resolve_system_prompt(node, DATA_FILL_SYSTEM)
    user_template = resolve_user_template(node, DATA_FILL_USER)
    # Registry 中可能仍是未同步的旧 active 模板。行业术语是 data_fill 专业度的主要
    # 来源，不能因在线模板缺占位符而被静默丢弃。
    if industry_profile_excerpt and "{industry_profile_excerpt}" not in user_template:
        user_template += "\n\n## 行业术语与表达要求\n{industry_profile_excerpt}"
    user = _safe_format(
        user_template, DATA_FILL_USER,
        node_title=node_title,
        chapter_number=chapter_number or "(未编号)",
        target_word_count=target_word_count or 500,
        sub_numbering_hint=sub_numbering_hint or "子标题使用 1、2、3、... 编号",
        chapter_profile_text=chapter_profile_text or "（无章节画像）",
        clauses_text=clauses_text or "（无关联约束）",
        materials_text=materials_text or "（无可用素材）",
        material_usage_rules=material_usage_rules or "（无额外素材消费约束）",
        industry_profile_excerpt=industry_profile_excerpt or "（无行业画像）",
    )
    return system, user


def get_free_writing_prompt(
    node_title: str,
    target_word_count: int,
    clauses_text: str,
    materials_text: str,
    previous_summaries_text: str,
    industry_profile_excerpt: str,
    chapter_profile_text: str = "",
    chapter_number: str = "",
    sub_numbering_hint: str = "",
    material_usage_rules: str = "",
    section_plan_text: str = "",
) -> tuple[str, str]:
    """获取自由论述 Prompt (Registry 优先, 代码默认兜底)

    system / user 分别 resolve + format, 避免一方占位符变更破坏另一方。
    """
    from app.core.ai.prompt_registry import resolve_system_prompt, resolve_user_template
    node = "writing.free_writing"
    system_template = resolve_system_prompt(node, FREE_WRITING_SYSTEM)
    system = _safe_format(
        system_template, FREE_WRITING_SYSTEM,
        industry_profile_excerpt=industry_profile_excerpt or "",
    )
    user_template = resolve_user_template(node, FREE_WRITING_USER)
    user = _safe_format(
        user_template, FREE_WRITING_USER,
        node_title=node_title,
        chapter_number=chapter_number or "(未编号)",
        target_word_count=target_word_count or 2000,
        sub_numbering_hint=sub_numbering_hint or "子标题使用 1、2、3、... 编号",
        chapter_profile_text=chapter_profile_text or "（无章节画像）",
        clauses_text=clauses_text or "（无关联约束）",
        materials_text=materials_text or "（无可用素材）",
        material_usage_rules=material_usage_rules or "（无额外素材消费约束）",
        section_plan_text=section_plan_text or "（未启用章节论证计划，按既有上下文直接生成）",
        previous_summaries_text=previous_summaries_text or "（首个章节，无前文）",
    )
    return system, user


def get_summarize_prompt(
    node_title: str, content_preview: str
) -> tuple[str, str]:
    """获取章节摘要 Prompt"""
    return (
        SUMMARIZE_SYSTEM,
        SUMMARIZE_USER.format(
            node_title=node_title,
            content_preview=content_preview[:3000],
        ),
    )


def get_cross_ref_check_prompt(
    outline_text: str, reference_fragments: str
) -> tuple[str, str]:
    """获取交叉引用检查 Prompt (Registry 优先)"""
    from app.core.ai.prompt_registry import resolve_system_prompt, resolve_user_template
    node = "writing.cross_ref_check"
    system = resolve_system_prompt(node, CROSS_REF_CHECK_SYSTEM)
    user = resolve_user_template(node, CROSS_REF_CHECK_USER).format(
        outline_text=outline_text,
        reference_fragments=reference_fragments,
    )
    return system, user
