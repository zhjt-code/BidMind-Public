"""素材元数据打标器

素材入库时调用 LLM 进行元数据标注，输出领域分类、关键实体、
适用章节和关键词，存入 WritingMaterial.metadata_tags (JSON)。

_tag_status（metadata_tags JSON 内部字段）:
- 未设置/"llm_success"：LLM 打标成功
- "llm_failed"：LLM 调用失败，当前标签来自 _fallback_tags（关键词降级）
- "image_skipped"：图片素材未经验证，跳过 LLM 章节定位
matcher 消费时对 llm_failed 素材走放宽阈值路径（见 P0.3 设计）。
"""

import asyncio
import json
import re
from typing import Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai import LLMClient, get_review_client
from app.core.ai.json_response import require_dict_response
from app.core.ai.prompt_registry import call_with_fallback, resolve_runtime_params
from app.core.ai.retry_tracker import LLMRetryTracker
from app.core.writer.material_titles import get_effective_material_title
from app.core.writer.prompts.material_tag import get_material_tag_prompt as _code_material_tag
from app.models.writing_material import WritingMaterial

_TAG_STATUS_LLM_SUCCESS = "llm_success"
_TAG_STATUS_LLM_FAILED = "llm_failed"
_TAG_STATUS_IMAGE_SKIPPED = "image_skipped"
_MAX_TAG_CONCURRENCY = 4


# NOTE: call_with_fallback 内部 inspect.signature(code_fn).bind(*a, **kw)
# 把位置/关键字参数统一映射为 kwargs 再 format, 避免 Registry 命名占位符
# 因调用方传位置参数而静默 fallback。
def get_material_tag_prompt(*a, **kw):
    return call_with_fallback("writing.material_tag", _code_material_tag, *a, **kw)


class MaterialTagger:
    """素材元数据打标器"""

    def __init__(
        self,
        db: AsyncSession,
        llm_client: Optional[LLMClient] = None,
        project_id: Optional[int] = None,
    ) -> None:
        self.db = db
        self.llm_client = llm_client or get_review_client(project_id=project_id)
        self._retry_tracker = LLMRetryTracker("material_tagger")

    async def tag(
        self,
        material: WritingMaterial,
        outline_tree: str = "",
        *,
        _is_retry: bool = False,
    ) -> Optional[dict]:
        """对单个素材进行 LLM 元数据打标

        Args:
            material: 待打标的素材 ORM 对象
            outline_tree: 标书目录树文本（缩进格式），用于精确章节定位
            _is_retry: 内部标记，重试调用时为 True

        Returns:
            打标结果字典; 重试仍失败时返回 None（保留先前 fallback 结果）
        """
        mt = material.material_type
        material_type = mt.value if hasattr(mt, 'value') else (mt or "text")
        label_conflict = getattr(material, "label_conflict", None)
        resolved_label = ""
        if label_conflict == "matched":
            resolved_label = getattr(material, "resolved_label", None) or ""
        preview_label = (
            resolved_label
            or getattr(material, "structure_hint_label", None)
            or getattr(material, "file_name_label", None)
            or ""
        )
        content_preview = self._build_content_preview(material, resolved_label=preview_label)
        if material_type == "image" and label_conflict != "matched":
            result = self._fallback_tags(material)
            result["_tag_status"] = _TAG_STATUS_IMAGE_SKIPPED
            result["confidence"] = min(float(result.get("confidence", 0.2)), 0.2)
            result["evidence_source"] = _TAG_STATUS_IMAGE_SKIPPED
            result["tag_basis"] = "图片素材未完成标签一致性验证，跳过LLM章节定位"
            material.metadata_tags = json.dumps(result, ensure_ascii=False)
            await self.db.commit()
            logger.info(
                "素材打标降级: id={}, material_type=image, label_conflict={!r}, 跳过LLM章节定位",
                material.id,
                label_conflict,
            )
            return result
        system_msg, user_msg = get_material_tag_prompt(
            title=get_effective_material_title(material),
            material_type=material_type,
            content_preview=content_preview,
            outline_tree=outline_tree,
            resolved_label=resolved_label,
        )

        tag_status = _TAG_STATUS_LLM_SUCCESS
        temperature, max_tokens = resolve_runtime_params(
            "writing.material_tag", 0.1, 1000,
        )
        try:
            result = await self.llm_client.chat_json(
                user_message=user_msg,
                system_message=system_msg,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            result = require_dict_response(
                result, operation_name=f"素材打标(material_id={material.id})"
            )
        except Exception as e:
            if _is_retry:
                logger.warning(f"素材打标重试仍失败: material_id={material.id}, err={e}")
                return None
            logger.warning(f"素材打标 LLM 调用失败: material_id={material.id}, err={e}")
            result = self._fallback_tags(material)
            tag_status = _TAG_STATUS_LLM_FAILED
            m, ot = material, outline_tree
            self._retry_tracker.record(
                key=str(material.id),
                retry_coro_factory=lambda _m=m, _ot=ot: self.tag(_m, _ot, _is_retry=True),
                fallback_applied="fallback_tags",
            )
            try:
                from app.core.observability.degradation_aggregator import (
                    record_degradation as _record_degradation,
                )
                _record_degradation(
                    kind="material_tag_failed",
                    ref=str(material.id),
                    reason=f"{type(e).__name__}: {str(e)[:120]}",
                )
            except Exception:
                pass

        result = self._normalize_tags(result)
        result = self._apply_resolved_label_bias(
            result,
            resolved_label=resolved_label,
            material_type=material_type,
        )
        if tag_status == _TAG_STATUS_LLM_SUCCESS:
            result.setdefault("evidence_source", "llm")
            result.setdefault("tag_basis", "LLM依据素材标题、内容摘要和目录结构打标")
            result.setdefault("confidence", 0.8)
        result["_tag_status"] = tag_status

        material.metadata_tags = json.dumps(result, ensure_ascii=False)
        await self.db.commit()

        logger.info(
            f"素材打标完成: id={material.id}, domain={result.get('domain')}, "
            f"entities={len(result.get('entities', []))}个, "
            f"keywords={len(result.get('keywords', []))}个, "
            f"status={tag_status}"
        )
        return result

    async def retry_failed(self) -> dict:
        """重试先前 LLM 失败的素材打标

        首轮打标中 LLM 调用失败的素材会暂存 fallback 结果。
        本方法在首轮完成后调用，利用 dispatcher 可能已轮转到的
        健康模型组重新尝试 LLM 打标，成功则覆盖 fallback 结果。

        Returns:
            {"retried": int, "recovered": int}
        """
        stats = await self._retry_tracker.retry_all()
        return {"retried": stats.retried, "recovered": stats.recovered}

    async def tag_batch(
        self,
        materials: list[WritingMaterial],
        outline_tree: str = "",
        *,
        auto_retry_failed: bool = True,
    ) -> list[Optional[dict]]:
        """批量打标（受控并发 + 末尾自动重试失败项）

        与旧顺序版本相比:
        - 使用 Semaphore 控制并发, 避免 Dispatcher 过载;
        - 默认在所有素材首轮打标结束后自动调用一次 retry_failed(),
          利用 dispatcher 可能已轮转到的健康通道挽回首轮 LLM 失败项;
        - 首轮 LLM 失败但 fallback 成功的素材 metadata_tags
          带 _tag_status=llm_failed, matcher 据此走放宽阈值路径。
        """
        semaphore = asyncio.Semaphore(_MAX_TAG_CONCURRENCY)

        async def _tag_one(mat: WritingMaterial) -> Optional[dict]:
            async with semaphore:
                try:
                    return await self.tag(mat, outline_tree=outline_tree)
                except Exception as exc:  # noqa: BLE001 — 保护批次其他素材
                    logger.error(
                        f"素材打标出现未处理异常: material_id={mat.id}, err={exc}",
                        exc_info=True,
                    )
                    return None

        results = await asyncio.gather(*(_tag_one(m) for m in materials))

        if auto_retry_failed:
            try:
                retry_stats = await self.retry_failed()
                if retry_stats.get("retried", 0) > 0:
                    logger.info(
                        "素材打标末尾自动重试: retried={}, recovered={}",
                        retry_stats.get("retried"),
                        retry_stats.get("recovered"),
                    )
            except Exception as exc:
                logger.warning(f"素材打标末尾自动重试失败(降级): {exc}")

        return results

    def _normalize_tags(self, raw: dict) -> dict:
        """规范化打标结果"""
        return {
            "domain": raw.get("domain", "其他") or "其他",
            "bid_purpose": raw.get("bid_purpose", "") or "",
            "exclusive_section": raw.get("exclusive_section") or None,
            "applicable_sections": self._ensure_list(raw.get("applicable_sections")),
            "inapplicable_sections": self._ensure_list(raw.get("inapplicable_sections")),
            "entities": self._ensure_list(raw.get("entities")),
            "keywords": self._ensure_list(raw.get("keywords")),
            "confidence": self._coerce_confidence(raw.get("confidence"), default=0.8),
            "evidence_source": str(raw.get("evidence_source") or "llm"),
            "tag_basis": str(raw.get("tag_basis") or ""),
        }

    def _apply_resolved_label_bias(
        self,
        result: dict,
        *,
        resolved_label: str,
        material_type: str,
    ) -> dict:
        """对已知业务标签施加轻量规则偏置，纠正明显漂移。"""
        if material_type != "text":
            return result

        company_profile_labels = {"公司简介", "企业简介", "企业概况", "企业介绍", "单位简介"}
        performance_labels = {"项目业绩综述", "项目经验综述", "类似项目综述", "项目业绩", "业绩材料", "项目合同", "项目经验"}
        service_labels = {"服务承诺", "售后服务承诺", "响应承诺", "保障措施"}
        management_labels = {"管理制度", "制度汇编", "管理办法", "质量管理制度", "安全管理制度"}
        if resolved_label not in company_profile_labels | performance_labels | service_labels | management_labels:
            return result

        normalized = dict(result)
        basis_parts = []
        if resolved_label in company_profile_labels:
            if normalized.get("domain") in {"企业资质", "其他", "", None}:
                normalized["domain"] = "企业概况"
            normalized["exclusive_section"] = None
            if not normalized.get("bid_purpose"):
                normalized["bid_purpose"] = "企业基本情况与综合实力介绍材料"
            basis_parts.append("resolved_label纠正为企业概况类素材")
        elif resolved_label in performance_labels:
            if normalized.get("domain") in {"其他", "", None}:
                normalized["domain"] = "项目业绩"
            normalized["exclusive_section"] = None
            if not normalized.get("bid_purpose"):
                normalized["bid_purpose"] = "类似项目经验与履约成果综述材料"
            basis_parts.append("resolved_label纠正为项目业绩类素材")
        elif resolved_label in service_labels:
            if normalized.get("domain") in {"其他", "", None}:
                normalized["domain"] = "服务承诺"
            normalized["exclusive_section"] = None
            if not normalized.get("bid_purpose"):
                normalized["bid_purpose"] = "服务承诺、响应机制与保障措施说明材料"
            basis_parts.append("resolved_label纠正为服务承诺类素材")
        elif resolved_label in management_labels:
            if normalized.get("domain") in {"其他", "", None}:
                normalized["domain"] = "管理制度"
            normalized["exclusive_section"] = None
            if not normalized.get("bid_purpose"):
                normalized["bid_purpose"] = "内部管理制度、流程与执行机制说明材料"
            basis_parts.append("resolved_label纠正为管理制度类素材")
        keywords = self._ensure_list(normalized.get("keywords"))
        if resolved_label not in keywords:
            keywords.insert(0, resolved_label)
        normalized["keywords"] = keywords[:10]
        if basis_parts:
            normalized["evidence_source"] = "resolved_label_bias"
            existing_basis = normalized.get("tag_basis") or ""
            normalized["tag_basis"] = "；".join(
                part for part in [existing_basis, *basis_parts] if part
            )
            normalized["confidence"] = max(
                float(normalized.get("confidence") or 0.0),
                0.85,
            )
        return normalized

    def _build_content_preview(
        self,
        material: WritingMaterial,
        *,
        resolved_label: str,
    ) -> str:
        content = material.content or ""
        if not content:
            return ""
        preview_limit = 2000
        long_text_labels = {
            "公司简介", "企业简介", "企业概况", "企业介绍", "单位简介",
            "项目业绩综述", "项目经验综述", "类似项目综述", "项目业绩", "业绩材料", "项目合同", "项目经验",
            "服务承诺", "售后服务承诺", "响应承诺", "保障措施",
            "管理制度", "制度汇编", "管理办法", "质量管理制度", "安全管理制度",
        }
        if resolved_label not in long_text_labels:
            return content[:preview_limit]
        compact = re.sub(r"\s+", " ", content)
        if len(compact) <= 4000:
            return compact
        head = compact[:2200]
        tail = compact[-1600:]
        return head + "\n...\n" + tail

    def _fallback_tags(self, material: WritingMaterial) -> dict:
        """LLM 失败时的降级打标（基于标题关键词）"""
        title = get_effective_material_title(material)
        domain = "其他"
        keywords: list[str] = []

        domain_rules = {
            "人员配置": ["人员", "简历", "项目经理", "团队"],
            "项目业绩": ["业绩", "合同", "项目经验", "案例"],
            "服务承诺": ["服务承诺", "售后服务", "响应承诺", "保障措施"],
            "管理制度": ["管理制度", "制度汇编", "管理办法", "质量管理", "安全管理"],
            "企业资质": ["资质", "证书", "执照", "许可"],
            "技术方案": ["技术", "方案", "实施", "架构"],
            "设备清单": ["设备", "清单", "配置", "硬件"],
            "财务资料": ["财务", "审计", "报表", "资产"],
        }
        for d, kws in domain_rules.items():
            for kw in kws:
                if kw in title:
                    domain = d
                    keywords.append(kw)
                    break

        return {
            "domain": domain,
            "bid_purpose": "",
            "exclusive_section": None,
            "entities": [],
            "applicable_sections": [],
            "inapplicable_sections": [],
            "keywords": keywords,
            "confidence": 0.35 if keywords else 0.2,
            "evidence_source": "fallback_keyword",
            "tag_basis": f"基于素材标题关键词降级打标: {', '.join(keywords) if keywords else '未命中明确关键词'}",
        }

    @staticmethod
    def _ensure_list(v: object) -> list:
        """确保值为列表"""
        if isinstance(v, list):
            return v
        return []

    @staticmethod
    def _coerce_confidence(value: object, *, default: float) -> float:
        """归一化置信度到 0.0-1.0。"""
        try:
            score = float(value)
        except (TypeError, ValueError):
            score = default
        return max(0.0, min(1.0, score))


def get_material_tagger(
    db: AsyncSession,
    llm_client: Optional[LLMClient] = None,
    project_id: Optional[int] = None,
) -> MaterialTagger:
    """工厂函数"""
    return MaterialTagger(db=db, llm_client=llm_client, project_id=project_id)
