"""串标对比结果模型

存储两份投标文件之间的多维度对比分析结果。
每个 project 产生 C(N_bid, 2) 条记录。
"""

from typing import TYPE_CHECKING
from sqlalchemy import Column, DateTime, Integer, ForeignKey, Float, SmallInteger, String, Text, JSON, UniqueConstraint
from sqlalchemy.orm import relationship
import enum

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.document import Document


class CollusionRiskLevel(str, enum.Enum):
    """串标风险等级枚举"""
    HIGH = "high"                # 高风险：单独可认定串标嫌疑
    MEDIUM = "medium"            # 中风险：强力佐证
    LOW = "low"                  # 低风险：辅助参考
    NONE = "none"                # 无风险：未发现异常
    INCONCLUSIVE = "inconclusive"  # 无法判定：分析超时或失败


class CollusionResult(BaseModel):
    """串标对比结果表

    记录两份投标文件之间的全维度对比分析结果。
    
    Attributes:
        project_id: 所属项目ID
        doc_a_id: 投标文件A的document_id
        doc_b_id: 投标文件B的document_id
        overall_risk: 综合风险等级
        similarity_score: 向量相似度预筛选分数（0-1）
        d1_personnel_overlap: D1人员重叠分数（0-1）
        d2_company_relation: D2企业关联分数（0-1）
        d3_text_similarity: D3文本雷同分数（0-1）
        d4_price_pattern: D4报价异常分数（0-1）
        d5_qualification_overlap: D5资质业绩重叠分数（0-1）
        d6_format_similarity: D6文档格式相似分数（0-1）
        d7_abnormal_consistency: D7异常一致性分数（0-1）
        d8_metadata_similarity: D8元数据相似分数（0-1）
        findings: 各维度具体发现（JSON列表）
        evidence_chunks: 高相似度chunk对（JSON列表）
        llm_analysis: LLM完整分析结果（JSON对象）
        analysis_mode: 分析模式标注（full/simplified/algorithm_only）
    """

    __tablename__ = "collusion_results"
    __table_args__ = (
        UniqueConstraint("project_id", "doc_a_id", "doc_b_id", name="uq_cr_pair"),
    )

    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目ID"
    )
    doc_a_id = Column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        comment="投标文件A的文档ID"
    )
    doc_b_id = Column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        comment="投标文件B的文档ID"
    )

    # 综合风险
    overall_risk = Column(
        String(20),
        nullable=False,
        default=CollusionRiskLevel.INCONCLUSIVE,
        comment="综合风险等级"
    )

    # 向量预筛选分数
    similarity_score = Column(Float, nullable=True, comment="向量相似度预筛选分数（0-1）")

    # 八维度得分（0-1，None表示未分析）
    d1_personnel_overlap = Column(Float, nullable=True, comment="D1人员重叠分数")
    d2_company_relation = Column(Float, nullable=True, comment="D2企业关联分数")
    d3_text_similarity = Column(Float, nullable=True, comment="D3文本雷同分数")
    d4_price_pattern = Column(Float, nullable=True, comment="D4报价异常分数")
    d5_qualification_overlap = Column(Float, nullable=True, comment="D5资质业绩重叠分数")
    d6_format_similarity = Column(Float, nullable=True, comment="D6文档格式相似分数")
    d7_abnormal_consistency = Column(Float, nullable=True, comment="D7异常一致性分数")
    d8_metadata_similarity = Column(Float, nullable=True, comment="D8元数据相似分数")

    # 详细分析数据
    findings = Column(JSON, nullable=True, comment="各维度具体发现列表")
    evidence_chunks = Column(JSON, nullable=True, comment="高相似度chunk对列表")
    llm_analysis = Column(JSON, nullable=True, comment="LLM完整分析结果")

    # 分析模式标注
    analysis_mode = Column(
        String(50),
        nullable=False,
        default="algorithm_only",
        comment="分析模式（full/simplified/algorithm_only/grouped_parallel/grouped_parallel_recovered/skipped）"
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
    project = relationship("Project", back_populates="collusion_results")
    doc_a = relationship("Document", foreign_keys=[doc_a_id])
    doc_b = relationship("Document", foreign_keys=[doc_b_id])

    def __repr__(self) -> str:
        return (
            f"<CollusionResult(id={self.id}, project_id={self.project_id}, "
            f"doc_a={self.doc_a_id}, doc_b={self.doc_b_id}, risk={self.overall_risk})>"
        )
