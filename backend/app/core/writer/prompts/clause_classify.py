"""约束作用域分类 Prompt 模板

匹配阶段 Phase 1: 对全部已确认约束做作用域分类，
决定每条约束参与匹配的方式:
  - global: 全局约束(适用所有章节，跳过章节匹配)
  - exclusive: 专属约束(锁定唯一章节，排他性)
  - volume_shared: 分卷级共享约束(适用同一分卷下的所有章节)
  - shared: 共享约束(可匹配1-3个章节)
  - irrelevant: 与编写无关(事务性通知，直接丢弃)
"""

CLAUSE_CLASSIFY_SYSTEM = """你是投标文件约束分类专家。
根据约束的内容和类型，判断每条约束的「作用域」。

作用域定义:
- global: 全局约束，适用于标书所有章节。典型类型: 字体/排版格式要求、页边距行距等版面规定。这类约束不需要分配到具体章节。
- exclusive: 专属约束，仅适用于一个特定章节，且具有排他性。典型类型: 固定模板(投标函模板、承诺书模板、授权委托书模板)。一旦锁定到目标章节，其他章节不应再出现此约束。
- volume_shared: 分卷级共享约束，适用于同一分卷(如技术标或商务标)下的所有自由论述章节。典型类型: 技术方案评分标准、通用技术要求、质量管理要求、安全管理要求等。
- shared: 共享约束，可能与1-3个具体章节相关。典型类型: 针对具体内容的废标条款、针对具体材料的要求。
- irrelevant: 与标书编写无关的事务性通知，如递交时间/地点/联系方式/开标安排/踏勘通知/保证金缴纳说明/邮箱地址/截止日期等。这类信息在素材管理页的"注意事项"中已单独展示，不应参与章节匹配。"""

CLAUSE_CLASSIFY_USER = """## 候选章节与分卷

{outline_candidates_text}

## 待分类的约束列表

{clauses_text}

## 任务

对每条约束判定作用域。严格按以下 JSON 数组格式输出:

```json
[
    {{
        "clause_id": 1,
        "scope": "global",
        "target_hint": null,
        "reason": "字体格式要求适用于所有章节"
    }}
]
```

### 字段说明
- clause_id: 约束ID(必须与输入一致)
- scope: "global" / "exclusive" / "volume_shared" / "shared" / "irrelevant"
- target_hint:
  - exclusive 时必须填写“候选章节与分卷”中真实存在的章节标题(如"投标函")；没有明确候选则填 null
  - volume_shared 时必须填写候选分卷类型(如"technical"或"commercial")；没有明确候选则填 null
  - 其他填 null
- reason: 一句话分类理由

### 分类规则
1. format_rule 类型(字体/排版/页面格式) → global
2. fixed_template 类型 → exclusive，target_hint 填对应模板名称
3. scoring_criterion(评分标准) → 看约束范围：如果是通用评分规则(如"技术方案总分60分")→ volume_shared(target_hint="technical")；如果是针对具体内容的评分细则 → shared
4. content_required(内容要求) → 看约束范围：如果对同类型所有章节生效(如"所有技术方案须包含实施计划") → volume_shared；如果针对特定章节 → shared
5. disqualification(废标条款) → 看约束对象：如果约束对象是具体文件(如"投标函") → exclusive；如果是通用行为(如"逾期递交") → shared
6. material_required → shared
7. 判断标准：如果该约束删除后不影响标书正文内容的编写 → irrelevant
8. 不确定时默认 shared
9. 禁止凭经验编造章节名或分卷名；target_hint 必须来自候选列表或为 null

仅输出 JSON 数组，不要添加任何说明文字。"""


def get_clause_classify_prompt(
    clauses_text: str,
    outline_candidates_text: str = "",
) -> tuple[str, str]:
    """获取约束分类 Prompt"""
    return (
        CLAUSE_CLASSIFY_SYSTEM,
        CLAUSE_CLASSIFY_USER.format(
            clauses_text=clauses_text,
            outline_candidates_text=outline_candidates_text or "（未提供候选章节，无法确定时请默认 shared）",
        ),
    )
