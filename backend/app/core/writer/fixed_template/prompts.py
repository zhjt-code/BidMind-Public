"""固定模板 LLM-first 识别 Prompt 与 anchors。

职责: 集中存放 writing.fixed_template_recognize 节点的代码默认 Prompt 与各模板 anchors。
不做: 不发起 LLM 调用、不解析 LLM 输出、不维护任务快照。
"""

from __future__ import annotations

from typing import Tuple


FIXED_TEMPLATE_RECOGNIZE_SYSTEM = """你是招标文件固定模板识别专家。

任务: 从给定招标文件文本中识别所有"固定模板"。

固定模板定义:
- 招标方提供具体格式, 要求投标人按格式填写使用的模板
- 例如: 投标函、投标保证金缴纳证明、法定代表人证明书、法定代表人授权委托书、投标人基本情况表、开标一览表、投标报价书等

识别铁律:
1. 章节目录、纯材料要求声明、合同正文、投标须知、评标办法不是模板, 不能输出
2. 模板正文必须含具体字段或签章信号, 如 "致:" / "投 标 人:" / "盖单位章" / "(签字)" / "日期"
3. raw_text 必须是干净可直接套用的模板正文:
   - 删除页眉、页脚、孤立页码、孤立编号、"表格第X行"残片、"0 ¥"金额占位行
   - 合并 PDF 抽取造成的"独立数字行 + 顿号开头的下一行", 写成 "1、xxx" 一行
   - 剥离与模板标题完全重复的首行, 例如标题为"投标函"则首行不应只是"五、投标函"
   - 不补全字段值, 字段位保留原始空白或下划线
4. template_key 优先取自以下白名单(7 类, 不接受自定义后缀, 不接受 _technical / _commercial 形式):
   bid_letter / bid_security_certificate / legal_representative_certificate /
   legal_representative_authorization / bidder_basic_info / bid_opening_table / bid_quote
   白名单之外的固定模板(如各类简历表、投标意向确认书、廉洁承诺书、投标函附录等)
   **同样必须输出**: template_key 填 "custom", title 填模板原标题
5. 如果同一模板在技术标和商务标各出现一次, 输出两条 entry, **template_key 保持完全一致**,
   仅用 volume_type 字段区分 technical / commercial; 不要在 template_key 后缀里区分分卷
6. 严格输出 JSON 数组, 不要 markdown 代码块, 不要解释"""

FIXED_TEMPLATE_RECOGNIZE_USER = """以下是招标文件固定模板候选页文本:

page_range_hint: {page_range_hint}
volume_type_hint: {volume_type_hint}

{chunks_text}

请识别所有固定模板, 严格输出 JSON 数组:
[
  {{
    "template_key": "优先 bid_letter / bid_security_certificate / legal_representative_certificate / legal_representative_authorization / bidder_basic_info / bid_opening_table / bid_quote; 白名单外模板填 custom",
    "title": "模板标题, 例如 投标函",
    "volume_type": "technical / commercial / null",
    "source_page_start": 41,
    "source_page_end": 41,
    "raw_text": "干净的模板正文, 已剔除页眉/孤立编号/目录条目/表格残片/重复标题",
    "fields": ["字段名1", "字段名2"],
    "signature_slots": ["盖单位章", "签字", "日期"],
    "confidence": 0.0
  }}
]

仅输出 JSON 数组, 不要任何说明文字。"""


# 各 template_key 期望命中的字段 / 签章 anchor 集合。
# 模板正文如不能完整覆盖 anchors, 视为 missing_fields 命中。
FIXED_TEMPLATE_ANCHORS: dict[str, tuple[str, ...]] = {
    "bid_letter": ("致：", "投标方", "签字", "日期"),
    "bid_opening_table": ("项目名称", "报价", "备注", "签字", "盖章"),
    "bid_security_certificate": ("致：", "投标保证金", "投 标 人", "盖单位章", "日 期"),
    "legal_representative_certificate": ("致：", "现任我单位", "职务", "投标人", "公章", "日 期"),
    "legal_representative_authorization": ("致：", "特授权", "被授权人", "法定代表人", "投标人", "公章"),
    "bid_quote": ("致：", "投标人", "报价", "签字", "公章", "日 期"),
    "bidder_basic_info": ("单位名称", "联系电话", "注册资金", "成立时间", "法人代表"),
}


def get_recognize_prompt(
    chunks_text: str,
    page_range_hint: str,
    volume_type_hint: str | None,
) -> Tuple[str, str]:
    """返回 (system_message, user_message)，供测试或未接入 Registry 时复用。"""
    user = FIXED_TEMPLATE_RECOGNIZE_USER.format(
        chunks_text=chunks_text,
        page_range_hint=page_range_hint,
        volume_type_hint=volume_type_hint or "unknown",
    )
    return FIXED_TEMPLATE_RECOGNIZE_SYSTEM, user
