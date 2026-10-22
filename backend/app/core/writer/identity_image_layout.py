"""身份证图片布局计划器。

职责: 从已绑定身份证图片记录中派生法人/代理人与正反面布局顺序。
不做: 不查询数据库、不读取图片文件、不生成 HTML/Word 内容。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.models.writing_material_image import ImageType, WritingMaterialImage

ROLE_LEGAL = "legal_representative"
ROLE_AGENT = "agent"
ROLE_UNKNOWN = "unknown"

SIDE_FRONT = "front"
SIDE_BACK = "back"
SIDE_UNKNOWN = "unknown"

_ROLE_ORDER = {
    ROLE_LEGAL: 0,
    ROLE_AGENT: 1,
    ROLE_UNKNOWN: 2,
}

_FRONT_KEYWORDS = (
    "正面",
    "人像面",
    "姓名",
    "性别",
    "民族",
    "住址",
    "出生",
    "公民身份号码",
)
_BACK_KEYWORDS = (
    "反面",
    "背面",
    "国徽",
    "签发机关",
    "有效期限",
    "有效期",
)
_LEGAL_KEYWORDS = (
    "法定代表人",
    "法人代表",
    "法人身份证",
    "法人",
)
_AGENT_KEYWORDS = (
    "委托代理人",
    "授权代表",
    "代理人",
    "被授权人",
)
_IDENTITY_KEYWORDS = (
    "身份证",
    "居民身份证",
    "公民身份号码",
)


@dataclass(frozen=True)
class IdentityImageSlot:
    """单张身份证图片在布局中的槽位。"""

    image: WritingMaterialImage
    image_id: int
    caption: str
    role: str
    side: str
    review_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class IdentityImageRow:
    """同一人员角色的身份证正反面布局行。"""

    role: str
    front: IdentityImageSlot | None = None
    back: IdentityImageSlot | None = None
    unknown_slots: tuple[IdentityImageSlot, ...] = ()

    @property
    def slots(self) -> tuple[IdentityImageSlot, ...]:
        """按展示顺序返回本行全部图片槽位。"""
        ordered: list[IdentityImageSlot] = []
        if self.front is not None:
            ordered.append(self.front)
        if self.back is not None:
            ordered.append(self.back)
        ordered.extend(self.unknown_slots)
        return tuple(ordered)


@dataclass(frozen=True)
class IdentityImageLayout:
    """身份证图片布局计划。"""

    rows: tuple[IdentityImageRow, ...] = ()

    @property
    def ordered_slots(self) -> tuple[IdentityImageSlot, ...]:
        """按最终展示顺序返回全部槽位。"""
        return tuple(slot for row in self.rows for slot in row.slots)

    @property
    def ordered_images(self) -> tuple[WritingMaterialImage, ...]:
        """按最终展示顺序返回全部图片记录。"""
        return tuple(slot.image for slot in self.ordered_slots)

    @property
    def ordered_image_ids(self) -> tuple[int, ...]:
        """按最终展示顺序返回全部图片 ID。"""
        return tuple(slot.image_id for slot in self.ordered_slots)

    @property
    def image_id_set(self) -> frozenset[int]:
        """返回布局已消费的图片 ID 集合。"""
        return frozenset(self.ordered_image_ids)


def build_identity_image_layout(
    images: list[WritingMaterialImage],
) -> IdentityImageLayout:
    """为身份证图片构建法人/代理人、正/反面布局计划。"""
    identity_slots = [
        _build_slot(image)
        for image in sorted(images or [], key=_stable_image_key)
        if _is_identity_image(image)
    ]
    if not identity_slots:
        return IdentityImageLayout()

    slots_by_role: dict[str, list[IdentityImageSlot]] = {}
    for slot in identity_slots:
        slots_by_role.setdefault(slot.role, []).append(slot)

    rows: list[IdentityImageRow] = []
    for role in sorted(slots_by_role, key=lambda item: _ROLE_ORDER.get(item, 99)):
        rows.append(_build_row(role, slots_by_role[role]))
    return IdentityImageLayout(rows=tuple(rows))


def order_identity_images(
    images: list[WritingMaterialImage],
) -> list[WritingMaterialImage]:
    """返回身份证图片按专用布局排序后的列表。"""
    return list(build_identity_image_layout(images).ordered_images)


def _build_slot(image: WritingMaterialImage) -> IdentityImageSlot:
    evidence = _evidence_text(image)
    role = _infer_role(evidence)
    side = _infer_side(evidence)
    return IdentityImageSlot(
        image=image,
        image_id=int(getattr(image, "id", 0) or 0),
        caption=str(getattr(image, "caption", "") or ""),
        role=role,
        side=side,
    )


def _build_row(role: str, slots: list[IdentityImageSlot]) -> IdentityImageRow:
    front: IdentityImageSlot | None = None
    back: IdentityImageSlot | None = None
    unknown: list[IdentityImageSlot] = []

    for slot in slots:
        if slot.side == SIDE_FRONT and front is None:
            front = slot
        elif slot.side == SIDE_BACK and back is None:
            back = slot
        else:
            unknown.append(slot)

    if front is None and unknown:
        front = _with_review_reason(unknown.pop(0), "side_inferred_from_order", SIDE_FRONT)
    if back is None and unknown:
        back = _with_review_reason(unknown.pop(0), "side_inferred_from_order", SIDE_BACK)

    return IdentityImageRow(
        role=role,
        front=front,
        back=back,
        unknown_slots=tuple(unknown),
    )


def _with_review_reason(
    slot: IdentityImageSlot,
    reason: str,
    side: str,
) -> IdentityImageSlot:
    reasons = tuple(dict.fromkeys((*slot.review_reasons, reason)))
    return IdentityImageSlot(
        image=slot.image,
        image_id=slot.image_id,
        caption=slot.caption,
        role=slot.role,
        side=side,
        review_reasons=reasons,
    )


def _stable_image_key(image: WritingMaterialImage) -> tuple[int, int, int]:
    return (
        int(getattr(image, "material_id", 0) or 0),
        int(getattr(image, "image_index", 0) or 0),
        int(getattr(image, "id", 0) or 0),
    )


def _is_identity_image(image: WritingMaterialImage) -> bool:
    subtype = str(getattr(image, "business_subtype", "") or "").strip().lower()
    if subtype == "id_card":
        return True
    if getattr(image, "image_type", None) != ImageType.personnel_cert:
        return False
    return any(keyword in _evidence_text(image) for keyword in _IDENTITY_KEYWORDS)


def _infer_role(text: str) -> str:
    if any(keyword in text for keyword in _AGENT_KEYWORDS):
        return ROLE_AGENT
    if any(keyword in text for keyword in _LEGAL_KEYWORDS):
        return ROLE_LEGAL
    return ROLE_UNKNOWN


def _infer_side(text: str) -> str:
    explicit_front = "正面" in text or "人像面" in text
    explicit_back = "反面" in text or "背面" in text or "国徽" in text
    if explicit_front and not explicit_back:
        return SIDE_FRONT
    if explicit_back and not explicit_front:
        return SIDE_BACK

    front_score = sum(1 for keyword in _FRONT_KEYWORDS if keyword in text)
    back_score = sum(1 for keyword in _BACK_KEYWORDS if keyword in text)
    if front_score > back_score:
        return SIDE_FRONT
    if back_score > front_score:
        return SIDE_BACK
    return SIDE_UNKNOWN


def _evidence_text(image: WritingMaterialImage) -> str:
    parts = [
        str(getattr(image, "caption", "") or ""),
        str(getattr(image, "vl_content", "") or ""),
    ]
    fields = _parse_structured_fields(getattr(image, "structured_fields", None))
    for key, value in fields.items():
        parts.append(str(key))
        parts.append(str(value))
    return "\n".join(part for part in parts if part)


def _parse_structured_fields(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}
