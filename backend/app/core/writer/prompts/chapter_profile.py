"""章节画像 Prompt 模板

目录生成后，为每个叶子章节生成语义画像，用于匹配阶段的
精准裁决。画像包含:
  - semantic_role: 该章节在标书中的功能定位
  - expects: 期望出现的约束/素材类型
  - rejects: 明确不应出现的内容类型
"""

CHAPTER_PROFILE_SYSTEM = """你是投标文件结构分析专家。
根据目录树的结构和章节类型，为每个叶子章节生成精确的语义画像。

画像的核心价值：帮助系统精准判断「哪些招标约束属于哪个章节」。
- expects 应聚焦于：本章节必须遵守或体现的约束类型
- rejects 应聚焦于：容易被误匹配到本章节但实际无关的内容类型"""

CHAPTER_PROFILE_USER = """## 目录树结构

{tree_text}

## 行业画像 (摘录)

{industry_profile_excerpt}

## 本卷约束分布 (按 sub_type 聚合)

{clause_domain_summary}

## 具体约束示例（优先参考）

{clause_examples_summary}

## 需要生成画像的叶子章节

{leaf_list}

## 任务

为「需要生成画像的叶子章节」列表中的**每一个**章节生成语义画像。严格按以下 JSON 数组格式输出:

输出边界（必须遵守）:
- **覆盖完整性**：输出数组长度必须等于「需要生成画像的叶子章节」列表的条目数，禁止省略任何一个章节，禁止只挑选部分章节生成
- **node_id 准确**：每条画像的 `node_id` 必须与输入列表中的 `ID=` 完全对应，不得新造或修改
- 顶层必须是 JSON 数组 `[...]`
- 即使本卷只有一个叶子章节，也必须用数组包裹（输出 `[{{...}}]`），禁止直接输出单个对象 `{{...}}`
- 禁止输出 `{{"profiles": [...]}}`、`{{"items": [...]}}`、`{{"data": [...]}}` 等包裹对象
- 禁止输出 Markdown 代码块以外的解释、前言或总结

```json
[
    {{
        "node_id": 5,
        "semantic_role": "一句话描述该章节的功能定位(如'按招标方模板原样填写的格式化文件')",
        "expects": ["期望匹配的约束类型1", "期望匹配的约束类型2"],
        "rejects": ["不应出现的内容类型1", "不应出现的内容类型2"]
    }}
]
```

### 画像编写规则

1. **semantic_role**: 用一句自然语言描述章节用途，区分于兄弟章节
2. **expects**: 列出 2-5 个该章节真正需要的约束/素材类别，优先参考"本卷约束分布"中实际存在的类别。
   - fixed_template 类型章节：expects 应只包含对应模板的格式/填写要求
   - free_writing 类型章节：expects 应包含评分标准、技术要求等
   - data_fill 类型章节：expects 应包含资料清单、证明材料要求等
   - 优先从“具体约束示例”中提炼，不要只按通用章节名泛化
3. **rejects**: 列出 3-8 个容易被误匹配但实际不属于本章节的内容类别。
   例如"投标函"应 reject "技术方案/业绩证明/人员资质/报价明细"

仅输出 JSON 数组，不要添加任何说明文字或对象包裹。"""


def get_chapter_profile_prompt(
    tree_text: str,
    leaf_list: str,
    industry_profile_excerpt: str = "",
    clause_domain_summary: str = "",
    clause_examples_summary: str = "",
) -> tuple[str, str]:
    """获取章节画像生成 Prompt (Registry 优先)

    Args:
        tree_text: 完整目录树（含层级缩进）
        leaf_list: 需要画像的叶子章节列表
        industry_profile_excerpt: P2.3 行业画像摘录, 缺失时用占位
        clause_domain_summary: P2.3 本卷约束按 sub_type 的计数摘要

    Returns:
        tuple[str, str]: (system_message, user_message)
    """
    from app.core.ai.prompt_registry import resolve_system_prompt, resolve_user_template
    node = "writing.chapter_profile"
    system = resolve_system_prompt(node, CHAPTER_PROFILE_SYSTEM)
    try:
        user_template = resolve_user_template(node, CHAPTER_PROFILE_USER)
        # Registry 中可能仍是旧 active 模板。具体约束示例是画像质量关键输入，
        # 不能因在线模板未同步而被静默丢弃。
        if "{clause_examples_summary}" not in user_template and clause_examples_summary:
            user_template += "\n\n## 具体约束示例（优先参考）\n\n{clause_examples_summary}"
        user = user_template.format(
            tree_text=tree_text,
            leaf_list=leaf_list,
            industry_profile_excerpt=industry_profile_excerpt or "(无行业画像)",
            clause_domain_summary=clause_domain_summary or "(无约束分布摘要)",
            clause_examples_summary=clause_examples_summary or "(无具体约束示例)",
        )
    except (KeyError, IndexError):
        # Registry 模板占位符错误时回退到代码默认, 防生产崩溃
        user = CHAPTER_PROFILE_USER.format(
            tree_text=tree_text,
            leaf_list=leaf_list,
            industry_profile_excerpt=industry_profile_excerpt or "(无行业画像)",
            clause_domain_summary=clause_domain_summary or "(无约束分布摘要)",
            clause_examples_summary=clause_examples_summary or "(无具体约束示例)",
        )
    return system, user
