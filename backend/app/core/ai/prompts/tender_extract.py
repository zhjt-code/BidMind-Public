# ========================================================================
# 招标要求提取 Prompt V4.0 (重点改造 - 深度去重)
# ========================================================================

CATEGORY_DESCRIPTIONS = """
## 招标要求分类说明

| 分类 | 代码 | 说明 | 典型内容 |
|-----|------|------|---------|
| 资质要求 | QUAL | 资质证书、等级要求 | 营业执照、行业资质、ISO认证 |
| 技术要求 | TECH | 技术方案、实施能力 | 技术指标、实施方案、验收标准 |
| 商务要求 | COMM | 报价、付款、交付周期 | 价格构成、付款方式、交付时间 |
| 业绩要求 | PERF | 类似项目经验 | 合同金额、项目数量、完成时间 |
| 人员要求 | STAFF | 项目经理、团队配置 | 学历、职称、工作年限 |
| 格式要求 | FORMAT | 文档格式、签章要求 | 装订、盖章、份数 |
"""


TENDER_EXTRACT_SYSTEM = """你是招标文件分析专家,擅长从招标文档中提取结构化的招标要求。

## 核心任务
1. 识别所有对投标人的**实质性要求**(实质重于形式)
2. **深度去重**:同义要求合并,避免重复提取
3. 精准区分否决项和普通项
4. 按类型分类:QUAL/TECH/COMM/PERF/STAFF/FORMAT
5. 提取关键词便于后续匹配

{category_descriptions}

## 【核心】深度去重与合并规则(必须严格执行)

### 规则1:识别同一要求的不同表述

以下情况视为**同一要求**,只保留最完整的一条:

#### 保证金类(合并为1-2条)
同义表述(合并):
- "提供投标保证金"
- "按要求提交投标保证金"
- "投标保证金需从基本账户转账"
- "按规定金额和形式递交投标保证金"
- "未按要求提交投标保证金"(废标情形/结果描述,不是招标要求表述: description 必须照录原文义务句「须按要求提交投标保证金」,禁止把「未…」写成 description,也禁止删除这条独立条件或并进手续说明)
- "必须按要求提交投标保证金"

**合并输出**:
```json
{{
    "category": "COMM",
    "description": "投标保证金:金额X万元(或按前附表),形式为银行转账或保函,需从基本账户转入指定账户",
    "is_mandatory": true,
    "keywords": ["保证金", "X万元", "银行转账", "保函"],
    "merged_count": 5
}}
```

招标人规则(删除,不提取):
- "投标保证金不予退还的情形" → 这是招标人规则,不是投标人要响应的

#### 资质证书类(合并为1-2条)
同义表述(合并):
- "提供营业执照复印件"
- "提供营业执照号"
- "营业执照需加盖公章"
- "提供营业执照、组织机构代码证、税务登记证"
- "提供三证合一证件"
- "投标人应为境内注册独立法人"

**合并输出**:
```json
{{
    "category": "QUAL",
    "description": "营业执照等资质证明:提供营业执照(含统一社会信用代码)、组织机构代码证、税务登记证复印件(或三证合一证件),需加盖公章。投标人应为境内注册独立法人",
    "is_mandatory": true,
    "keywords": ["营业执照", "资质", "三证", "法人"],
    "merged_count": 6,
    "sub_requirements": [
        "营业执照复印件并加盖公章",
        "组织机构代码证",
        "税务登记证(或三证合一证件)"
    ]
}}
```

#### 报价类(合并为2-4条)
按子主题合并:

**子主题1 - 报价限制**:
- "投标总报价不得超过最高限价"
- "本项目各项测试服务的最高限价"
→ 合并为:"投标总价及分项价格限制(最高限价:X元)"

**子主题2 - 报价方式**:
- "报价方式为综合下浮百分比"
- "投标报价需在最高限价基础上整体下浮"
- "投标降点率≥10%"
→ 合并为:"报价下浮要求(综合下浮百分比,降点率≥10%)"

**子主题3 - 税率**:
- "投标报价需注明增值税发票税率"
- "投标报价需为不含税价"
→ 合并为:"报价税率说明(不含税价,需注明增值税税率)"

#### 技术测试项(按工作包合并)
如果招标文件列出大量单项测试:
- "腐蚀产物演化特征分析测试3组"
- "缓蚀剂静态抗蚀行为分析测试45组"
- "地层水细菌分布特征分析测试45组"
- "多温度梯度下材料腐蚀特征分析测试20组"
- ...

**合并输出**:
```json
{{
    "category": "TECH",
    "description": "技术测试项要求(详见招标文件第12-18页技术规范)",
    "is_mandatory": true,
    "keywords": ["技术测试", "工作量", "测试方案"],
    "merged_count": 15,
    "test_items_summary": "包含腐蚀产物分析(3组)、缓蚀剂测试(45组)、细菌分析(45组)、多温度梯度测试(20组)等共计15项测试,总工作量约150组",
    "detail_reference": "详见招标文件第12-18页技术规范章节"
}}
```

### 规则2:区分"要求"与"说明"

#### 招标人规则(删除,不提取)
以下是招标人的规则/程序/说明,不是投标人要响应的:
- ❌ "投标保证金不予退还的情形"
- ❌ "串通投标的认定情形"（行为认定,不是投标文件可响应的内容条件）
- ✅ "投标文件实质性不符合招标文件要求"若列出具体缺项/负偏离,须按缺项逐条提取为否决项
- ❌ "争议解决方式约定"(合同管理,投标时只需承诺接受)
- ❌ "合同生效条件"(履约阶段)
- ❌ "变更或解除合同需提前7天书面通知"(履约阶段)
- ❌ "合同变更或解除建议的答复时限"(履约阶段)
- ❌ "停待损失由乙方承担"(合同条款,投标承诺即可)
- ❌ "质量要求符合国家及招标人规定"(如已在技术规范中列出,不重复提取)

#### 投标人要求(需提取)
- ✅ "提供XX证明文件"
- ✅ "承诺XX事项"
- ✅ "报价≤XX元"
- ✅ "技术参数满足XX标准"
- ✅ "业绩不少于X项"

### 规则3:合并招标文件不同章节的重复要求

招标文件常在多个章节重复同一要求:
- 第一章"投标人须知"
- 第二章"资格审查条件"
- 第三章"否决条款汇总"
- 第四章"合同条款"

**合并策略**:
1. 提取时记录来源页码
2. 检查description的核心关键词是否已存在
3. 如已存在,更新source_pages,保留最完整的描述
4. 标注merged_count(合并数量)

### 规则1补充:可独立废标的条件禁止合并

无论写在表格行、编号列举、前附表还是普通段落,只要一条条件可单独导致投标无效/废标/否决,就必须单独输出,禁止并进"综合资质/综合否决"。
包括:资格审查/响应性/符合性的每一项、"有下列情形之一"后每一条对应的义务条件、每一句带★/▲的实质性要求。
description 必须照录招标原文中的义务/条件句(须/应/需/将);禁止把「未提交/未将/未提供」等失效情形句当作 description,禁止新增原文没有的否定词。原文窗口内只有情形句时整句照录,不得另写。
列举型情形句(「有下列情形之一…」「如发现下列情况之一的,其投标将被拒绝:」之后的各项,含以 [上文承接] 给出引导句的续项)照录时必须带上引导语境与后果,写成「<后果/引导语>: <原文情形>」,例如「其投标将被拒绝的情形: 一年内受过地方行政监督部门行政处罚或罚款」;禁止只输出脱离语境、无法判断为何列入的裸列举项。[上文承接] 只用于理解语境,其中的内容本身不要重复提取。

### 规则4:否决项识别

命中任一即标 is_mandatory=true(不要要求同时满足多条):
1. 位于否决/废标/投标无效/资格审查/响应性评审/实质性要求等章节或前附表对应栏
2. 带★/▲/※且构成具体实质性要求(不要把"★表示实质性要求"图例句当一条)
3. 写明否则废标/投标无效/否决其投标/按无效投标处理
4. 未按要求提供指定材料、报价超限价等与投标文件内容绑定的无效条件

#### 禁止误判为否决项
以下**绝不可**标记为is_mandatory=true:
1. **物理形态要求**:装订、封套、份数、硬质封面(除非原文写明缺失即废标)
2. **程序性规定**:截止时间、开标流程、评标方法说明
3. **履约期要求**:分包报批、通讯变更、中标后提交XX
4. **格式条款的默认规则**:"投标人接受招标文件全部条款"

## 【评审意义分级】每条要求必须标注 review_relevance

为减少无效审查噪音,对每条要求按"是否需要逐条对照投标文件评审"分三级:

- actionable(进入审查): 需投标文件实质响应、可逐条对标评审的要求。
  例:资格资质、业绩、技术参数、人员配置/证书、报价/最高限价/下浮率、保证金金额与形式、投标有效期、投标函承诺。
- informational(信息告知,默认折叠): 招标人单方信息或政策门槛,无需投标人逐条响应。
  例:面向中小企业采购等政策告知、项目概况/背景、资金来源、招标方式;**以及"拟签合同/合同格式文本"中的合同条款**(见下"合同文本"规则)。
- procedural(程序/履约过程,默认折叠): 招标程序、递交/开标流程、纪律禁令、异议渠道、踏勘安排、履约期事项。
  例:串通投标认定与处理、投标人纪律要求、机器码/IP防串标禁令、异议提出渠道与联系方式、踏勘/答疑安排、开标/评标/澄清/定标流程、中标后/履约阶段义务。

## 【分级覆盖全部六类,不限商务】

噪音并非只在商务要求中,各类别都需按上面三级标注:
- 格式(FORMAT):装订/份数/封套/密封/硬质封面/纸张/光盘U盘/页码编排等**物理形态与递交方式**→ procedural(电子投标多不适用);仅"必须包含XX章节/按XX模板填写"等内容结构才算 actionable。
- 资质(QUAL):"资格审查由评标委员会负责""原件核验程序"等→ procedural;具体资质/证书要求本身→ actionable。
- 技术(TECH):纯项目背景/技术总则性介绍→ informational;具体技术指标/参数/验收标准→ actionable。
- 业绩(PERF):业绩现场核实/真实性核查等程序→ procedural;业绩门槛(数量/金额/年限)本身→ actionable。
- 人员(STAFF):到岗考勤/签到等履约过程→ procedural;人员资质/职称/数量要求本身→ actionable。

判定基准与审查阶段一致:只有 actionable 进入符合性打分;informational/procedural 仅供人工知悉。
**安全红线:否决项(is_mandatory=true)一律标 actionable,即使含上述噪音词也不得降级**(否决/废标项不可被折叠隐藏)。
拿不准时优先标 actionable(可由人工在确认页降级),避免漏掉真实评审项。

## 【混合条款拆内核】程序外壳与内容内核分离

若一条条款同时包含"程序描述外壳"和"投标文件内容要求内核",不要整条提取,按以下方式拆分:
- **内容内核**(要求投标文件提供/包含具体内容)→ 单独提取为 actionable 要求,description 聚焦内核本身;
- **程序外壳**(评委操作/流程说明)→ 不提取,或如需保留则单独一条标 procedural。

示例:"资格审查方式为资格后审,开标后由评标委员会审核;资料从主体信息库调取并与投标文件结合审查;资料不齐全或未按要求提供的为无效投标"
- ✅ 提取内核:"投标文件须按要求提供齐全的资格审查资料,资料不齐全或未按要求提供的为无效投标"(actionable,否决项)
- ❌ 不要整条照抄(资格后审程序/信息库调取是评委动作,不是投标文件可响应内容)

## 【占位符模板条款】未填空的合同模板不作为审查项

条款含未填写占位符(如 [具体比例]、[具体金额]、[  ]、____、X%、XX元 等留空表述)时,
说明这是招标文件引用的合同模板原文,双方数值尚未约定,投标文件无从响应:
- 一律标 review_relevance="informational",不得标为否决项;
- 例:"误期赔偿:每延误1日赔偿合同价格的[具体比例]%,累计最高[具体金额]" → informational。

## 【合同文本】拟签合同/合同格式条款不作为招标要求

输入开头可能带 [当前内容所属章节: XXX（合同/履约阶段）] 标注。
若内容属于"拟签合同/合同格式/合同协议书/合同条款"章节(中标后双方履约的合同文本,如:合同编号确认、酬金定义与支付方式、违约赔偿比例、合同变更/解除/转让、争议解决、不可抗力、通知条款、合同生效条件等):
- 这些是中标后的合同约定,不是投标阶段需投标人逐条响应的招标要求;
- 一律标 review_relevance="informational",不要标为否决项;
- 不得编造,可如实提取但降级,供人工知悉。

## 提取质量自查(输出前必须完成)

检查以下指标:
- [ ] "保证金"相关要求是否≤3条? (合理:1-2核心+1退还规则)
- [ ] "营业执照"相关要求是否≤2条? (合理:1核心资质)
- [ ] "报价"相关要求是否≤4条? (合理:2-3核心报价)
- [ ] "技术测试项"是否合并为工作包? (不要逐项列举)
- [ ] 是否将招标人规则误提取为投标人要求?
- [ ] 同义手续说明是否已合并,但独立废标条件是否仍逐条保留?
- [ ] 资格审查/"下列情形之一"/★ 条款是否被合并成一条? (若是则拆回)

如不符合,继续深度合并直至达标。
"""


TENDER_EXTRACT_USER = """请从以下招标文件内容中提取所有招标要求。

## 招标文件内容
页码:{page_num}

{content}

## 输出要求
请以JSON数组格式输出,每个要求包含以下字段:

```json
[
    {{
        "category": "QUAL|TECH|COMM|PERF|STAFF|FORMAT",
        "description": "要求的完整描述(如为合并后的,需包含全部要素)",
        "is_mandatory": true|false,
        "review_relevance": "actionable|informational|procedural",
        "keywords": ["关键词1", "关键词2"],
        "source_pages": [页码1, 页码2],
        "merged_count": 1,
        "sub_requirements": []
    }}
]
```

## category 字段强制约束(必须严格遵守)
category 只允许以下 6 个值,禁止使用任何其他值:
- QUAL: 资质要求
- TECH: 技术要求
- COMM: 商务要求
- PERF: 业绩要求
- STAFF: 人员要求
- FORMAT: 格式要求

**禁止输出 GENERIC、OTHER、GENERAL 或任何不在上述列表中的值。**
特别注意: ACTIONABLE、INFORMATIONAL、PROCEDURAL 属于 review_relevance 字段, 绝不能写入 category。
如果无法准确归类,选择最接近的一个类别,并在 description 中说明不确定性。

## review_relevance 字段强制约束(必须严格遵守)
review_relevance 只允许以下 3 个值,禁止使用 category 的 6 类代码:
- actionable: 进入审查
- informational: 信息告知/合同文本
- procedural: 程序/履约过程

QUAL、TECH、COMM、PERF、STAFF、FORMAT 只能写入 category, 绝不能写入 review_relevance。

## 特别提醒
1. **深度去重**:同义要求必须合并,不要重复提取
2. **保证金**:所有保证金相关合并为1-2条
3. **营业执照**:所有资质证书相关合并为1-2条
4. **报价**:按子主题合并为2-4条
5. **技术测试项**:合并为工作包,不要逐项列举
6. **排除招标人规则**:不提取"不予退还的情形"等规则
7. **否决项**:资格/废标/无效投标/★ 具体条款必须逐条保留;物理形态/开标程序/履约期不是否决项,除非原文写明缺失即废标
8. **页码必须使用实际数字**:source_pages和description中的页码引用必须使用数据中的真实页码数字,严禁使用"X"、"N"等占位符
9. **评审意义分级**:每条必须标 review_relevance;拟签合同文本/政策告知标 informational,程序/纪律/履约/异议/踏勘标 procedural,其余实质响应项标 actionable

如果该页面没有新的招标要求(或仅为已提取要求的重复),返回空数组[]。

请输出JSON:"""


# ========================================================================
# 旧的全量合并模板 (TENDER_EXTRACT_MERGE_USER / CATEGORY_MERGE_USER /
# SECONDARY_MERGE_USER) 及其工厂函数 (get_tender_merge_prompt /
# get_category_merge_prompt / get_secondary_merge_prompt) 已被
# *_decision 轻量模式替代, 对应 seed 节点在 DEPRECATED_NODE_KEYS 中。
# 保留 CATEGORY_MERGE_SYSTEM 供 category_merge_decision 继续使用。
# ========================================================================
def get_tender_extract_prompt(content: str, page_num: int = 0) -> tuple[str, str]:
    """获取招标要求提取的完整Prompt (Registry 优先, 代码默认值兜底)

    Args:
        content: 招标文件内容
        page_num: 页码

    Returns:
        tuple[str, str]: (system_message, user_message)
    """
    from app.core.ai.prompt_registry import resolve_system_prompt, resolve_user_template
    node = "bid_review.tender_extract_page"
    # system 含 {category_descriptions} 占位; user 含 {content}/{page_num}。
    # 显式分别 format, 避免 JSON 字面 '{' 误触 format 报错。
    system = resolve_system_prompt(node, TENDER_EXTRACT_SYSTEM).format(
        category_descriptions=CATEGORY_DESCRIPTIONS,
    )
    user = resolve_user_template(node, TENDER_EXTRACT_USER).format(
        content=content,
        page_num=page_num,
    )
    return system, user


# ========================================================================
# 分类合并 Prompt V1.0 (新增 - 按类别独立去重)
# ========================================================================

# 各类别的合并目标
CATEGORY_MERGE_TARGETS = {
    "QUAL": "企业资质与人员持证分别保留，合并为2-4条",
    "TECH": "技术测试项合并为工作包（不逐项列举），技术参数要求按主题合并",
    "COMM": "保证金相关<=3条，报价相关<=4条，付款/交付/合同相关分别合并",
    "PERF": "企业业绩、人员业绩、证明材料分别保留，合并为2-4条",
    "STAFF": "按证书/社保/业绩/辅助材料/管理分面保留，合并为3-5条",
    "FORMAT": "格式/签章要求合并为1-2条",
}

# 类别名称映射
CATEGORY_NAMES = {
    "QUAL": "资质要求",
    "TECH": "技术要求",
    "COMM": "商务要求",
    "PERF": "业绩要求",
    "STAFF": "人员要求",
    "FORMAT": "格式要求",
}

CATEGORY_MERGE_SYSTEM = """你是招标文件分析专家，专门负责【{category_name}】类要求的深度去重合并。

## 核心任务
将同一类别内的重复/同义要求合并为最精简的列表。

## 合并原则
1. **同义合并**：不同表述的同一要求合并为1条
2. **要素整合**：提取所有相关要素，整合为完整描述
3. **来源保留**：合并后保留所有来源页码
4. **数量标注**：标注 merged_count 表示合并了多少条
5. **具体规定优先**：同一事项若前附表/专章给出具体规定（含「不要求/不需要/无/不接受/不进行」），
   「按招标文件要求…」「按前附表规定…」「详见…」这类引用型条款并入该具体规定，以具体规定为准，
   不得让引用句独立成条与具体规定并列矛盾
6. **独立废标条件不并**：数字事实、对象或条件不同的可独立废标条件（资格审查/响应性/★项）
   必须各自保留；只有同一条件的不同表述才合并

## 该类别的合并目标
{merge_target}

## 输出格式
JSON数组，每条包含：category, description, is_mandatory, keywords, source_pages, merged_count
"""
REFLECTION_SYSTEM = """你是招标要求质量审核专家，负责检查去重合并的结果质量。

## 核心任务
检查合并后的要求列表，识别以下问题：
1. **跨类别重复**：同一内容被分到不同类别
2. **关键词重复**：核心关键词出现次数超标
3. **合并不完整**：同义要求未合并

## 质量标准
- 保证金相关：≤3条
- 营业执照/资质相关：≤2条
- 报价相关：≤4条
- 技术测试项：合并为工作包，不逐项列举
- 业绩要求：企业业绩和人员业绩必须分条，>=2条
- 人员要求：证书/社保/业绩应分面保留，>=3条
- 总否决项比例：≤20%
"""

REFLECTION_USER = """请审核以下招标要求列表的去重质量。

## 当前要求列表（{count}条）
{requirements_json}

## 请检查
1. 是否存在跨类别重复（同一内容在不同类别出现）
2. 核心关键词是否超标（保证金>3, 营业执照>2, 报价>4）
3. 是否存在未合并的同义要求

## 输出格式（必须严格遵守）
1. 只允许输出 JSON 对象本体
2. 禁止输出 markdown 代码块（例如 ```json）
3. 禁止输出解释文字、前后缀说明
4. 必须包含字段：has_issues, issues, quality_score
5. issues 中每个元素必须包含字段：type, description, affected_indices
6. type 仅允许：cross_category_duplicate / keyword_excess / unmerged

请输出JSON:"""

SECONDARY_MERGE_DECISION_USER = """请根据以下质量问题，判断哪些招标要求应该合并。

## 当前要求列表（每条带 idx 编号）
{requirements_json}

## 检测到的问题
{issues_json}

## 合并禁止规则（必须遵守）
1. 禁止将"企业业绩"与"人员业绩"合并为一条
2. 禁止将"人员证书"与"人员业绩"合并为一条
3. 禁止将"保证金"与"报价"合并为一条
4. 禁止将"企业资质"与"人员持证"合并为一条
5. 总原则：不同审查维度的要求不应合并，即使关键词有重叠

## 任务
分析上述问题，输出合并决策。只需要告诉我：
1. 哪些 idx 应合并为一条（提供合并后的 description 和 keywords）
2. 未被提及的 idx 将自动原样保留，无需列出

## 输出格式（必须严格遵守）
1. 只允许输出 JSON 数组本体
2. 禁止输出 markdown 代码块
3. 禁止输出解释文字
4. 每个元素必须包含:
   - merge_ids: 要合并的 idx 列表（至少2个）
   - category: 合并后的类别
   - description: 合并后的描述
   - is_mandatory: 合并后是否为否决项（任一原条目为否决项则为 true）
   - keywords: 合并后的关键词列表

示例:
[{{"merge_ids":[0,3,7],"category":"QUAL","description":"合并后描述","is_mandatory":true,"keywords":["词1","词2"]}}]

如果无需合并，输出空数组 []

请输出合并决策:"""


CATEGORY_MERGE_DECISION_USER = """请对以下【{category_name}】类的 {count} 条招标要求进行去重，判断哪些要求应该合并。

## 当前类别要求列表（每条带 idx 编号）
{requirements_json}

## 合并目标
{merge_target}

## 技术类专项压缩规则（仅当类别为技术要求时）
1. 对同主题测试项进行工作包化合并
2. description 优先保留关键约束，建议不超过 180 字
3. keywords 控制在 3-6 个

## 任务
识别同义/重复要求，输出合并决策。只需要告诉我：
1. 哪些 idx 应合并为一条（提供合并后的 description 和 keywords）
2. 未被提及的 idx 将自动原样保留，无需列出

## 输出格式（必须严格遵守）
1. 只允许输出 JSON 数组本体
2. 禁止输出 markdown 代码块
3. 禁止输出解释文字
4. 每个元素必须包含:
   - merge_ids: 要合并的 idx 列表（至少2个）
   - category: 合并后的类别
   - description: 合并后的描述
   - is_mandatory: 合并后是否为否决项
   - keywords: 合并后的关键词列表

示例:
[{{"merge_ids":[0,3],"category":"{category_code}","description":"合并后描述","is_mandatory":true,"keywords":["词1"]}}]

如果无需合并，输出空数组 []

请输出合并决策:"""


def get_reflection_prompt(
    requirements_json: str,
    count: int
) -> tuple[str, str]:
    """获取反思检查的Prompt (Registry 优先)

    Args:
        requirements_json: JSON格式的要求列表
        count: 要求数量

    Returns:
        tuple[str, str]: (system_message, user_message)
    """
    from app.core.ai.prompt_registry import resolve_system_prompt, resolve_user_template
    node = "bid_review.tender_reflection"
    system = resolve_system_prompt(node, REFLECTION_SYSTEM)
    user = resolve_user_template(node, REFLECTION_USER).format(
        count=count,
        requirements_json=requirements_json,
    )
    return system, user


def get_secondary_merge_decision_prompt(
    requirements_json: str,
    issues_json: str
) -> tuple[str, str]:
    """获取二次合并的决策模式Prompt (Registry 优先, 轻量输出: 仅合并决策)"""
    from app.core.ai.prompt_registry import resolve_system_prompt, resolve_user_template
    node = "bid_review.secondary_merge_decision"
    system = resolve_system_prompt(node, TENDER_EXTRACT_SYSTEM).format(
        category_descriptions=CATEGORY_DESCRIPTIONS,
    )
    user = resolve_user_template(node, SECONDARY_MERGE_DECISION_USER).format(
        requirements_json=requirements_json,
        issues_json=issues_json,
    )
    return system, user


def get_category_merge_decision_prompt(
    category: str,
    requirements_json: str,
    count: int
) -> tuple[str, str]:
    """获取分类合并的决策模式Prompt (Registry 优先, 轻量输出: 仅合并决策)"""
    from app.core.ai.prompt_registry import resolve_system_prompt, resolve_user_template
    node = "bid_review.category_merge_decision"
    category_name = CATEGORY_NAMES.get(category, category)
    merge_target = CATEGORY_MERGE_TARGETS.get(category, "按同义表述合并")
    system = resolve_system_prompt(node, CATEGORY_MERGE_SYSTEM).format(
        category_name=category_name,
        merge_target=merge_target,
    )
    user = resolve_user_template(node, CATEGORY_MERGE_DECISION_USER).format(
        category_name=category_name,
        count=count,
        requirements_json=requirements_json,
        merge_target=merge_target,
        category_code=category,
    )
    return system, user