"""门禁要素特征库

职责: 定义招标/投标文件的加权要素词表(正则+权重+强特征标记)。
不做: 不做打分与verdict判定(由gate_engine负责)，不做文本采样。

权重设计说明:
- 强特征(strong=True): 正规标书文件几乎必有、非标书文档几乎不会有的要素，
  缺失时会进入怀疑理由(missing_features)
- 目录加成在gate_engine中处理: 特征命中目录条目时按1.5倍权重计分
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class GateFeature:
    """门禁要素特征"""

    key: str
    label: str  # 用户可读名称(用于hit/missing列表与怀疑理由)
    pattern: re.Pattern
    weight: float
    strong: bool = False


def _c(p: str) -> re.Pattern:
    return re.compile(p)


# 招标文件要素(总权重约96分)
TENDER_FEATURES: tuple[GateFeature, ...] = (
    GateFeature(
        key="announcement",
        label="招标公告/投标邀请",
        pattern=_c(r"招标公告|投标邀请|竞争性磋商|竞争性谈判|询价公告|比选公告|磋商邀请|谈判邀请"),
        weight=12,
        strong=True,
    ),
    GateFeature(
        key="bidder_notice",
        label="投标人须知",
        pattern=_c(r"投标人须知|供应商须知|应答人须知|磋商须知|谈判须知|响应人须知"),
        weight=16,
        strong=True,
    ),
    GateFeature(
        key="evaluation_method",
        label="评标办法/评分标准",
        pattern=_c(r"评标办法|评审办法|评分标准|评分细则|评标标准|评审因素|评审标准"),
        weight=16,
        strong=True,
    ),
    GateFeature(
        key="project_number",
        label="招标编号/项目编号",
        pattern=_c(r"招标编号|项目编号|采购编号|标段编号|招标项目编号"),
        weight=8,
    ),
    GateFeature(
        key="deadline",
        label="开标时间/投标截止时间",
        pattern=_c(r"开标时间|投标截止|递交.{0,6}截止|应答截止|响应截止|开启时间"),
        weight=10,
        strong=True,
    ),
    GateFeature(
        key="purchaser",
        label="招标人/采购人/代理机构",
        pattern=_c(r"招标人|采购人|招标代理|采购代理"),
        weight=8,
    ),
    GateFeature(
        key="deposit",
        label="保证金条款",
        pattern=_c(r"投标保证金|磋商保证金|履约保证金"),
        weight=6,
    ),
    GateFeature(
        key="qualification",
        label="资格/资质要求",
        pattern=_c(r"资格要求|资质要求|资格审查|资格条件|投标人资格|供应商资格"),
        weight=8,
    ),
    GateFeature(
        key="contract_terms",
        label="合同条款",
        pattern=_c(r"合同条款|合同主要条款|合同格式|拟签订.{0,4}合同"),
        weight=4,
    ),
    GateFeature(
        key="technical_requirements",
        label="技术规格/采购需求",
        pattern=_c(r"技术规格|技术要求|技术需求|采购需求|工程量清单|服务要求|项目需求"),
        weight=8,
    ),
)

# 投标文件要素(总权重约84分)
BID_FEATURES: tuple[GateFeature, ...] = (
    GateFeature(
        key="bid_letter",
        label="投标函/响应函",
        pattern=_c(r"投标函|响应函|报价函|投标书|磋商响应"),
        weight=14,
        strong=True,
    ),
    GateFeature(
        key="authorization",
        label="法定代表人授权委托书",
        pattern=_c(r"法定代表人身份证明|授权委托书|法人授权|法定代表人授权"),
        weight=12,
        strong=True,
    ),
    GateFeature(
        key="price_sheet",
        label="报价一览表/投标报价",
        pattern=_c(r"开标一览表|报价一览表|投标报价|报价明细|分项报价|报价表"),
        weight=12,
        strong=True,
    ),
    GateFeature(
        key="deviation",
        label="商务/技术响应偏离表",
        pattern=_c(r"商务响应|商务偏离|技术偏离|偏离表|响应偏离|点对点响应"),
        weight=8,
    ),
    GateFeature(
        key="solution",
        label="技术/实施方案",
        pattern=_c(r"技术方案|施工组织设计|实施方案|服务方案|项目实施|技术标"),
        weight=8,
    ),
    GateFeature(
        key="qualification_proof",
        label="资格证明文件",
        pattern=_c(r"资格证明|营业执照|资质证书|信用承诺|无重大违法"),
        weight=8,
    ),
    GateFeature(
        key="seal_signature",
        label="签字盖章要素",
        pattern=_c(r"盖章|公章|签字|法定代表人.{0,4}签"),
        weight=6,
    ),
    GateFeature(
        key="deposit",
        label="投标保证金",
        pattern=_c(r"投标保证金|磋商保证金"),
        weight=4,
    ),
    GateFeature(
        key="project_number",
        label="招标编号呼应",
        pattern=_c(r"招标编号|项目编号|采购编号"),
        weight=6,
    ),
    GateFeature(
        key="commitment",
        label="投标/服务承诺",
        pattern=_c(r"投标承诺|响应承诺|服务承诺|质量承诺"),
        weight=6,
    ),
)


def get_features_for_doc_type(doc_type: str) -> tuple[GateFeature, ...]:
    """按文档类型返回要素特征集"""
    if doc_type == "tender":
        return TENDER_FEATURES
    return BID_FEATURES
