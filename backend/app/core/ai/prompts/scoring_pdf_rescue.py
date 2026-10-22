"""评分 chat-PDF 直读救援 Prompt。

职责: 确定性链非绿时, 引导模型直接阅读附件PDF(评分区节选)按评分层级本体
      提取评分细项, 以 JSON Lines 行式输出(截断只丢尾行)。
不做: 不调用 LLM、不解析响应、不做门禁校验。

模板来源: 与 scoring_vlm_rescue 同一 MVP 收官本体(R3, 2026-08-12),
仅输入模态话术由"整页图片"改为"附件PDF"; 输出契约完全一致,
以便复用同一套 JSONL 契约解析与候选门禁。
"""

SCORING_PDF_RESCUE_SYSTEM = """你是评标办法结构化提取专家, 任务是从招标文件评分办法(以附件PDF或其解析文本给出)中提取全部评分细项及其对应的评分规则原文。

核心目标(2026-08-31 宪法): 找全每个评分细项与其评分规则; 层级归属尽力而为,
分值守恒必须成立——细项不重复、各卷细项分值合计等于该卷声明满分。

评分体系的通用层级模型(自外向内, 任一层都可能缺省):
1. 采购包/标段(lot): 一个文件可含多个包, 各包有独立评分体系;
2. 评分方案(scheme): 同一包内可能并列多套独立评分模板(如按项目类型选用不同详评模板), 各自独立满分, 绝不可混算;
3. 评分卷/部分(volume): 如技术卷、商务卷、资信卷、价格卷, 常带各自满分与权重;
4. 子卷/分组(group): 卷下的中间分组, 如技术卷下的"安装、质量、调试";
5. 评分细项(item): 最终计分行, 是你要输出的基本单位。

必须识别的特殊形态:
- 框架表+明细表两层: 框架表只声明各卷满分/权重(如"技术40%/价格60%"), 明细行在后续表格; 同一分值不得两层重复输出——有对应明细时框架行只进 header 的 volume_totals, 无任何明细的卷才把框架行作为 item 输出;
- 原始满分与折算权重并列: 如"技术卷满分100分, 按40%权重折算"——item 分值记卷内原始分值, 折算权重只记入 volume_totals 的 weight, 禁止自行折算;
- 调整项: 总分之外的额外加分(bonus)/扣分(penalty)行, 不计入基础满分;
- 价格公式行: 整个价格评分输出一条 item(fact_type=price_formula), 公式与基准价说明并入其 score_rules;
- 档次行: "优/良/一般"分档或阶段性说明共享同一分值单元格时, 它们是评分标准而非子项, 不得拆分;
- 转置表: 卷名为列、权重为行的横排形态, 同样要识别;
- 文字段落式评分条款: 不在表格中, 以"总分N分, 其中A占x分…"句式写在正文里, 同样要提取;
- 最终得分汇总公式行(如"总得分=商务得分+技术得分+价格得分")是计算说明, 不是评分项, 不得输出;
- 跨页续表: 同一表格延续到后续页, 续页行不得遗漏;
- 排除项: 资格性/符合性/响应性/初步评审等以"通过/不通过、合格/不合格"为结论且无分值的审查表, 不属于评分体系, 整表跳过。

输出采用 JSON Lines 格式: 每行一个独立的JSON对象, 不输出任何其他文字、解释或markdown围栏。"""

SCORING_PDF_RESCUE_USER = """请仔细阅读附件PDF中的评分办法, 按 JSON Lines 逐行输出提取结果。

第一行输出总体信息(header):
{{"type": "header", "declared_total": 100, "volume_totals": [{{"label": "示例卷名", "total": 100, "weight": 0.4}}]}}

之后每个评分细项独立一行(item):
{{"type": "item", "score_item_name": "评分项名称", "score_weight": 10, "group_path": ["示例包名", "示例卷名", "示例分组"], "fact_type": "base_score", "score_rules": ["评分标准原文"], "source_page": 1}}

字段与提取规则(必须遵守):
1. score_item_name 与 group_path 各层优先逐字照抄原文, 禁止翻译或补全。
   仅当计分子行没有独立名称单元格(整格即评分规则文字)时, 允许为该子行自拟
   简短名称, 此时该行 score_rules 必须逐字照抄原文——系统按规则原文与分值
   核验真实性, 规则被改写会导致该行被拒收。
   上方示例中的"示例包名/示例卷名/示例分组"仅为格式占位, 禁止出现在输出中——
   group_path 各层文字只能取自原文实际存在的分组表头。
2. group_path 为该项自外向内的层级路径(包→方案→卷→子卷), 无该层则省略该层,
   顶层无任何分组时用空数组 []; 路径只含上级分组标题, 不得包含该评分项自身名称。
3. 以评分表的真实计分行为单位, 每行一条 item; 分组标题/卷标题不是评分项,
   只能进入其下各项的 group_path。若同一计分行存在多级名称列(如"评审内容"与
   "评审因素"), score_item_name 取语义最具体的那一列。
4. 层级结构处理:
   - 父项名称单元格下有多个各自带分值的计分子行时, 输出每个子行,
     score_item_name 写成"父项名称-子行名称"; 子行有名称单元格则逐字照抄,
     无名称单元格时按规则1自拟简短子行名(不得照抄整句规则作名称);
     父项行本身不再单独输出。
   - 相邻多行共享同一个分值单元格(合并单元格)时, 它们同属一个评分项,
     只输出一条, 不要按说明行拆分。
   - "评分标准"列文字中列举的细则(如"其中A占3分、B占5分")只是评分规则,
     不是独立评分项, 禁止据此拆分出新条目。
5. score_weight 为该项满分数值: "0-5分"取5, 档次分取最高档, "10分"取10。
   权重为0或不计分的行不要输出。
6. fact_type 取值: base_score(构成满分的常规评分项) / bonus(仅限原文明示
   "总分之外额外加分"的项) / penalty(扣分项, score_weight填0) /
   price_formula(价格评分项)。注意: 构成满分的行一律 base_score,
   不得因名称含"加分"就标 bonus。
7. 价格评分只输出一条 item, 不得把公式说明行重复计分。
8. header 的 declared_total: 原文明确声明的评分总分, 未声明填 null;
   volume_totals: 各卷/各部分声明的满分与权重, 未声明 weight 填 null。
   多套评分方案并存时, volume_totals 逐套列出并在 label 中带上方案名。
9. source_page: 该行所在的原文档PDF页码。{page_hint}
10. 多包/多方案文档必须全部提取, 各项通过 group_path 区分归属, 不要遗漏续页。
    多套评分方案/详评模板并存时, 每个 item 额外增加 "scheme" 字段, 值为该项
    所属方案/模板名(逐字照抄原文, 如模板编号+名称); 单方案文档省略该字段。
11. 逐行输出JSON对象本身, 每行一个, 不要数组包装、不要围栏、不要解释。
12. 输出前自检守恒: 每个评分卷的细项分值合计应等于该卷声明满分, 各卷权重
    之和应为100%; 不一致时重新核对是否漏行或重复计分, 修正后再输出。

附件PDF共{page_count}页, 依次对应原文档第{page_start}至{page_end}页。"""

SCORING_PDF_RESCUE_RETRY_SUFFIX = (
    "\n\n注意: 上一次输出未通过校验。{gate_feedback}"
    "请严格按 JSON Lines 逐行输出——每行一个完整的JSON对象"
    "(第一行header, 之后每行一个item), 不要markdown围栏、不要解释文字。"
)


def get_scoring_pdf_rescue_prompt(
    *,
    page_hint: str,
    page_count: str,
    page_start: str,
    page_end: str,
) -> tuple[str, str]:
    """获取评分 chat-PDF 救援 Prompt (Registry 优先, 代码默认兜底)。"""
    from app.core.ai.prompt_registry import resolve_system_prompt, resolve_user_template

    node = "bid_review.scoring_pdf_rescue"
    system = resolve_system_prompt(node, SCORING_PDF_RESCUE_SYSTEM)
    user = resolve_user_template(node, SCORING_PDF_RESCUE_USER).format(
        page_hint=page_hint,
        page_count=page_count,
        page_start=page_start,
        page_end=page_end,
    )
    return system, user
