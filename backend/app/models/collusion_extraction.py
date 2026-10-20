"""串标信息提取结果模型

存储每份投标文件的关键结构化信息提取结果（S1阶段产物）。
每个 project 的每份文件产生1条记录。
"""

from typing import TYPE_CHECKING
from sqlalchemy import Column, DateTime, Float, Integer, ForeignKey, JSON, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.document import Document


class CollusionExtraction(BaseModel):
    """文档信息提取结果表

 存储 S1b 阶段从每份投标文件中提取的结构化信息，
 以及 S1a 阶段（可选）从招标文件中提取的格式要求。

 Attributes:
 project_id: 所属项目ID
 document_id: 被提取的文档ID
 doc_type: 文档类型（tender=招标/bid=投标）
 company_info: 企业基本信息（JSON）
 personnel_info: 关键人员信息（JSON列表）
 price_info: 报价信息（JSON）
 technical_features: 技术方案特征（JSON）
 qualification_info: 资质业绩信息（JSON列表）
 guarantee_info: 保证金信息（JSON）
 metadata_info: PDF元数据（创建者/时间/软件等，JSON）
 subcontractors_info: 分包商/供应商列表（LLM 业务字段， 从 metadata_info 拆出）
 format_requirements: 招标格式要求（仅招标文件，JSON）
 extraction_status: 提取状态（success/failed/empty）
 """

    __tablename__ = "collusion_extractions"
    __table_args__ = (
        UniqueConstraint("project_id", "document_id", name="uq_ce_project_doc"),
    )

    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目ID"
    )
    document_id = Column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        comment="被提取的文档ID"
    )
    doc_type = Column(
        String(10),
        nullable=False,
        default="bid",
        comment="文档类型（tender=招标，bid=投标）"
    )

    # S1b 提取结果（投标文件）
    company_info = Column(JSON, nullable=True, comment="企业基本信息")
    personnel_info = Column(JSON, nullable=True, comment="关键人员信息列表")
    price_info = Column(JSON, nullable=True, comment="报价信息")
    technical_features = Column(JSON, nullable=True, comment="技术方案特征")
    qualification_info = Column(JSON, nullable=True, comment="资质业绩信息列表")
    guarantee_info = Column(JSON, nullable=True, comment="保证金信息")
    metadata_info = Column(JSON, nullable=True, comment="PDF元数据（D8维度）")
    # (2026-04-24): 独立承载 LLM 业务字段 subcontractors；修复前此数据
    # 被错位写入 metadata_info 子字典。迁移脚本已把存量数据拷贝到此列。
    subcontractors_info = Column(JSON, nullable=True, comment="分包商/供应商列表（LLM业务字段）")

    # S1a 提取结果（仅招标文件使用）
    format_requirements = Column(JSON, nullable=True, comment="招标格式要求（白名单过滤器）")

    # 提取状态
    extraction_status = Column(
        String(10),
        nullable=False,
        default="success",
        comment="提取状态（success/failed/empty）"
    )
    error_message = Column(
        String(500),
        nullable=True,
        comment="提取失败原因（仅status=failed时有值）"
    )

    # B1: 失败合同字段（failure_contract §4.1）
    failure_state = Column(String(32), nullable=False, server_default="completed", comment="五状态枚举")
    failure_reason = Column(Text, nullable=True, comment="人类可读失败原因")
    confidence = Column(Float, nullable=True, comment="Gate 综合置信度 0.0-1.0")
    gate_trace = Column(JSON, nullable=True, comment="每道 Gate verdict 列表")
    review_required = Column(SmallInteger, nullable=False, server_default="0", comment="是否需人工复核")
    reviewed_at = Column(DateTime(timezone=True), nullable=True, comment="人工复核完成时间")
    reviewed_by = Column(Integer, nullable=True, comment="复核人 user_id")

    # 关系
    project = relationship("Project", back_populates="collusion_extractions")
    document = relationship("Document")

    def __repr__(self) -> str:
        return (
            f"<CollusionExtraction(id={self.id}, project_id={self.project_id}, "
            f"document_id={self.document_id}, status={self.extraction_status})>"
        )
