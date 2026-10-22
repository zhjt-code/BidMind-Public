"""素材-章节句对蕴含裁决 Prompt 模板。

对一批"素材-章节"候选对逐对裁决素材是否真正属于该章节，输出四类关系:
supports / partial / unrelated / conflict。

背景:
- 旧匹配靠多路召回 + 字面命中直接绑定，"通用证明"的高相似近邻被当作有效绑定。
- 本节点以语义蕴含裁决收敛精度: 仅字面重叠不成立 supports；通用证明只对最精确章节
 判 supports，对宽泛分类/汇总章节判 partial/unrelated；证件主体不同严禁互判 supports。

Registry 接入在 material_matching/pair_judge.py 完成(call_with_fallback)，本模块只提供
代码默认模板与格式化函数。
"""

MATERIAL_PAIR_JUDGE_SYSTEM = """你是投标素材归属裁决专家。
给定一批"素材-章节"候选对，逐对判断素材是否真正属于该章节。

裁决关系(relation)四选一:
- supports: 素材正是该章节所需内容，业务类型/用途/主体一致，且该章节是素材最精确的归属
- partial: 素材与章节话题相关，但该章节过于宽泛、是汇总或分类目录，或存在更精确的兄弟章节更适合该素材
- unrelated: 素材与章节仅标题或关键词字面重叠，无实质业务关联
- conflict: 素材的证件/主体类型与章节明确冲突(如身份证进入营业执照章节)

裁决铁律:
1. 仅标题或关键词字面重叠不构成 supports；必须业务语义一致才可 supports
2. 通用证明(如"资质证书""认证证书""扫描件")易与多个章节字面相似，只能对最具体章节判 supports，
   对宽泛分类/汇总章节(如"资质材料汇总""证明材料")判 partial 或 unrelated
3. 同一素材若多个候选章节都相关，优先选择粒度最细、指向最明确的章节为 supports，其余判 partial
4. 证件主体不同(个人身份证 vs 法人证照 vs 企业资质)严禁互判 supports
5. evidence_quote 必须取自素材或章节的给定信息，不得臆造"""

MATERIAL_PAIR_JUDGE_USER = """## 候选素材-章节对(JSON)

{pairs_json}

## 任务

逐对裁决 relation。严格按以下 JSON 格式输出:

```json
{{
    "decisions": [
        {{
            "pair_id": "m691_n6481",
            "relation": "supports",
            "confidence": 0.9,
            "evidence_quote": "素材业务类型与章节需求一致的关键信息",
            "reason": "一句话裁决理由"
        }}
    ]
}}
```

- 必须为列表中的**每一个** pair_id 输出恰好一条裁决，pair_id 必须与输入完全一致
- relation 只能是 supports/partial/unrelated/conflict 之一
- confidence 为 0.0~1.0 的数值
- 仅输出 JSON 对象，不要输出任何解释或 Markdown 以外的文字"""


def get_material_pair_judge_prompt(pairs_json: str) -> tuple[str, str]:
    """获取句对裁决 Prompt (代码默认; Registry 接入在 pair_judge.py 的包装函数完成)。"""
    return (
        MATERIAL_PAIR_JUDGE_SYSTEM,
        MATERIAL_PAIR_JUDGE_USER.format(pairs_json=pairs_json),
    )
