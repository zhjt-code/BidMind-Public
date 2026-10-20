"""串标 S1 提取路由与聚合工具。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExtractionRouteConfig:
    """单路提取配置。"""

    route_name: str
    label: str
    keywords: tuple[str, ...]
    target_fields: tuple[str, ...]
    max_chars: int
    front_page_limit: int = 0
    include_image_chunks: bool = False


EXTRACTION_ROUTE_CONFIGS: tuple[ExtractionRouteConfig, ...] = (
    ExtractionRouteConfig(
        route_name="entity",
        label="企业与人员",
        keywords=(
            "法定代表人",
            "公司名称",
            "单位名称",
            "联系人",
            "联系电话",
            "注册地址",
            "统一社会信用代码",
            "项目经理",
            "技术负责人",
            "人员配置",
            "身份证",
            "证书编号",
        ),
        target_fields=("company_info", "personnel_list"),
        max_chars=9000,
        front_page_limit=8,
        include_image_chunks=True,
    ),
    ExtractionRouteConfig(
        route_name="pricing",
        label="报价与商务",
        keywords=(
            "投标总价",
            "报价",
            "报价表",
            "报价明细",
            "总价",
            "分项",
            "投标保证金",
            "保证金",
            "投标有效期",
            "商务条款",
        ),
        target_fields=("price_info",),
        max_chars=8000,
        front_page_limit=4,
        include_image_chunks=False,
    ),
    ExtractionRouteConfig(
        route_name="qualification",
        label="资质与业绩",
        keywords=(
            "资质",
            "资格",
            "证书",
            "业绩",
            "类似项目",
            "合同",
            "分包",
            "供应商",
            "建造师",
            "安全生产许可证",
        ),
        target_fields=("qualifications", "past_projects", "subcontractors"),
        max_chars=9000,
        front_page_limit=3,
        include_image_chunks=True,
    ),
    ExtractionRouteConfig(
        route_name="technical",
        label="技术方案",
        keywords=(
            "技术方案",
            "施工方案",
            "实施方案",
            "服务方案",
            "组织方案",
            "响应方案",
            "项目理解",
            "技术路线",
            "工期",
            "质量保证",
        ),
        target_fields=("technical_highlights",),
        max_chars=10000,
        front_page_limit=2,
        include_image_chunks=False,
    ),
    ExtractionRouteConfig(
        route_name="evidence",
        label="图片证据",
        keywords=(),
        target_fields=(
            "company_info",
            "personnel_list",
            "qualifications",
            "past_projects",
            "subcontractors",
        ),
        max_chars=7000,
        front_page_limit=0,
        include_image_chunks=True,
    ),
)


def get_extraction_route(route_name: str) -> ExtractionRouteConfig | None:
    for config in EXTRACTION_ROUTE_CONFIGS:
        if config.route_name == route_name:
            return config
    return None


def build_empty_extraction() -> dict[str, Any]:
    return {
        "company_info": None,
        "personnel_list": [],
        "price_info": None,
        "technical_highlights": [],
        "qualifications": [],
        "past_projects": [],
        "subcontractors": [],
        "metadata_info": {
            "extraction_trace": {
                "route_order": [],
                "routes": {},
                "list_field_traces": {},
            }
        },
    }


def merge_route_extractions(
    *,
    route_outputs: dict[str, dict[str, Any]],
    route_context_meta: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """按字段路由偏好合并多路提取结果，并保留来源 trace。"""
    merged = build_empty_extraction()
    trace = merged["metadata_info"]["extraction_trace"]
    trace["route_order"] = list(route_outputs.keys())

    company_trace: dict[str, dict[str, Any]] = {}
    price_trace: dict[str, dict[str, Any]] = {}
    list_field_traces: dict[str, list[dict[str, Any]]] = {
        "qualifications": [],
        "past_projects": [],
        "subcontractors": [],
        "technical_highlights": [],
    }

    for route_name, output in route_outputs.items():
        trace["routes"][route_name] = {
            "target_fields": list((get_extraction_route(route_name) or ExtractionRouteConfig(route_name, route_name, (), (), 0)).target_fields),
            **route_context_meta.get(route_name, {}),
        }
        if not isinstance(output, dict):
            continue

        company_info = output.get("company_info") or {}
        if isinstance(company_info, dict):
            merged_company = merged["company_info"] or {}
            for key, value in company_info.items():
                if key == "_trace":
                    continue
                if value and not merged_company.get(key):
                    merged_company[key] = value
                    company_trace[key] = _build_trace(route_name, value)
            if merged_company:
                merged_company["_trace"] = company_trace
                merged["company_info"] = merged_company

        price_info = output.get("price_info") or {}
        if isinstance(price_info, dict):
            merged_price = merged["price_info"] or {}
            for key, value in price_info.items():
                if key == "_trace":
                    continue
                if value and not merged_price.get(key):
                    merged_price[key] = value
                    price_trace[key] = _build_trace(route_name, value)
            if merged_price:
                merged_price["_trace"] = price_trace
                merged["price_info"] = merged_price

        for person in output.get("personnel_list") or []:
            normalized = _normalize_person_item(person, route_name)
            if not normalized:
                continue
            if not any(_same_person_item(normalized, existing) for existing in merged["personnel_list"]):
                merged["personnel_list"].append(normalized)

        for field_name in ("qualifications", "past_projects", "subcontractors", "technical_highlights"):
            for item in output.get(field_name) or []:
                text = str(item or "").strip()
                if not text or text in merged[field_name]:
                    continue
                merged[field_name].append(text)
                list_field_traces[field_name].append(_build_trace(route_name, text))

    trace["list_field_traces"] = list_field_traces
    return merged


def _build_trace(route_name: str, raw_value: Any) -> dict[str, Any]:
    text = str(raw_value or "").strip()
    return {
        "value": text,
        "raw_value": text,
        "source_page": _extract_page_hint(text),
        "source_quote": text[:120],
        "source_type": "image_evidence" if route_name == "evidence" else "route_context",
        "confidence": _route_confidence(route_name),
        "route": route_name,
    }


def _route_confidence(route_name: str) -> float:
    return {
        "entity": 0.88,
        "pricing": 0.84,
        "qualification": 0.82,
        "technical": 0.72,
        "evidence": 0.78,
    }.get(route_name, 0.7)


def _extract_page_hint(text: str) -> str | None:
    start = text.find("第")
    if start < 0:
        return None
    end = text.find("页", start)
    if end < 0:
        return None
    return text[start:end + 1]


def _normalize_person_item(person: Any, route_name: str) -> dict[str, Any] | None:
    if not isinstance(person, dict):
        return None
    name = str(person.get("name") or "").strip()
    role = str(person.get("role") or "").strip()
    if not name or not role:
        return None
    certs = [str(x).strip() for x in (person.get("certificate_numbers") or []) if str(x).strip()]
    normalized = {
        "name": name,
        "role": role,
        "id_number": person.get("id_number"),
        "certificate_numbers": certs,
        "source_page": person.get("source_page"),
        "source_quote": f"{name}，{role}",
        "source_type": "image_evidence" if route_name == "evidence" else "route_context",
        "confidence": _route_confidence(route_name),
        "extraction_route": route_name,
    }
    return normalized


def _same_person_item(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_name = str(left.get("name") or "").strip()
    right_name = str(right.get("name") or "").strip()
    if not left_name or left_name != right_name:
        return False
    left_certs = set(str(x).strip() for x in (left.get("certificate_numbers") or []) if str(x).strip())
    right_certs = set(str(x).strip() for x in (right.get("certificate_numbers") or []) if str(x).strip())
    if left_certs and right_certs:
        return left_certs == right_certs
    return str(left.get("role") or "").strip() == str(right.get("role") or "").strip()
