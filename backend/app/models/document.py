"""文档模型"""

from sqlalchemy import Column, String, Integer, Text, ForeignKey, Enum, Boolean, JSON
from sqlalchemy.orm import relationship
import enum
from app.models.base import BaseModel


class DocumentType(str, enum.Enum):
    """文档类型枚举"""
    TENDER = "tender"                # 招标文件
    BID = "bid"                      # 投标文件
    MATERIAL = "material"            # 素材文档(标书编写)
    REFERENCE_BID = "reference_bid"  # 参考旧标书(标书编写)


class Document(BaseModel):
    """文档模型:存储PDF文档的元数据
    
    一个项目包含两个文档:
    - 招标文件(tender)
    - 投标文件(bid)
    """
    
    __tablename__ = "documents"
    
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目ID"
    )
    doc_type = Column(
        Enum(DocumentType),
        nullable=False,
        comment="文档类型:tender/bid"
    )
    # 投标信封分卷(见 core/bid_envelope.py 档案): NULL=整本(存量默认)
    bid_part = Column(
        String(16),
        nullable=True,
        comment="投标信封卷角色: NULL=整本 / commercial=商务标 / technical=技术标",
    )
    filename = Column(String(255), nullable=False, comment="原始文件名")
    file_path = Column(String(500), nullable=False, comment="文件存储路径")
    file_size = Column(Integer, nullable=False, comment="文件大小(字节)")
    page_count = Column(Integer, nullable=True, comment="页数")
    ocr_image_count = Column(Integer, nullable=True, default=0, comment="OCR处理图片数")
    # 图片识别来源快照: 让计费明细能区分"OCR为0"是无图片/跳过/走VL通道
    image_count = Column(Integer, nullable=True, default=0, comment="解析提取图片总数")
    vl_skipped = Column(Boolean, nullable=True, comment="本次处理是否跳过图片识别(null=历史未知)")
    parsed = Column(Integer, default=0, nullable=False, comment="是否已解析:0/1")
    toc_map = Column(Text, nullable=True, comment="目录页码映射JSON")
    # 完整性双指纹(缺口D): 字节级/逻辑级副本检测, 存量为 None 不回填
    file_hash = Column(String(64), nullable=True, index=True, comment="上传文件字节sha256")
    content_hash = Column(String(64), nullable=True, comment="解析后归一化全文sha256")
    # 文档门禁(解析前要素校验): 存量为 None 表示未经过门禁
    gate_verdict = Column(String(16), nullable=True, comment="门禁结论: pass/suspect/unreadable/encrypted/corrupt/empty")
    gate_result = Column(JSON, nullable=True, comment="门禁校验结果JSON(DocGateResult)")
    gate_ack = Column(Boolean, nullable=False, default=False, comment="用户是否确认警告后继续")
    # 评分结构信号快照(SchemeReader 阶段0): 装配前逐字捕获的边界信号,
    # 存量/无评分区段文档为 None
    scoring_signals = Column(JSON, nullable=True, comment="评分结构边界信号快照JSON")
    scoring_verdict_probe = Column(
        JSON, nullable=True,
        comment="最近一次评分提取的verdict探针JSON(阶段2 VLM-first裁决数据)",
    )
    # 关系
    project = relationship("Project", back_populates="documents")
    chunks = relationship(
        "Chunk",
        back_populates="document",
        cascade="all, delete-orphan"
    )
    requirements = relationship(
        "Requirement",
        back_populates="document",
        cascade="all, delete-orphan"
    )
    bid_responses = relationship(
        "BidResponse",
        back_populates="document",
        cascade="all, delete-orphan"
    )
    
    def __repr__(self) -> str:
        return f"<Document(id={self.id}, type='{self.doc_type}', filename='{self.filename}')>"
