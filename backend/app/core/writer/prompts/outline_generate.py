"""目录树生成 Prompt 模板

包含三套 Prompt:
- OUTLINE_GENERATE_*: 原始整体生成(unified 模式回退)
- SECTION_FILL_*: 自拟模式分卷填充(无明确目录时只生成技术标+商务标)
- SKELETON_ENRICH_*: 照搬模式骨架节点丰富(招标文件有明确目录时)
"""

# ==================== Unified 模式回退 Prompt ====================

OUTLINE_GENERATE_SYSTEM = """你是资深投标方案策划师，擅长设计投标文件的目录结构。
你的目标是生成一个严格满足招标要求、并在允许范围内突出竞争优势的目录树。

## 事实边界（不可违反）

- 目录规则、评分标准、内容要求、材料要求和项目背景是唯一依据。
- 未被招标约束支撑的章节不得新增；不确定内容放入“其他资料/其他响应”类兜底节点。
- 不得为了显得完整或专业而虚构招标文件未要求的章节。
- 不得改变招标文件已经明确规定的目录事实。

## node_type 判定规则

每个节点必须分配一个类型:
- fixed_template: 固定模板章节(投标函/承诺书/授权书/法定代表人声明等)
- data_fill: 资料填充章节(人员配置表/业绩汇总表/设备清单/报价表/资质文件/证书)
- free_writing: 自由论述章节(技术方案/实施方案/服务方案/项目理解/管理方案)

## 输出格式约束

输出一个 JSON 数组，每个元素代表一个目录节点:
```json
[
    {
        "title": "章节标题",
        "depth": 0,
        "sort_order": 0,
        "node_type": "free_writing",
        "is_required": 1,
        "score_weight": null,
        "target_word_count": null,
        "children": [
            {
                "title": "子章节标题",
                "depth": 1,
                "sort_order": 0,
                "node_type": "free_writing",
                "is_required": 1,
                "score_weight": 5.0,
                "target_word_count": 1800,
                "children": []
            }
        ]
    }
]
```

注意:
- depth 从 0 开始（顶级节点）
- sort_order 在同级节点中递增
- score_weight 仅在有评分标准对应时填写（从评分标准约束中获取）
- target_word_count 仅叶子节点需要填写建议字数；非叶子节点填 null
- fixed_template 章节填 null；data_fill 建议 300-3000；free_writing 建议 500-5000
- is_required: 1=必填, 0=选填
- children 为空时用 []"""

OUTLINE_GENERATE_USER = """## 招标约束信息

### 目录规则
{directory_rules}

### 内容要求
{content_requirements}

### 必备材料
{material_requirements}

### 评分标准
{scoring_criteria}

### 行业画像（参考）
{industry_profile}

## 任务
基于以上招标约束，生成完整的投标文件目录树。

要求:
1. 严格遵循目录规则中的章节结构要求
2. 确保所有必备材料都有对应章节
3. 为每个评分标准关联到对应章节并设置 score_weight
4. 合理安排章节深度（建议最多3级）
5. 为每个叶子节点正确分配 node_type
6. 为每个叶子节点给出合理的 target_word_count 建议字数
7. 未被招标约束支撑的章节不得新增；不确定内容统一放入兜底节点

仅输出 JSON 数组，不要添加任何说明文字。"""


def get_outline_generate_prompt(
    directory_rules: str,
    content_requirements: str,
    material_requirements: str,
    scoring_criteria: str,
    industry_profile: str,
) -> tuple[str, str]:
    """获取目录树生成 Prompt (unified 回退)"""
    return (
        OUTLINE_GENERATE_SYSTEM,
        OUTLINE_GENERATE_USER.format(
            directory_rules=directory_rules or "（未提取到目录规则）",
            content_requirements=content_requirements or "（未提取到内容要求）",
            material_requirements=material_requirements or "（未提取到必备材料）",
            scoring_criteria=scoring_criteria or "（未提取到评分标准）",
            industry_profile=industry_profile or "（无行业画像）",
        ),
    )


# ==================== 自拟模式: 分卷填充 Prompt ====================

SECTION_FILL_SYSTEM = """你是资深投标方案策划师，擅长设计投标文件的目录结构。

## 核心约束（不可违反）
未被招标约束支撑的章节不得新增；不确定内容放入“其他资料/其他响应”类兜底节点。
投标函、授权书、保证金证明、资质证书等必须归属于当前结构模式下的相应分卷，不可跨分卷重复生成。

你的任务是为投标文件中的指定分卷生成完整子目录。
你只需要输出该分卷下的章节，不要生成其他分卷的内容。

## node_type 判定规则

每个节点必须分配一个类型:
- fixed_template: 固定模板章节(投标函/承诺书/授权书/法定代表人声明/开标一览表/报价书)
- data_fill: 资料填充章节(人员配置表/业绩汇总表/设备清单/报价表/资质文件/证书/执照)
- free_writing: 自由论述章节(技术方案/实施方案/服务方案/项目理解/管理方案)

## 输出格式

输出一个 JSON 数组，每个元素代表该分卷下的一个子章节:
```json
[
    {{
        "title": "章节标题",
        "depth": 0,
        "sort_order": 0,
        "node_type": "free_writing",
        "is_required": 1,
        "score_weight": null,
        "target_word_count": 1800,
        "children": []
    }}
]
```

注意:
- depth 从 0 开始（相对于本分卷）
- sort_order 在同级节点中递增
- score_weight 仅在有评分标准对应时填写
- target_word_count 仅叶子节点需要填写建议字数；非叶子节点填 null
- fixed_template 章节填 null；data_fill 建议 300-3000；free_writing 建议 500-5000
- is_required: 1=必填, 0=选填
- children 为空时用 []
- 建议最多2级子章节深度"""

SECTION_FILL_USER = """## 与"{section_title}"相关的招标约束

### 当前分卷策略
{section_policy}

### 当前分卷定位
{section_description}

### 目录规则
{relevant_dir_rules}

### 内容要求
{relevant_content_reqs}

### 评分标准
{relevant_scoring}

### 必备材料
{relevant_materials}

### 行业画像（参考）
{industry_profile}

## 任务
为"{section_title}"生成完整子目录树。

要求:
1. 只生成属于"{section_title}"的章节
2. 投标函、授权书、保证金证明等必须包含在本分卷之内（如适用）
3. 确保所有与本分卷相关的必备材料都有对应章节
4. 为每个评分标准关联到对应章节并设置 score_weight
5. 合理安排章节深度（建议最多2级子章节）
6. 为每个叶子节点正确分配 node_type
7. 为每个叶子节点给出合理的 target_word_count 建议字数
8. 最大限度覆盖招标要求中与本分卷相关的所有内容，防止遗漏导致废标
9. 未被招标约束支撑的章节不得新增；不确定内容统一放入兜底节点

仅输出 JSON 数组，不要添加任何说明文字。"""

_SECTION_DESCRIPTIONS = {
    "technical": (
        "技术标部分通常包含: 投标函、资质证书、法定代表人证明及授权委托书、"
        "营业执照、公司简介与人员配置、技术方案/实施方案、设备清单、"
        "工期保障、质量保证、HSE管理方案、以及招标文件要求的其他技术性内容。"
    ),
    "commercial": (
        "商务标部分通常包含: 投标函、开标一览表、投标报价书/报价明细、"
        "开户许可证、财务证明、以及招标文件要求的其他商务性内容。"
    ),
}

_SECTION_POLICIES = {
    "tech_comm": "当前结构模式为技术标+商务标；仅生成当前分卷下的章节，不得新增第三个独立分卷。",
    "qual_tech_comm": "当前结构模式包含资格标、技术标、商务/报价相关分卷；仅生成当前分卷下的章节，资格证明、技术方案、报价商务内容不得互相压扁或混放。",
    "tech_only": "当前结构模式以技术标为主；仅生成技术标内部章节，不得新增独立商务标或资格标。",
    "comm_only": "当前结构模式以商务标为主；仅生成商务标内部章节，不得新增独立技术标或资格标。",
    "unified": "当前结构模式为不分卷统一编排；仅生成当前章节下的子章节，不得新增独立分卷。",
}


def get_section_fill_prompt(
    section_title: str,
    section_category: str,
    relevant_dir_rules: str,
    relevant_content_reqs: str,
    relevant_scoring: str,
    relevant_materials: str,
    industry_profile: str,
    structure_mode: str = "tech_comm",
    section_policy: str = "",
) -> tuple[str, str]:
    """获取自拟模式分卷填充 Prompt"""
    description = _SECTION_DESCRIPTIONS.get(
        section_category, f"本部分包含与{section_title}相关的所有内容。"
    )
    policy = section_policy or _SECTION_POLICIES.get(
        structure_mode,
        "按上游结构分析结果生成当前分卷内部章节，不得新增未被招标约束支撑的独立分卷。",
    )
    return (
        SECTION_FILL_SYSTEM,
        SECTION_FILL_USER.format(
            section_title=section_title,
            section_description=description,
            section_policy=policy,
            relevant_dir_rules=relevant_dir_rules or "（无相关规则）",
            relevant_content_reqs=relevant_content_reqs or "（无相关要求）",
            relevant_scoring=relevant_scoring or "（无相关评分标准）",
            relevant_materials=relevant_materials or "（无相关材料要求）",
            industry_profile=industry_profile or "（无行业画像）",
        ),
    )


# ==================== 照搬模式: 骨架节点丰富 Prompt ====================

SKELETON_ENRICH_SYSTEM = """你是资深投标方案策划师。
你的任务是为投标文件中的一个骨架章节补充子目录。

## 背景
招标文件明确规定了投标文件的目录结构。当前章节是招标文件指定的骨架条目，标题不可更改。
你需要在这个条目下补充子目录，帮助后续内容生成更加精细和有针对性。

## 重要规则
1. 子目录必须基于招标文件中明确提到的要求来组织，不要凭空编造
2. 如果该章节只需提供单份文件（如证书扫描件、执照复印件），则不需要子目录，返回空数组 []
3. 如果该章节涉及评分标准，必须为每个评分项创建对应的子条目
4. 如果该章节需要编写方案（如实验方案、管理方案），应按招标文件中的具体要求拆分为子项
5. 不要把采购需求、研究内容、建设内容、服务事项拆成子目录；该项由后续步骤挂载
6. 未被招标约束支撑的子章节不得新增；不确定内容放入当前骨架节点自身或兜底子项

## node_type 判定规则
- fixed_template: 固定模板(投标函/承诺书/授权书等)
- data_fill: 资料填充(证书/表格/清单等)
- free_writing: 自由论述(方案/分析/说明等)

## 输出格式
输出一个 JSON 数组:
```json
[
    {{
        "title": "子章节标题",
        "depth": 0,
        "sort_order": 0,
        "node_type": "free_writing",
        "is_required": 1,
        "score_weight": null,
        "target_word_count": 1200,
        "children": []
    }}
]
```
如果不需要子目录，返回 []。
- target_word_count 仅叶子节点需要填写建议字数；非叶子节点填 null
- fixed_template 章节填 null；data_fill 建议 300-3000；free_writing 建议 500-5000
仅输出 JSON 数组，不要添加任何说明文字。"""

SKELETON_ENRICH_USER = """## 当前骨架章节
标题: "{node_title}"
所属分卷: {volume_title}
章节序号: 第{node_order}项

## 同卷其他骨架章节（内容不可重复）
{sibling_titles_text}
本章节的子目录不得与上述章节的主题重复。已被其他章节覆盖的内容不要在此出现。

## 与本章节相关的招标信息

### 评分标准
{relevant_scoring}

### 内容要求
{relevant_content_reqs}

### 必备材料
{relevant_materials}

### 项目工作内容
{project_context}

## 任务
为"{node_title}"补充子目录。
- 如果只需提供单份文件，返回空数组 []
- 如果涉及评分项，按评分表逐项展开
- 如果需要编写方案，按招标文件中的具体测试/工作项目拆分
- 不要包含已被同卷其他骨架章节覆盖的内容
- 为每个叶子节点给出合理的 target_word_count 建议字数
- 未被招标约束支撑的子章节不得新增

仅输出 JSON 数组，不要添加任何说明文字。"""


def get_skeleton_enrich_prompt(
    node_title: str,
    node_order: int,
    volume_title: str,
    relevant_scoring: str,
    relevant_content_reqs: str,
    relevant_materials: str,
    project_context: str,
    sibling_titles_text: str = "",
) -> tuple[str, str]:
    """获取照搬模式骨架节点丰富 Prompt"""
    return (
        SKELETON_ENRICH_SYSTEM,
        SKELETON_ENRICH_USER.format(
            node_title=node_title,
            node_order=node_order,
            volume_title=volume_title,
            sibling_titles_text=sibling_titles_text or "（无其他章节）",
            relevant_scoring=relevant_scoring or "（无相关评分标准）",
            relevant_content_reqs=relevant_content_reqs or "（无相关要求）",
            relevant_materials=relevant_materials or "（无相关材料要求）",
            project_context=project_context or "（无项目背景信息）",
        ),
    )
