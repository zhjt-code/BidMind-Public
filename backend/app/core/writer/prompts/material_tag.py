"""素材元数据打标 Prompt 模板

支持三种素材类型的定制化打标:
- text: 通用文本类素材
- image: 图片类素材(含VL视觉识别结果)
- table: 表格类素材(含结构化数据)

打标输出包含:
- domain: 领域分类
- bid_purpose: 该素材在投标中的业务用途
- exclusive_section: 排他性章节(仅属于唯一章节时填写)
- applicable_sections: 适用章节列表(从目录中精确选择,最多2个)
- inapplicable_sections: 明确不适用的章节类型
- entities: 关键实体
- keywords: 关键词
- confidence: 打标置信度(0-1)
- evidence_source: 标签依据来源(llm/fallback_keyword/image_skipped/resolved_label_bias)
- tag_basis: 一句话说明标签依据
"""

MATERIAL_TAG_SYSTEM = """你是投标素材分类专家。
根据素材的标题、类型、内容摘要和标书目录结构，对素材进行精确的领域分类和章节定位。

核心原则:
1. applicable_sections 必须从标书目录中精确选择，禁止凭通用知识猜测
2. 每个素材最多适用于 1-2 个章节，禁止输出超过 2 个
3. 如果素材明确只属于一个特定章节（如身份证→法定代表人身份证明书），必须填写 exclusive_section
4. 必须明确列出该素材不应出现的章节类型
5. 目录缺失、目录过粗或无法精确选择章节时，禁止自创章节名称"""

MATERIAL_TAG_USER = """## 素材信息

素材标题: {title}
素材类型: {material_type}
素材内容摘要:
{content_preview}

## 标书目录结构

{outline_tree}

## 任务

对该素材进行分类打标。**必须从上述目录结构中精确选择适用章节**。

严格按以下 JSON 格式输出:

```json
{{
    "domain": "该素材所属领域(如: 人员配置/项目业绩/企业资质/技术方案/设备清单/财务资料/管理制度/其他)",
    "bid_purpose": "一句话描述该素材在投标中的业务用途(如: 法定代表人身份证明材料/企业质量管理体系认证/项目经理工作简历)",
    "exclusive_section": "如果该素材只应出现在一个特定章节,填写目录中的精确章节名称;否则填null",
    "applicable_sections": ["从上述目录中精确选择最适用的1-2个章节名称"],
    "inapplicable_sections": ["明确不适用的章节类型,至少3个(如: 技术方案/施工组织设计/报价)"],
    "entities": ["提取的关键实体(人名/公司名/证书名/项目名/设备型号),最多10个"],
    "keywords": ["5-10个关键词"],
    "confidence": 0.85,
    "evidence_source": "llm",
    "tag_basis": "一句话说明依据了素材标题、内容摘要或目录中的哪些信息"
}}
```

### 打标规则
- exclusive_section: 身份证件/授权委托书/特定承诺书等只属于一个章节的素材,必须填写
- applicable_sections: 严格从目录中选择,禁止自创章节名称,最多2个
- 如果目录为“目录尚未生成”、目录过粗或没有可精确选择的章节: exclusive_section 填 null, applicable_sections 填 [], 并在 tag_basis 中说明“目录不确定”
- inapplicable_sections: 写出该素材明显不属于的章节类型,帮助后续匹配排除误命中
- 若素材业务类型为"公司简介/企业简介/企业概况/单位简介"且素材类型为 text:
  - `domain` 优先归为"企业概况"或"企业介绍"，不得归为"企业资质"
  - 不应填写 `exclusive_section`
  - `bid_purpose` 应聚焦企业基本情况、主营业务、综合实力或代表项目介绍
- 若素材业务类型为"项目业绩综述/项目经验/类似项目/业绩概述"且素材类型为 text:
  - `domain` 优先归为"项目业绩"
  - `bid_purpose` 应聚焦代表项目、服务规模、履约成果或项目经验综述
  - `applicable_sections` 优先选择与项目业绩、类似经验、履约成果相关的章节
- 若素材业务类型为"服务承诺/售后服务承诺/响应承诺/保障措施"且素材类型为 text:
  - `domain` 优先归为"服务承诺"
  - `bid_purpose` 应聚焦响应时效、服务机制、保障措施或违约责任
  - 除非目录中存在唯一明确章节，否则不应填写 `exclusive_section`
- 若素材业务类型为"管理制度/制度汇编/管理办法/质量管理制度/安全管理制度"且素材类型为 text:
  - `domain` 优先归为"管理制度"
  - `bid_purpose` 应聚焦制度名称、流程机制、责任分工、执行与监督要求
  - `applicable_sections` 优先选择质量管理、安全管理、内部控制、组织管理类章节

仅输出 JSON 对象，不要添加任何说明文字。"""


MATERIAL_TAG_IMAGE_USER = """## 图片素材信息

素材标题: {title}
素材类型: 图片(已通过视觉模型识别)

### 视觉模型识别结果
{content_preview}

## 标书目录结构

{outline_tree}

## 任务

根据上述视觉模型识别结果，对该图片素材进行分类打标。**必须从上述目录结构中精确选择适用章节**。

重点关注:
- 如果是证书/执照/资质证明: 提取证书名称、编号、持证人、有效期
- 如果是业绩证明/合同: 提取项目名称、金额、签约方
- 如果是人员证件: 提取姓名、职称/职位、证书类型
- 如果是设备照片: 提取设备名称、型号、规格

严格按以下 JSON 格式输出:

```json
{{
    "domain": "该素材所属领域(如: 企业资质/人员资质/项目业绩/设备清单/安全生产/环保证明/其他)",
    "bid_purpose": "一句话描述该图片在投标中的业务用途",
    "exclusive_section": "如果该图片只属于一个特定章节,填写目录中的精确章节名称;否则填null",
    "applicable_sections": ["从上述目录中精确选择最适用的1-2个章节名称"],
    "inapplicable_sections": ["明确不适用的章节类型,至少3个"],
    "entities": ["从VL识别结果中提取的关键实体(证书名/编号/人名/公司名/项目名),最多10个"],
    "keywords": ["5-10个关键词,需包含证书类型、持证主体等"],
    "confidence": 0.85,
    "evidence_source": "llm",
    "tag_basis": "一句话说明依据了视觉识别结果和目录中的哪些信息"
}}
```

### 打标规则
- 身份证/身份证明 → exclusive_section 填"法定代表人身份证明书"或对应人员章节
- 单一证书(如营业执照) → exclusive_section 填对应资质章节
- 体系认证(如三体系) → 可能适用2个章节,但不应超过2个
- 人员照片/证件 → exclusive_section 填对应人员章节
- 如果目录为“目录尚未生成”、目录过粗或没有可精确选择的章节: exclusive_section 填 null, applicable_sections 填 [], 并在 tag_basis 中说明“目录不确定”

仅输出 JSON 对象，不要添加任何说明文字。"""


MATERIAL_TAG_TABLE_USER = """## 表格素材信息

素材标题: {title}
素材类型: 表格/数据

### 表格内容
{content_preview}

## 标书目录结构

{outline_tree}

## 任务

根据上述表格数据，对该素材进行分类打标。**必须从上述目录结构中精确选择适用章节**。

重点关注:
- 表格的列名/表头: 识别数据维度
- 数据行内容: 提取关键实体(人名/项目名/设备型号/金额等)
- 统计信息: 如有合计行，提取汇总数据

严格按以下 JSON 格式输出:

```json
{{
    "domain": "该素材所属领域(如: 人员配置/设备清单/报价明细/项目业绩/物资采购/财务资料/其他)",
    "bid_purpose": "一句话描述该表格在投标中的业务用途",
    "exclusive_section": "如果该表格只属于一个特定章节,填写目录中的精确章节名称;否则填null",
    "applicable_sections": ["从上述目录中精确选择最适用的1-2个章节名称"],
    "inapplicable_sections": ["明确不适用的章节类型,至少3个"],
    "entities": ["从表格中提取的关键实体(人名/设备名称/项目名称/供应商),最多10个"],
    "keywords": ["5-10个关键词,需包含表格主题、关键列名等"],
    "confidence": 0.85,
    "evidence_source": "llm",
    "tag_basis": "一句话说明依据了表格标题、表头或目录中的哪些信息"
}}
```

### 打标规则
- 如果目录为“目录尚未生成”、目录过粗或没有可精确选择的章节: exclusive_section 填 null, applicable_sections 填 [], 并在 tag_basis 中说明“目录不确定”
- 禁止自创章节名称

仅输出 JSON 对象，不要添加任何说明文字。"""


_RESOLVED_LABEL_SECTION = """
## 素材业务类型(系统预判)

{resolved_label_text}

注意: 如果系统预判的业务类型与你根据内容判断的一致, 优先使用该类型对应的章节进行定位。
如果不一致, 以实际内容为准, 但请在 bid_purpose 中说明差异。"""

_NO_RESOLVED_LABEL = ""


def _build_resolved_label_block(resolved_label: str) -> str:
    """构建 resolved_label 的 Prompt 段落"""
    if not resolved_label:
        return _NO_RESOLVED_LABEL
    return _RESOLVED_LABEL_SECTION.format(
        resolved_label_text=f"系统已识别该素材为: **{resolved_label}**"
    )


def get_material_tag_prompt(
    title: str,
    material_type: str,
    content_preview: str,
    outline_tree: str = "",
    resolved_label: str = "",
) -> tuple[str, str]:
    """获取素材打标 Prompt

    根据 material_type 路由到对应的专用模板:
    - image: 使用 MATERIAL_TAG_IMAGE_USER，强调从VL识别结果提取信息
    - table: 使用 MATERIAL_TAG_TABLE_USER，强调从表格结构提取信息
    - 其他: 使用通用 MATERIAL_TAG_USER

    Args:
        title: 素材标题
        material_type: 素材类型 (text/image/table/...)
        content_preview: 内容摘要（由调用方完成预算控制，最多保留约4000字）
        outline_tree: 标书目录树文本（缩进格式）
        resolved_label: 标签决议器产出的最终业务类型标签

    Returns:
        tuple[str, str]: (system_message, user_message)
    """
    safe_title = title or "（无标题）"
    safe_content = content_preview[:4000] if content_preview else "（无内容）"
    safe_outline = outline_tree or "（目录尚未生成）"
    label_block = _build_resolved_label_block(resolved_label)

    if material_type == "image":
        user_msg = MATERIAL_TAG_IMAGE_USER.format(
            title=safe_title,
            content_preview=safe_content,
            outline_tree=safe_outline,
        )
        if label_block:
            user_msg = user_msg + "\n" + label_block
        return MATERIAL_TAG_SYSTEM, user_msg
    elif material_type == "table":
        user_msg = MATERIAL_TAG_TABLE_USER.format(
            title=safe_title,
            content_preview=safe_content,
            outline_tree=safe_outline,
        )
        if label_block:
            user_msg = user_msg + "\n" + label_block
        return MATERIAL_TAG_SYSTEM, user_msg
    else:
        user_msg = MATERIAL_TAG_USER.format(
            title=safe_title,
            material_type=material_type or "text",
            content_preview=safe_content,
            outline_tree=safe_outline,
        )
        if label_block:
            user_msg = user_msg + "\n" + label_block
        return MATERIAL_TAG_SYSTEM, user_msg
