"""三元匹配 Prompt 模板

章节拉取式架构:
  - CHAPTER_JUDGE: 章节驱动的约束裁决(Phase 2 核心)

注: 原 MATCH_JUDGE (单项匹配判断, 低置信补充) 已被 writing.chapter_judge 统一替代,
对应 seed 节点 writing.match_judge 在 DEPRECATED_NODE_KEYS 中 soft-deactivate。
"""

# ==================== Phase 2: 章节拉取裁决 ====================

CHAPTER_JUDGE_SYSTEM = """你是投标文件章节匹配裁决专家。
根据章节的语义画像，从候选约束中精准筛选出真正属于该章节的约束。

裁决铁律:
1. 只有「必须在本章节中体现或遵守」的约束才应选入
2. 仅因共享高频词(如"投标""文件""要求")而出现的候选，如果约束内容与本章节的semantic_role无实质关联，应拒绝。但如果约束内容明确提到本章节名称（如"见投标函""投标函附录"），即使含高频词也应保留。
3. 废标条款: 只有当本章节是该废标条件的直接约束对象时才选入
4. 格式模板约束: 只分配到与模板名称对应的章节
5. 参考 rejects 列表，命中 rejects 的候选大概率应拒绝
6. 宁可保留一条有疑问的约束(让用户审核)，也不要遗漏真正相关的约束"""

CHAPTER_JUDGE_USER = """## 当前章节

标题: {chapter_title}
类型: {chapter_type}
路径: {chapter_path}
语义角色: {semantic_role}
期望内容: {expects}
排除内容: {rejects}

## 候选约束列表

{candidates_text}

## 任务

从候选约束中选出真正属于本章节的约束。

严格按以下 JSON 数组格式输出:
```json
[
    {{
        "clause_id": 12,
        "should_bind": true,
        "confidence": 0.92,
        "reason": "直接要求使用固定模板"
    }}
]
```

- 只输出 should_bind=true 的项；未选中的候选不要出现
- confidence: 置信度 0.0~1.0
- reason: 不超过 40 字

仅输出 JSON 数组，不要添加任何说明文字。"""


def get_chapter_judge_prompt(
    chapter_title: str,
    chapter_type: str,
    chapter_path: str,
    semantic_role: str,
    expects: str,
    rejects: str,
    candidates_text: str,
) -> tuple[str, str]:
    """获取章节裁决 Prompt (代码默认; Registry 接入在 material_matcher.py 的包装函数中完成)"""
    return (
        CHAPTER_JUDGE_SYSTEM,
        CHAPTER_JUDGE_USER.format(
            chapter_title=chapter_title,
            chapter_type=chapter_type,
            chapter_path=chapter_path,
            semantic_role=semantic_role,
            expects=expects,
            rejects=rejects,
            candidates_text=candidates_text,
        ),
    )
