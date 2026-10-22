"""行为性否决条款规则地板（P1 否决项双确认降级的确定性一翼）。

职责: 用词表判定一条否决项是否为"纯行为性/程序性废标"——其证据载体是投标人
 行为或招标程序，单份投标文件文字无法核验，可参与折叠降级双确认。
不做: 不单独决定降级（必须与 LLM 证据载体判定双确认，见 relevance_classifier）、
 不处理非否决项、不调用 LLM、不访问 DB。

设计（镜像 writer clause_writability_filter 成熟模式， 红线约束）:
- 词表仅作规则地板：LLM 判非投标文件载体 且 本词表命中 且 无内容性例外，三者
 同时成立才允许折叠否决项，任一不满足保持 actionable（宁可多审不可漏审）。
- 内容性例外强制保留：条款含 ★/▲、未提供、超限价、负偏离、技术参数等信号时，
 说明存在可通过投标文件内容核验的内核，一律不折叠。
"""

from __future__ import annotations

# 行为性/程序性废标特征（证据载体 = 投标人行为 / 招标程序 / 外部核查，
# 单份投标文件文字无法核验）。覆盖 1527 样例中的挂靠/转包/行贿/虚报业绩等
# 既有词表缺口（writer 侧词表仅含串通/多份投标/逾期/撤回）。
_BEHAVIORAL_PATTERNS: tuple[str, ...] = (
    # 串通 / 围标
    "串通", "围标", "陪标", "恶意竞标", "投标团伙",
    # 挂靠 / 转包 / 借用资质
    "挂靠", "转包", "违法分包", "借用资质", "以他人名义", "代为投标",
    # 造假 / 行贿
    "行贿", "谋取中标", "弄虚作假", "虚假承诺", "虚报业绩",
    "提供虚假", "虚假材料", "伪造", "变造",
    # 多份投标 / 递交行为
    "2个或者2个以上", "两个或两个以上", "2个以上", "两份以上",
    "多个投标文件", "多份投标文件", "不同的投标文件",
    "逾期送达", "逾期递交", "逾期到达", "逾期送到",
    "撤回投标", "撤销投标", "撤回其投标",
    # 现场 / 程序行为
    "干扰评标", "影响评标", "扰乱招投标", "扰乱开标",
)

# 内容性例外：命中任一则强制保留 actionable（防误杀含可核验内核的否决项，
# 如"★条款未响应""未提供指定材料""超最高限价""技术负偏离"）。
_CONTENT_EXEMPTIONS: tuple[str, ...] = (
    "★", "▲", "☆",
    "最高限价", "招标控制价", "最高投标限价", "报价超", "超过限价",
    "未提供", "未按要求提供", "未提交", "缺少", "资料不齐", "不齐全",
    "应包含", "须包含", "应提供", "须提供", "应提交", "须提交",
    "负偏离", "技术参数", "技术指标", "实质性要求",
    "投标有效期", "保证金金额", "签字盖章不", "未加盖", "未签字",
)


def has_reviewable_content_kernel(description: str | None) -> bool:
    """否决项是否含可由投标文件内容核验的内核（★条款/指定材料/限价/负偏离等）。: 该例外是否决项折叠的统一守门，任何规则地板（行为性词表 / 噪音规则 /
 合同启发式）命中都必须先过它，否则「资格后审程序 + 资料不齐无效」这类混合条款
 会被程序外壳词拖进折叠。
 """
    text = description or ""
    return any(token in text for token in _CONTENT_EXEMPTIONS)


def matches_behavioral_disqualification(description: str | None) -> bool:
    """判定否决项描述是否命中"纯行为性废标"规则地板。

    Args:
        description: 要求描述文本。

    Returns:
        命中行为性特征且无内容性例外时为 True（仅作为双确认的一翼，
        不单独触发降级）。
    """
    text = description or ""
    if not text.strip():
        return False
    if has_reviewable_content_kernel(text):
        return False
    return any(token in text for token in _BEHAVIORAL_PATTERNS)


__all__ = ["has_reviewable_content_kernel", "matches_behavioral_disqualification"]
