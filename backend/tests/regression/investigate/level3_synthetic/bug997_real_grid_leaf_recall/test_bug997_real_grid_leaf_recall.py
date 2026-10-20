""" Level3 回归: 项目3477真实网格形态的叶子强证据召回与判绿。

样本来源: document_id=2513(文档27.pdf)确认页链路只读复算取证, 三个 scoring_table
chunk 的缝合后网格 + 10 条落库评分项(名称/分值/卷标签均为实测值)。

载荷特征(缺一不可, 均为 根因载体):
 - chunk0 规则列含"得 4-5分"类区间(v1 据此歧义弃行, 召回 4/9);
 - chunk0 row1 为跨页表头污染的声明行(捕获出伪组"该项:1分" → expected=101);
 - chunk1 含 0.00 分扣分行(失信行为扣);
 - chunk2 网格名"报价评分"对提取名"报价"(v1 严格等名匹配失败);
 - 卷标签 详细评审(技术) vs 声明 详细评审技术(括号形态失配)。
"""
from __future__ import annotations

import pytest

from app.core.extractor.scoring.ledger.contracts import LedgerItemEntry
from app.core.extractor.scoring.ledger.scoped_reconciliation import (
    ScopedLedgerRequest,
    coordinate_scoped_ledger,
)
from app.core.extractor.scoring.scoring_green_verdict import resolve_scoring_verdict

pytestmark = pytest.mark.scoring

_TECH_GRID = [
    ["序号", "评审因素", "分值", "评审标准", "评审依据"],
    ["分值构成：详细评审技术：43分；详细评审商务：4分；序号评审因素分值评审标准评审依据该项：1分；报价评审：53分"],
    ["1", "技术方案与技术措施", "5.00",
     "有施工及维护方案、技术措施， 且合理可行、针对性强的得 4-5分； 有施工及维护方案、技术措施， 且合理可行、针对性一般的得 2-3分； 有施工及维护方案、技术措施， 且基本合理、针对性差的得 0-1分。",
     "包括技术路线、关键措施，达到技术指标要求的过程说明， 施工技术保证措施等。"],
    ["2", "工期保证措施", "2.00",
     "有施工进度计划，能保证工期措施切实可行并具有针对性的加2分； 有施工进度计划，有保证工期措施,具有针对性但保证措施不完善，针对性不强的加1分。 施工进度计划不贴近投标文件或过于简单、实际操作性较低得0分。"],
    ["3", "质量保障措施", "3.00",
     "质量保障措施详细有阐述且有针对性、切实可行切实可行得3分； 质量措施满足招标文件，有阐述，但针对性、可行性一般的得2分； 质量措施不贴近投标文件或过于简单、实际操作性较低得 0-1分。"],
    ["4", "QHSE保障措施", "3.00",
     "投标人提供的QHSE保障措施及应急方案（机构设置、规章制度、规程、人员、教育、设备设施）完整，风险识别、评价、风险削减措施得当的得3 分； 保障措施及应急方案较为完整，风险识别、评价，风险削减措施较为得当的得2分； 保障措施不具体不全面或针对性不强，得0-1分。",
     "针对涉及QHSE相关要求的项目"],
    ["5", "资源配置情况", "15.00",
     "施工队伍车辆11台含司机， 包含工程车（吊车至少三辆、 举升车至少三辆，操作人员需提供特种车辆操作证）、人员拉运车辆等，工器具可满足各专业机组的使用（电镐15台、 万用表13台、焊接器具8套)完全可以保证工程进度、满足施工需要得15分； 施工队伍车辆10台含司机，基本可以满足工程进度、施工需要得10分； 施工队伍车辆9台含司机，勉强可以满足工程进度、施工需要得5分。",
     "常用工具及设备须提供清单或照片，并提供购买发票或租用合同。"],
    ["6", "施工人员配备情况", "15.00",
     "投标人能配备80人及以上施工队伍：含电工15人、焊工5 人，得15分； 投标人能配备70人至79人施工队伍：含电工14人、焊工4 人，得10分； 投标人能配备61人至69人施工队伍：含电工13人，焊工3 人，得5分。",
     "电工、焊工须提供与本单位签订的劳动合同。"],
]
_COMM_GRID = [
    ["序号", "评审因素", "分值", "评审标准", "评审依据"],
    ["1", "投标文件内容与格式", "1.00",
     "该项满分1分，投标文件编制完整，格式规范、关联准确、符合招标文件要求的，得1分；投标文件存在非实质性差异或漏洞，每项扣 0.2 分，扣完该项得分为止。"],
    ["2", "业绩", "3.00",
     "在满足资格条件的基础上： 自2023年1月1日起至投标截止日期间（以合同签订时间为准），投标人具有已完成的合同金额 100万元以上的通信工程施工总承包或电子与智能化专业相关业绩，每项可得[1]分。最高可得[3 分]。",
     "需同时提供合同、发票、发票查验截图。"],
    ["3", "失信行为扣", "0.00", "1.失信分以开标当日中"],
]
_PRICE_GRID = [
    ["分组", "评分因素", "评分内容与标准", "分值"],
    ["价格部分"],
    ["", "报价评分",
     "分 国石油招标投标网发布的失信行为信息为准。 2.失信权重=10%3.失信扣分=7×10%×(失信分/10) 2.4 报价评审满分:53.00分以清单计价中单价的为最高限价,以此为基准整体下浮X%进行报价。 Fi=Dmin/Di*Fmax即招标报价得分=(评标基准价÷有效报价)×53。",
     "53"],
]

_ENTRIES: tuple[LedgerItemEntry, ...] = (
    LedgerItemEntry(name="技术方案与技术措施", score=5.0, volume_label="详细评审(技术)"),
    LedgerItemEntry(name="工期保证措施", score=2.0, volume_label="详细评审(技术)"),
    LedgerItemEntry(name="质量保障措施", score=3.0, volume_label="详细评审(技术)"),
    LedgerItemEntry(name="QHSE保障措施", score=3.0, volume_label="详细评审(技术)"),
    LedgerItemEntry(name="资源配置情况", score=15.0, volume_label="详细评审(技术)"),
    LedgerItemEntry(name="施工人员配备情况", score=15.0, volume_label="详细评审(技术)"),
    LedgerItemEntry(name="投标文件内容与格式", score=1.0, volume_label="详细评审(商务)"),
    LedgerItemEntry(name="业绩", score=3.0, volume_label="详细评审(商务)"),
    LedgerItemEntry(name="失信行为扣", score=0.0, volume_label="详细评审(商务)"),
    LedgerItemEntry(name="报价", score=53.0, volume_label="报价"),
)

# 确认页 legacy 报告实测值(按卷校验全过, 无结构问题)
_TOTALS_PAYLOAD = {
    "needs_review": False,
    "note": "按卷评分项满分校验通过",
    "by_volume": {"详细评审(技术)": 43.0, "详细评审(商务)": 4.0, "报价": 53.0},
    "weight_sum_ok": True,
    "item_count": 9,
    "missing_score_count": 0,
    "missing_rule_count": 0,
    "structure_review_required": False,
    "diagnostics": [],
}


def _request(entries: tuple[LedgerItemEntry, ...]) -> ScopedLedgerRequest:
    grids = ((68, _TECH_GRID), (69, _COMM_GRID), (70, _PRICE_GRID))
    return ScopedLedgerRequest(
        pages=((68, ""), (69, ""), (70, "")),
        chunk_scopes=((None, None), (None, None), (None, None)),
        grids=grids,
        grid_scopes=((None, None), (None, None), (None, None)),
        entries_with_rules=tuple((e, "规则文本") for e in entries),
    )


class TestBug997RealGridLeafRecall:
    def test_leaf_integrity_verified_on_real_grids(self) -> None:
        """真实三网格 9 正分叶恰闭合 100: 强证据必须 verified(v1 为 4叶/59分/0匹配)。"""
        report = coordinate_scoped_ledger(_request(_ENTRIES))

        assert report.leaf_integrity.verified is True
        assert report.leaf_integrity.basis == "single_100"
        assert report.leaf_integrity.inventory_count == 9
        assert report.leaf_integrity.total == pytest.approx(100.0)

    def test_verdict_green_despite_declaration_noise(self) -> None:
        """伪声明组(该项:1分→expected=101)与括号卷标签失配噪音下, verdict 应绿。"""
        report = coordinate_scoped_ledger(_request(_ENTRIES))

        verdict = resolve_scoring_verdict(dict(_TOTALS_PAYLOAD), report)

        assert verdict == "green"

    def test_missing_price_item_stays_amber(self) -> None:
        """负例: 少提取报价项(多重集缺 53) → 强证据不成立, 维持非绿。"""
        entries = _ENTRIES[:-1]
        report = coordinate_scoped_ledger(_request(entries))

        assert report.leaf_integrity.verified is False
        assert resolve_scoring_verdict(dict(_TOTALS_PAYLOAD), report) != "green"

    def test_structure_review_required_blocks_green(self) -> None:
        """负例: 结构完整性标记为真时, 强证据不得放行。"""
        report = coordinate_scoped_ledger(_request(_ENTRIES))
        payload = {**_TOTALS_PAYLOAD, "structure_review_required": True}

        assert resolve_scoring_verdict(payload, report) != "green"
