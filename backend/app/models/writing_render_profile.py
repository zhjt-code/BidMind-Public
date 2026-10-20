"""标书编写 -- 渲染配置模型"""

from sqlalchemy import JSON, Column, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class WritingRenderProfile(BaseModel):
    """渲染配置模型

    存储标书的排版格式参数，一个项目对应一份配置(unique约束)。
    参数从招标文件的格式规定中提取，未明确的使用默认值。
    """

    __tablename__ = "writing_render_profiles"

    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment="所属项目ID(一对一)",
    )
    font_body = Column(
        String(100), default="仿宋", nullable=False, comment="正文字体"
    )
    font_heading = Column(
        String(100), default="黑体", nullable=False, comment="标题字体"
    )
    font_size_body = Column(
        Float, default=12.0, nullable=False, comment="正文字号(pt)"
    )
    font_size_h1 = Column(
        Float, default=22.0, nullable=False, comment="一级标题字号(pt)"
    )
    font_size_h2 = Column(
        Float, default=16.0, nullable=False, comment="二级标题字号(pt)"
    )
    font_size_h3 = Column(
        Float, default=14.0, nullable=False, comment="三级标题字号(pt)"
    )
    line_spacing = Column(
        Float, default=1.5, nullable=False, comment="行距倍数"
    )
    margin_top = Column(
        Float, default=25.4, nullable=False, comment="上边距(mm)"
    )
    margin_bottom = Column(
        Float, default=25.4, nullable=False, comment="下边距(mm)"
    )
    margin_left = Column(
        Float, default=31.8, nullable=False, comment="左边距(mm)"
    )
    margin_right = Column(
        Float, default=31.8, nullable=False, comment="右边距(mm)"
    )
    header_text = Column(
        String(200), nullable=True, comment="页眉文字"
    )
    footer_text = Column(
        String(200), nullable=True, comment="页脚文字"
    )
    page_number_position = Column(
        String(20),
        default="bottom_center",
        nullable=False,
        comment="页码位置: bottom_center/bottom_right",
    )
    cover_template_path = Column(
        String(500), nullable=True, comment="封面模板文件路径"
    )
    source_clause_ids = Column(
        Text, nullable=True, comment="来源约束ID列表(JSON)"
    )
    submission_requirements = Column(
        JSON,
        nullable=True,
        comment="提交/装订/签章/暗标等不可完全自动渲染的格式风险要求(JSON)",
    )

    project = relationship("Project", back_populates="writing_render_profile")

    def __repr__(self) -> str:
        return (
            f"<WritingRenderProfile(id={self.id}, project_id={self.project_id}, "
            f"font_body='{self.font_body}')>"
        )
