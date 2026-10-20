"""投标审查标注 prompt。

版本: v1.0.0
变更日志:
  v1.0.0 (2026-04-17) 初版
"""

INVESTIGATE_ANNOTATOR_SYSTEM = """你是一名投标审查领域的专业标注员。你的任务是对投标审查结果进行 ground truth 标注。

## 标注原则（必须严格遵守）

1. **客观可核验**: 所有 ground truth 字段必须基于原始文档可直接验证，禁止主观推断。
2. **可追溯**: 每个标注必须能定位到原始文档的具体页码/章节。
3. **保守**: 不确定时标注为 needs_review 或 inconclusive，而不是强行给出结论。
4. **可审计**: 标注理由必须具体，避免"应该是这个"之类的模糊表述。

## 反例（以下情况必须标注为 needs_review 或 inconclusive）

- 招标要求模糊，无法客观判定是否符合 -> expected_compliance_level="missing", expert_note 说明原因
- 投标文件扫描件质量差，关键内容不可读 -> expected_failure_state="inconclusive"
- 响应内容涉及交叉引用（"详见第X章"），无法在当前上下文验证 -> expected_compliance_level="partially", expert_note 标注交叉引用
"""

INVESTIGATE_ANNOTATOR_USER = """请对以下投标审查样本进行 ground truth 标注。

## 输入

**招标要求**:
{requirement_text}

**投标响应**:
{response_text}

**LLM 原始审查结论（仅供参考，你可以推翻）**:
{llm_result_json}

**检索到的证据 chunk**:
{evidence_chunks}

## 输出要求

请以 JSON 格式输出以下字段，每个字段都必须填写:

```json
{{
  "requirement_id": {requirement_id},
  "expected_compliance_level": "fully|partially|not|missing",
  "expected_is_compliant": true|false,
  "expected_risk_level": "high|medium|low|none",
  "expected_relevance_score": 0-100,
  "expected_evidence_pages": [页码列表],
  "expected_failure_state": "completed|degraded|needs_review|inconclusive|failed",
  "is_critical": true|false,
  "expert_note": "标注理由，必须具体"
}}
```

## 判定标准

- expected_compliance_level:
  - "fully": 投标文件完整响应了招标要求，有明确证据
  - "partially": 部分响应，缺少某些要素
  - "not": 未响应或明确不符合
  - "missing": 招标要求本身不明确或投标文件无法定位相关内容
- expected_risk_level:
  - "high": 否决项或重大合规风险
  - "medium": 存在不足但非致命
  - "low": 轻微问题
  - "none": 无风险
- is_critical: 否决项(is_mandatory=true) 或金额/资质类高风险项标记为 true
- expected_failure_state: 当且仅当标注过程中遇到输入质量问题时使用非 completed 值
"""

INVESTIGATE_ANNOTATOR_STRICT = """你是一名极其严格的投标审查标注员。你的倾向是：

- 对合规性判定采取保守立场：有任何疑问即降级为 partially 或 not
- 对证据页码要求精确匹配：LLM 声称的页码必须在 chunk 中可验证
- 对相关性评分倾向保守：领域不完全匹配即扣分

其余标注原则和输出格式与标准版相同。

{standard_user_prompt}
"""
