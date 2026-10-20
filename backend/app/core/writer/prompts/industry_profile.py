"""行业画像生成 Prompt 模板"""

PROFILE_OUTPUT_CONTRACT_MARKER = "industry_profile_output_contract:v1"

PROFILE_SECTION_HEADINGS: tuple[str, ...] = (
    "### 1. 行业识别与典型特征",
    "### 2. 专业术语表（20-30个）",
    "### 3. 技术方案典型结构",
    "### 4. 评标专家核心关注点（5-8个）",
    "### 5. 写作风格指导",
    "### 6. 差异化竞争建议",
)

INDUSTRY_PROFILE_OUTPUT_CONTRACT = f"""## 输出契约（{PROFILE_OUTPUT_CONTRACT_MARKER}）
这是写标风格指南，不是行业调研、市场分析或项目可行性报告。
禁止改写成「项目背景」「市场分析」「产品竞争力」等长文结构。

只输出以下六个 Markdown 三级标题，标题文字必须与下列原文完全一致：
{chr(10).join(PROFILE_SECTION_HEADINGS)}

禁止手写可见编号：一、（一）1.1 3.1 ①。节内列表只用 Markdown `- `。
每节正文不超过 250 字；术语表 20-30 条、每条一行。
六节必须写完。写到第 6 节结束即停，不要总结、不要开新节。"""

INDUSTRY_PROFILE_SYSTEM = """你是行业分析专家和资深投标顾问。
根据招标文件的约束信息，生成一份精准的行业写作画像，
指导标书编写的语言风格、技术深度和差异化策略。

输出必须是可直接入库、可直接展示的正文，禁止任何对话式开场白、
身份介绍、任务复述、解释性过渡或总结性客套话。

禁止出现类似表达：
- 好的，……
- 作为行业分析专家……
- 我已根据您提供的……
- 以下为您生成……
- 下面是……

正文必须直接从结构化内容开始。
""" + INDUSTRY_PROFILE_OUTPUT_CONTRACT

INDUSTRY_PROFILE_COMPLETION_SYSTEM = """你在补全一份未完成的行业写作画像。
只输出行业画像正文，不要解释。
必须遵守六段 Markdown 契约，禁止手写「一、」「（一）」「3.1」。
""" + INDUSTRY_PROFILE_OUTPUT_CONTRACT

INDUSTRY_PROFILE_USER = """## 已提取的招标约束摘要

### 项目背景
{project_context}

### 评分标准要点
{scoring_summary}

### 技术/内容要求要点
{content_requirements}

### 必备材料清单
{material_requirements}

### 差异化提示
{differentiation_hints}

### 目录/结构规则
{directory_rules}

### 结构化信号摘要
{structured_signals}

## 任务
基于以上招标约束信息，生成行业写作画像。请按以下六个部分输出纯文本（不需要 JSON）:

### 1. 行业识别与典型特征
识别该项目所属行业，列出该行业投标文件的典型特征和常见结构。

### 2. 专业术语表（20-30个）
列出该行业/项目类型中必须使用的专业术语，用于确保标书的专业度。

### 3. 技术方案典型结构
基于评分标准和内容要求，建议技术方案章节的推荐结构和重点。

### 4. 评标专家核心关注点（5-8个）
推断评标专家最关注的方面，按重要性排序。

### 5. 写作风格指导
建议标书的整体语言风格、论述深度、数据引用策略。

### 6. 差异化竞争建议
基于评分标准中的加分项和差异化提示，建议突出的竞争优势方向。

事实边界:
- 只能把上方约束中明确出现的信息写成招标事实。
- 对未明确出现但有助于写作的内容，只能表述为“建议突出/可考虑”，不能写成招标方已要求。
- 不得虚构行业、资质、业绩、技术指标或评分偏好。

直接输出正文内容，不要添加任何引言、寒暄、角色说明、任务复述或结束语。
输出必须从“### 1. 行业识别与典型特征”开始，不得在此标题前输出任何文字。

""" + INDUSTRY_PROFILE_OUTPUT_CONTRACT + """

输出骨架（按此标题原文起笔，不要改写标题）:

### 1. 行业识别与典型特征

### 2. 专业术语表（20-30个）

### 3. 技术方案典型结构

### 4. 评标专家核心关注点（5-8个）

### 5. 写作风格指导

### 6. 差异化竞争建议
"""


def get_industry_profile_prompt(
    project_context: str,
    scoring_summary: str,
    content_requirements: str,
    material_requirements: str,
    differentiation_hints: str = "",
    directory_rules: str = "",
    structured_signals: str = "",
) -> tuple[str, str]:
    """获取行业画像生成 Prompt

    Args:
        project_context: 项目背景约束汇总
        scoring_summary: 评分标准要点汇总
        content_requirements: 技术/内容要求要点汇总
        material_requirements: 必备材料清单汇总

    Returns:
        tuple[str, str]: (system_message, user_message)
    """
    return attach_output_contract(
        INDUSTRY_PROFILE_SYSTEM,
        INDUSTRY_PROFILE_USER.format(
            project_context=project_context or "（未提取到项目背景信息）",
            scoring_summary=scoring_summary or "（未提取到评分标准）",
            content_requirements=content_requirements or "（未提取到内容要求）",
            material_requirements=material_requirements or "（未提取到材料清单）",
            differentiation_hints=differentiation_hints or "（未提取到差异化提示）",
            directory_rules=directory_rules or "（未提取到目录/结构规则）",
            structured_signals=structured_signals or "（无结构化信号摘要）",
        ),
    )


def attach_output_contract(system_message: str, user_message: str) -> tuple[str, str]:
    """Registry 旧模板若未带契约标记，则把输出契约挂到 user 末尾。"""
    combined = f"{system_message or ''}\n{user_message or ''}"
    if PROFILE_OUTPUT_CONTRACT_MARKER in combined:
        return system_message, user_message
    return system_message, f"{(user_message or '').rstrip()}\n\n{INDUSTRY_PROFILE_OUTPUT_CONTRACT}"
