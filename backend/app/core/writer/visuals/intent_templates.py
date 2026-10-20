"""具名图模板默认节点/图层。

职责: 为 flowchart/layered 提供可复用的模板默认值。
不做: 不编译 IR、不渲染、不访问数据库。
"""

from __future__ import annotations

FLOWCHART_TEMPLATES: dict[str, list[str]] = {
    "pdca": ["计划", "执行", "检查", "改进"],
    "incident_loop": ["发现", "上报", "处置", "复核", "关闭"],
    "three_inspection": ["自检", "互检", "专检", "整改闭合"],
    "construction_process": ["施工准备", "测量放线", "主体施工", "验收"],
}
LAYERED_TEMPLATES: dict[str, list[str]] = {
    "architecture": ["基础设施层", "数据层", "服务层", "应用层"],
    "modules": ["基础模块", "业务模块", "应用模块"],
    "qms": ["质量目标", "组织与制度", "检查与改进"],
    "service": ["人员", "流程", "制度", "资源", "工具"],
    "pyramid": ["总体目标", "建设路径", "具体措施"],
    "qhse": ["质量目标", "安全目标", "环保目标", "检查与改进"],
    "construction_safety": ["目标", "组织", "制度", "防护", "应急", "检查"],
}


def flowchart_template_labels(template_id: str | None) -> list[str]:
    return list(FLOWCHART_TEMPLATES.get((template_id or "").strip(), []))


def layered_template_layers(template_id: str | None) -> list[dict[str, list[str] | str]]:
    labels = LAYERED_TEMPLATES.get((template_id or "").strip(), [])
    return [{"label": label, "items": []} for label in labels]


__all__ = [
    "FLOWCHART_TEMPLATES",
    "LAYERED_TEMPLATES",
    "flowchart_template_labels",
    "layered_template_layers",
]
