"""串标嫌疑群组模型

存储图聚类识别出的串通嫌疑群组信息。
每个 project 产生 0~N 个群组记录。
"""

from typing import TYPE_CHECKING

from sqlalchemy import JSON, Column, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    pass


class CollusionGroup(BaseModel):
    """串标嫌疑群组表

    代表一组疑似存在串通行为的投标人，由图聚类(S3.5)识别、
    群组研判(S4)分析后生成。

    Attributes:
        project_id: 所属项目ID
        group_index: 项目内递增序号（从0开始）
        member_doc_ids: 成员文档ID列表 [int]
        member_names: 成员文档名/公司名列表 [str]
        risk_level: 群组风险等级 (high/medium/low)
        aggregated_evidence: 按维度聚合的证据列表 (JSON)
        role_analysis: 角色分析 [{doc_id, company_name, role, role_evidence}]
        network_description: 串通网络描述
        collusion_strategy: 串通策略推断
        investigation_focus: 调查建议列表 [str]
        related_pair_ids: 关联的pair标识 ["doc_a_id:doc_b_id"]
        llm_group_analysis: LLM群组研判原始输出 (JSON)
    """

    __tablename__ = "collusion_groups"
    __table_args__ = (
        UniqueConstraint("project_id", "group_index", name="uq_cg_project_group"),
    )

    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目ID"
    )
    group_index = Column(
        Integer,
        nullable=False,
        comment="项目内递增序号"
    )
    member_doc_ids = Column(
        JSON,
        nullable=False,
        comment="成员文档ID列表"
    )
    member_names = Column(
        JSON,
        nullable=False,
        comment="成员文档名/公司名列表"
    )
    risk_level = Column(
        String(20),
        nullable=False,
        default="medium",
        comment="群组风险等级"
    )
    aggregated_evidence = Column(
        JSON,
        nullable=True,
        comment="按维度聚合的证据列表"
    )
    role_analysis = Column(
        JSON,
        nullable=True,
        comment="角色分析结果"
    )
    network_description = Column(
        Text,
        nullable=True,
        comment="串通网络描述"
    )
    collusion_strategy = Column(
        Text,
        nullable=True,
        comment="串通策略推断"
    )
    investigation_focus = Column(
        JSON,
        nullable=True,
        comment="调查建议列表"
    )
    related_pair_ids = Column(
        JSON,
        nullable=True,
        comment="关联的pair标识列表"
    )
    llm_group_analysis = Column(
        JSON,
        nullable=True,
        comment="LLM群组研判原始输出"
    )
    group_findings = Column(
        JSON,
        nullable=True,
        comment="群组级融合证据列表(证据融合S3.6产出)"
    )

    project = relationship("Project", back_populates="collusion_groups")

    def __repr__(self) -> str:
        return (
            f"<CollusionGroup(id={self.id}, project_id={self.project_id}, "
            f"group_index={self.group_index}, risk={self.risk_level}, "
            f"members={self.member_doc_ids})>"
        )
