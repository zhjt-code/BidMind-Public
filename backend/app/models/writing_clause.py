"""标书编写 -- 编写约束模型"""

import enum
from sqlalchemy import Column, String, Integer, Float, Text, ForeignKey, Enum, JSON
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector
from app.models.base import BaseModel


class WritingClauseLayer(str, enum.Enum):
    """约束层级枚举

    三层约束体系:
    - hard_constraint: 硬约束(废标条款/固定模板/格式规定/提交规则)
    - structure_constraint: 结构约束(目录规则/必备材料/内容要求/表格格式)
    - generation_guide: 生成引导(评分标准/项目背景/强调重点/差异化提示/行业画像)
    """
    hard_constraint = "hard_constraint"
    structure_constraint = "structure_constraint"
    generation_guide = "generation_guide"


class WritingClause(BaseModel):
    """编写约束模型

    存储从招标文件中提取的三层约束信息，用于指导标书编写。

    sub_type 完整值域:
    - hard_constraint: disqualification / fixed_template / format_rule / submission_rule / submission_notice
    - structure_constraint: directory_rule / material_required / content_required / table_format
    - generation_guide: scoring_criterion / project_context / emphasis_point
                        / differentiation_hint / industry_profile
    """

    __tablename__ = "writing_clauses"

    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目ID",
    )
    document_id = Column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        comment="来源文档ID",
    )
    layer = Column(
        Enum(WritingClauseLayer),
        nullable=False,
        comment="约束层级",
    )
    sub_type = Column(
        String(50),
        nullable=False,
        comment="约束子类型",
    )
    content = Column(Text, nullable=False, comment="约束内容(结构化)")
    raw_text = Column(Text, nullable=True, comment="招标原文")
    source_page = Column(
        Integer,
        nullable=True,
        comment="默认打开页(批次起始页/LLM返回的精确页); UI不用于精确展示",
    )
    source_page_start = Column(
        Integer,
        nullable=True,
        comment="条款所属批次起始页(诚实页范围下界)",
    )
    source_page_end = Column(
        Integer,
        nullable=True,
        comment="条款所属批次结束页(诚实页范围上界)",
    )
    score_weight = Column(Float, nullable=True, comment="评分权重")
    metadata_json = Column(
        JSON,
        nullable=True,
        comment="结构化补充信息(JSON): 评分梯度/材料类型/适用章节/证据要求等",
    )
    confirmed = Column(
        Integer, default=0, nullable=False, comment="是否已确认: 0/1"
    )
    is_manual = Column(
        Integer, default=0, nullable=False, comment="是否为人工新增: 0/1"
    )
    sort_order = Column(
        Integer, default=0, nullable=False, comment="排序序号"
    )
    embedding = Column(
        Vector(1024),
        nullable=True,
        comment="约束内容向量(用于语义匹配)",
    )

    project = relationship("Project", back_populates="writing_clauses")

    def __repr__(self) -> str:
        return (
            f"<WritingClause(id={self.id}, layer='{self.layer}', "
            f"sub_type='{self.sub_type}')>"
        )
