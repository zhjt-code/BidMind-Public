"""标书编写 -- 渲染产物与一致性缓存模型"""

from sqlalchemy import Column, String, Integer, Text, ForeignKey, DateTime, LargeBinary
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class WritingRenderArtifact(BaseModel):
    """当前渲染快照

    每个项目仅保留一份当前版本渲染产物，用于：
    1. 预览页复用同一版 HTML
    2. 下载接口复用同一版 DOCX
    3. 一致性检查结果按 render_version 缓存
    """

    __tablename__ = "writing_render_artifacts"

    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
        comment="所属项目ID(一对一)",
    )
    render_version = Column(
        Integer,
        nullable=False,
        default=1,
        comment="当前渲染版本号，每次显式重新渲染递增",
    )
    preview_html = Column(
        Text,
        nullable=True,
        comment="当前渲染版本对应的HTML预览快照",
    )
    docx_blob = Column(
        LargeBinary,
        nullable=True,
        comment="当前渲染版本对应的DOCX字节流",
    )
    docx_size = Column(
        Integer,
        nullable=True,
        comment="DOCX字节长度，用于诊断",
    )
    rendered_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="当前渲染版本生成时间",
    )
    consistency_issues_json = Column(
        Text,
        nullable=True,
        comment="一致性检查结果(JSON字符串)",
    )
    consistency_checked_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="当前渲染版本的一致性检查完成时间",
    )
    consistency_render_version = Column(
        Integer,
        nullable=True,
        comment="一致性结果对应的render_version",
    )

    project = relationship("Project", back_populates="writing_render_artifact")

    def __repr__(self) -> str:
        return (
            f"<WritingRenderArtifact(id={self.id}, project_id={self.project_id}, "
            f"render_version={self.render_version})>"
        )
