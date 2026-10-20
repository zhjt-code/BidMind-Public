"""标书编写 -- 素材模型"""

import enum
from sqlalchemy import Column, String, Integer, Text, ForeignKey, Enum, Float, DateTime
from sqlalchemy.orm import relationship, backref
from app.models.base import BaseModel


class MaterialType(str, enum.Enum):
    """素材物理类型枚举(描述文件格式维度)"""
    text = "text"
    image = "image"
    table = "table"
    certificate = "certificate"
    personnel = "personnel"
    performance = "performance"
    old_bid_section = "old_bid_section"
    template_fragment = "template_fragment"


class DocumentBusinessType(str, enum.Enum):
    """素材业务类型枚举(描述投标业务语义维度)

    两级体系: 本枚举为大类(12种), resolved_label 字段存细分标签(自由文本)。
    大类用于匹配阶段的强规则门禁, 细分标签用于精确定位和人工审核。
    """
    identity_document = "identity_document"
    business_license = "business_license"
    bank_permit = "bank_permit"
    qualification_cert = "qualification_cert"
    financial_document = "financial_document"
    tax_social_proof = "tax_social_proof"
    performance_proof = "performance_proof"
    authorization = "authorization"
    company_profile = "company_profile"
    personnel_info = "personnel_info"
    equipment_info = "equipment_info"
    other = "other"


class MaterialProcessingStatus(str, enum.Enum):
    """素材处理状态枚举

    两阶段流程: save(保存) -> processing(批处理中) -> completed/failed
    """
    saved = "saved"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class MaterialRole(str, enum.Enum):
    """素材角色。

    - normal: 普通素材，可直接参与匹配与生成
    - split_parent: 综合 PDF 的父容器，默认不参与消费
    - split_child: 综合 PDF 拆出的子素材
    """

    normal = "normal"
    split_parent = "split_parent"
    split_child = "split_child"


class SplitReviewStatus(str, enum.Enum):
    """拆分复核状态。"""

    none = "none"
    needs_review = "needs_review"
    confirmed = "confirmed"
    ignored = "ignored"


class WritingMaterial(BaseModel):
    """素材模型

    存储标书编写所需的各类素材，包括文本、图片、表格、证书等。
    metadata_tags 和 chunk_ids 以 JSON 字符串形式存储。

    说明:
    - `resolved_label` 是素材业务类型的全链路唯一真源, 由文件名预判
      与 VL/OCR 内容识别交叉校验后决议产生, 供匹配/生成/渲染统一消费。
    - `metadata_tags` 是 LLM 打标的语义标签, 由匹配和生成共同消费。
    - `chunk_ids` 仅保留为索引结果的兼容引用；生成阶段的主文本证据源
      为 `writing_material_chunks` 表，而非这里缓存的 JSON 列表。

    processing_status 支持两阶段上传流程:
      阶段1(save): 文件保存到磁盘 + DB记录(status=saved)
      阶段2(batch): 统一解析/VL识别/向量化/打标(status=processing -> completed)

    标签决议链路:
      文件名 -> file_name_label (规则解析)
      VL/OCR -> content_label (内容识别)
      交叉校验 -> resolved_label (最终决议) + label_conflict + label_source
    """

    __tablename__ = "writing_materials"

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
        nullable=True,
        comment="来源文档ID(手动上传时可为空)",
    )
    parent_material_id = Column(
        Integer,
        ForeignKey("writing_materials.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="父素材ID(综合PDF拆分出的子素材指向原始父素材)",
    )
    material_type = Column(
        Enum(MaterialType),
        nullable=False,
        comment="素材类型",
    )
    title = Column(String(500), nullable=False, comment="素材标题")
    display_title = Column(
        String(500),
        nullable=True,
        comment="用户修正后的展示标题",
    )
    content = Column(Text, nullable=True, comment="素材文本内容")
    file_path = Column(String(500), nullable=True, comment="文件存储路径")
    metadata_tags = Column(
        Text, nullable=True, comment="元数据标签(JSON)"
    )
    source_page = Column(Integer, nullable=True, comment="来源页码")
    source_page_start = Column(Integer, nullable=True, comment="来源PDF起始页")
    source_page_end = Column(Integer, nullable=True, comment="来源PDF结束页")
    chunk_ids = Column(
        Text, nullable=True, comment="关联的向量chunk ID列表(JSON)"
    )
    processing_status = Column(
        String(20),
        nullable=False,
        server_default="completed",
        comment="处理状态: saved/processing/completed/failed",
    )
    # 计费计量字段: 素材是写标业务的输入资源,页数/OCR张数
    # 由批处理阶段写入,BillingGuard._collect_metrics 聚合计费
    page_count = Column(
        Integer, nullable=True, server_default="0",
        comment="素材文件页数(批处理解析时写入,计费用)",
    )
    ocr_image_count = Column(
        Integer, nullable=True, server_default="0",
        comment="OCR处理图片数(批处理识别时写入,计费用)",
    )
    file_name_label = Column(
        String(100), nullable=True,
        comment="文件名规则解析的业务类型细分标签",
    )
    structure_hint_label = Column(
        String(100), nullable=True,
        comment="结构分析得到的业务类型提示标签",
    )
    content_label = Column(
        String(100), nullable=True,
        comment="VL/OCR/正文识别的业务类型细分标签",
    )
    resolved_label = Column(
        String(100), nullable=True,
        comment="最终决议的业务类型标签(全链路唯一真源)",
    )
    label_conflict = Column(
        String(20), nullable=True,
        comment="标签冲突状态: matched/conflicted/uncertain/needs_review",
    )
    label_source = Column(
        String(20), nullable=True,
        comment="决议来源: filename/vl/llm/rule",
    )
    split_confidence = Column(
        Float, nullable=True, comment="拆分置信度(0-1)",
    )
    material_role = Column(
        String(20),
        nullable=False,
        server_default=MaterialRole.normal.value,
        comment="素材角色: normal/split_parent/split_child",
    )
    split_review_status = Column(
        String(20),
        nullable=False,
        server_default=SplitReviewStatus.none.value,
        comment="拆分复核状态: none/needs_review/confirmed/ignored",
    )
    kb_source_id = Column(
        Integer,
        ForeignKey("kb_documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="知识库来源文档ID(影子复制溯源)",
    )
    kb_source_version = Column(
        String(64),
        nullable=True,
        comment="引用时的知识库版本号快照",
    )
    kb_snapshot_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="引用快照时间",
    )
    kb_source_chunk_ids = Column(
        Text,
        nullable=True,
        comment="引用的知识库chunk ID列表(JSON)",
    )
    kb_atom_id = Column(
        Integer,
        ForeignKey("kb_atoms.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="知识库原子素材ID(影子复制溯源)",
    )

    project = relationship("Project", back_populates="writing_materials")
    kb_source = relationship("KbDocument", foreign_keys=[kb_source_id])
    kb_atom = relationship("KbAtom", foreign_keys=[kb_atom_id])
    parent_material = relationship(
        "WritingMaterial",
        remote_side="WritingMaterial.id",
        backref=backref("split_children", passive_deletes=True),
        foreign_keys=[parent_material_id],
    )

    def __repr__(self) -> str:
        return (
            f"<WritingMaterial(id={self.id}, type='{self.material_type}', "
            f"title='{self.title}', resolved_label='{self.resolved_label}', "
            f"role='{self.material_role}')>"
        )
