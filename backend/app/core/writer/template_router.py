"""data_fill 表格模板路由（B5.7）。

基于 clause.structure_constraint 将常见表格类型路由到 Jinja2 模板，
跳过 LLM 调用。非命中类型仍走原 LLM 路径。

首批四类（D-020）：人员表 / 业绩表 / 资质表 / 设备表。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from app.core.config import settings

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)

_KEYWORD_TO_TEMPLATE: dict[str, str] = {
    "人员": "personnel.j2",
    "团队": "personnel.j2",
    "项目经理": "personnel.j2",
    "业绩": "performance.j2",
    "类似项目": "performance.j2",
    "合同业绩": "performance.j2",
    "资质": "qualification.j2",
    "证书": "qualification.j2",
    "认证": "qualification.j2",
    "设备": "equipment.j2",
    "机械": "equipment.j2",
    "仪器": "equipment.j2",
}


def should_use_template(project_id: int | str | None) -> bool:
    """灰度判定：是否启用模板化。"""
    ratio = settings.WRITER_DATA_FILL_TEMPLATE_RATIO
    if ratio <= 0.0:
        return False
    if ratio >= 1.0:
        return True
    if project_id is None:
        return False
    h = int(hashlib.md5(str(project_id).encode()).hexdigest(), 16) % 100
    return h < int(ratio * 100)


def match_template(title: str, structure_constraint: str | None = None) -> str | None:
    """尝试匹配模板名。返回模板文件名或 None（走 LLM）。"""
    combined = f"{title} {structure_constraint or ''}".lower()
    for keyword, template_name in _KEYWORD_TO_TEMPLATE.items():
        if keyword in combined:
            return template_name
    return None


def render_template(
    template_name: str,
    items: list[dict[str, Any]],
    title: str = "",
) -> str:
    """渲染 Jinja2 模板，返回 HTML 内容。"""
    tpl = _env.get_template(template_name)
    return tpl.render(items=items, title=title)
