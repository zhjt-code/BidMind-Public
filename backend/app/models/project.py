"""项目模型"""

import enum
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Column, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    pass


class ProjectStatus(str, enum.Enum):
    """项目状态枚举"""
    # 共享状态
    CREATED = "created"              # 已创建
    UPLOADING = "uploading"          # 上传中
    PARSING = "parsing"              # 解析中
    PARSED = "parsed"                # 就绪态: 所需文档已处理完,等待下一计费节点
    EXTRACTING = "extracting"        # 提取中(任务运行态)
    EXTRACTED = "extracted"          # 就绪态: 约束已提取,等待人工确认
    # 投标审查专属
    REVIEWING = "reviewing"          # 审查中
    # 标书编写专属
    OUTLINING = "outlining"          # 任务运行态: 目录生成中
    OUTLINED = "outlined"            # 就绪态: 目录已生成,等待编辑/上传素材/触发匹配
    MATCHING = "matching"            # 任务运行态: 素材匹配中
    MATCHED = "matched"              # 就绪态: 匹配完成,等待确认绑定
    WRITING = "writing"              # 任务运行态: 内容生成中
    COMPOSED = "composed"            # 就绪态: 内容生成完成,等待编辑/触发渲染
    FORMATTING = "formatting"        # 格式渲染中
    # 共享终态
    COMPLETED = "completed"          # 已完成
    FAILED = "failed"                # 失败


class ProjectType(str, enum.Enum):
    """项目类型枚举"""
    BID_REVIEW = "bid_review"            # 投标审查（默认，向后兼容）
    COLLUSION_CHECK = "collusion_check"  # 串标审查
    BID_WRITING = "bid_writing"          # 标书编写


class Project(BaseModel):
    """项目模型:管理一次完整的审查任务
    
    一个项目包含:
    - 1个招标文件
    - 1个投标文件（投标审查）或2-5个投标文件（串标审查）
    - N个招标要求
    - M个投标响应
    - K个审查结果
    
    Attributes:
        owner_id: 项目所有者ID
        owner: 项目所有者关系
        project_type: 项目类型（投标审查/串标审查）
    """
    
    __tablename__ = "projects"
    
    name = Column(String(200), nullable=False, comment="项目名称")
    description = Column(String(1000), nullable=True, comment="项目描述")
    status = Column(
        Enum(ProjectStatus),
        default=ProjectStatus.CREATED,
        nullable=False,
        comment="项目状态"
    )
    project_type = Column(
        Enum(ProjectType),
        default=ProjectType.BID_REVIEW,
        nullable=False,
        comment="项目类型（bid_review=投标审查，collusion_check=串标审查）"
    )
    error_message = Column(
        String(1000),
        nullable=True,
        comment="任务失败原因（status=failed时记录具体原因）"
    )
    collusion_summary = Column(
        JSON,
        nullable=True,
        comment="串标项目级综合结论与覆盖统计快照"
    )
    extraction_warnings = Column(
        JSON,
        nullable=True,
        comment="招标要求提取阶段的警告信息（如分块失败提示）"
    )
    knowledge_usage_config = Column(
        JSON,
        nullable=True,
        comment=(
            "项目级知识库使用配置: enabled / flow / "
            "selected_kb_document_ids / usage_policy / "
            "confirmed_by_user / confirmed_at"
        ),
    )

    run_mode = Column(
        String(20),
        nullable=False,
        default="guided",
        server_default="guided",
        comment="运行模式: guided=人工确认 / express=一步到位",
    )

    # 所有者关联
    owner_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,  # 允许为空，兼容历史数据
        index=True,
        comment="所有者ID"
    )
    
    # 关系
    owner = relationship("User", back_populates="projects")
    documents = relationship(
        "Document",
        back_populates="project",
        cascade="all, delete-orphan"
    )
    requirements = relationship(
        "Requirement",
        back_populates="project",
        cascade="all, delete-orphan"
    )
    review_results = relationship(
        "ReviewResult",
        back_populates="project",
        cascade="all, delete-orphan"
    )
    collusion_results = relationship(
        "CollusionResult",
        back_populates="project",
        cascade="all, delete-orphan"
    )
    collusion_extractions = relationship(
        "CollusionExtraction",
        back_populates="project",
        cascade="all, delete-orphan"
    )
    collusion_groups = relationship(
        "CollusionGroup",
        back_populates="project",
        cascade="all, delete-orphan"
    )
    writing_clauses = relationship(
        "WritingClause",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    writing_outline_nodes = relationship(
        "WritingOutlineNode",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    writing_materials = relationship(
        "WritingMaterial",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    writing_bindings = relationship(
        "WritingBinding",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    writing_render_profile = relationship(
        "WritingRenderProfile",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )
    writing_render_artifact = relationship(
        "WritingRenderArtifact",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )

    def __repr__(self) -> str:
        return f"<Project(id={self.id}, name='{self.name}', status='{self.status}')>"
