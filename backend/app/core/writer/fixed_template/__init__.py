"""固定模板 LLM-first 事实层入口。

职责: 对外暴露固定模板目录构建与标题匹配的轻量入口。
不做: 不访问数据库、不生成章节正文、不维护 LLM Center 元数据。
"""

from app.core.writer.fixed_template.catalog import (
    build_fixed_template_catalog,
    build_fixed_template_catalog_async,
)
from app.core.writer.fixed_template.models import FixedTemplateEntry, TemplateMatchResult
from app.core.writer.fixed_template.titles import (
    infer_fixed_template_key,
    match_template_title,
    normalize_template_title,
)

__all__ = [
    "FixedTemplateEntry",
    "TemplateMatchResult",
    "build_fixed_template_catalog",
    "build_fixed_template_catalog_async",
    "infer_fixed_template_key",
    "match_template_title",
    "normalize_template_title",
]
