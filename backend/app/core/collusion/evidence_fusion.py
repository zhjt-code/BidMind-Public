"""证据融合模块 (S3.6)

将 pair 级证据按维度+语义去重合并为群组级证据。
在 S3.5 图聚类之后、S4 群组研判之前执行，纯算法无 LLM 调用。

核心逻辑：
  1. 对每个群组，收集所有关联 pair 的 findings
  2. 按维度（D1-D8）分组
  3. 维度内按语义键去重合并：
     - D1: 人名 -> 合并所有出现该人员的文档引用
     - D2: 关系描述 -> 合并涉及的文档
     - D3-D8: description 前缀匹配 -> 合并 evidences
  4. 输出群组级 FindingItem（evidences 指向 N 个文档）
"""

from __future__ import annotations

import re
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Optional

from loguru import logger

from app.core.collusion.global_features import GlobalFeatureResult
from app.core.collusion.analyzer_parts.dimension_signals import (
    is_hollow_dimension_signal,
)
from app.schemas.collusion import sanitize_quote


_LEVEL_PRIORITY = {"first": 0, "second": 1, "third": 2, "unknown": 3}


class EvidenceFusionEngine:
    """将 pair 级 findings 融合为群组级 findings"""

    def __init__(
        self,
        doc_names: dict[int, str],
        global_features: Optional[GlobalFeatureResult] = None,
    ) -> None:
        self._doc_names = doc_names or {}
        self._gf = global_features

    def fuse_for_group(
        self,
        member_doc_ids: list[int],
        related_pair_ids: list[str],
        pair_findings_map: dict[str, list[dict]],
    ) -> list[dict]:
        """对一个群组执行证据融合

        Args:
            member_doc_ids: 群组成员 doc id 列表
            related_pair_ids: 群组关联的 pair id 列表
            pair_findings_map: {pair_id: [finding_dict, ...]}

        Returns:
            融合后的 FindingItem dict 列表
        """
        all_findings: list[tuple[dict, int, int]] = []
        signal_findings: list[tuple[dict, int, int]] = []
        for pid in related_pair_ids:
            findings = pair_findings_map.get(pid, [])
            parts = pid.split(":")
            if len(parts) == 2:
                doc_a, doc_b = int(parts[0]), int(parts[1])
            else:
                continue
            for f in findings:
                if not isinstance(f, dict):
                    continue
                # T1: 维度信号项无可复核引文，单独留存，避免被下方
                # _has_verifiable_evidences 门槛丢弃（保证群组模式中栏可见）。
                # 空壳信号无可核对路径，不进入群组融合。
                if f.get("is_dimension_signal"):
                    if is_hollow_dimension_signal(f):
                        continue
                    signal_findings.append((f, doc_a, doc_b))
                else:
                    all_findings.append((f, doc_a, doc_b))

        if not all_findings and not signal_findings:
            return []

        member_set = set(member_doc_ids)

        dim_groups: dict[str, list[tuple[dict, int, int]]] = defaultdict(list)
        for f, da, db in all_findings:
            dim = (f.get("dimension") or "unknown").upper()
            dim_groups[dim].append((f, da, db))

        fused: list[dict] = []

        if "D1" in dim_groups:
            fused += self._fuse_d1_personnel(
                dim_groups.pop("D1"), member_set)
        if "D2" in dim_groups:
            fused += self._fuse_d2_company(
                dim_groups.pop("D2"), member_set)

        for dim, items in sorted(dim_groups.items()):
            fused += self._fuse_generic(dim, items, member_set)

        # T1: 仅对"群组内无正式融合证据"的维度补回维度信号项，
        # 避免与正式证据重复；保证有得分维度在群组级仍可见。
        fused += self._fuse_dimension_signals(
            signal_findings, member_set, covered_dims={
                str(f.get("dimension") or "").upper() for f in fused
            },
        )

        fused.sort(key=lambda f: (
            _LEVEL_PRIORITY.get(f.get("level", "unknown"), 3),
            f.get("dimension", "Z"),
        ))

        logger.info(
            f"[EvidenceFusion] 群组({len(member_doc_ids)}成员): "
            f"{len(all_findings)}条pair证据 -> {len(fused)}条群组证据"
        )
        return fused

    # ------------------------------------------------------------------
    # D1: 人员重叠 - 用全局特征的人名精确匹配
    # ------------------------------------------------------------------

    def _fuse_d1_personnel(
        self,
        items: list[tuple[dict, int, int]],
        member_set: set[int],
    ) -> list[dict]:
        person_bucket: dict[str, list[tuple[dict, int, int]]] = defaultdict(list)

        for f, da, db in items:
            name = self._extract_person_name(f.get("description", ""))
            if name:
                person_bucket[name].append((f, da, db))
            else:
                person_bucket[f"_unknown_{id(f)}"].append((f, da, db))

        if self._gf and self._gf.personnel_overlaps:
            for person_key, doc_ids in self._gf.personnel_overlaps.items():
                parts = person_key.split("|", 1)
                name = parts[0]
                group_docs = [d for d in doc_ids if d in member_set]
                if len(group_docs) >= 2 and name not in person_bucket:
                    person_bucket[name] = []

        results: list[dict] = []
        for name, bucket in person_bucket.items():
            if name.startswith("_unknown_"):
                for f, da, db in bucket:
                    group_finding = self._single_finding_to_group(f, da, db)
                    if self._has_verifiable_evidences(group_finding.get("evidences") or []):
                        results.append(group_finding)
                continue

            involved_ids: set[int] = set()
            evidences: list[dict] = []
            best_level = "unknown"
            best_certainty = 0.0
            best_desc = ""
            best_detail = ""

            for f, da, db in bucket:
                involved_ids.add(da)
                involved_ids.add(db)
                evidences += self._collect_evidences(f, da, db)
                lvl = f.get("level", "unknown")
                if _LEVEL_PRIORITY.get(lvl, 3) < _LEVEL_PRIORITY.get(best_level, 3):
                    best_level = lvl
                cert = f.get("certainty") or 0.0
                if cert > best_certainty:
                    best_certainty = cert
                desc = f.get("description", "")
                if len(desc) > len(best_desc):
                    best_desc = desc
                det = f.get("detail") or ""
                if len(det) > len(best_detail):
                    best_detail = det

            if self._gf and self._gf.personnel_overlaps:
                for person_key, doc_ids in self._gf.personnel_overlaps.items():
                    pn = person_key.split("|", 1)[0]
                    if pn == name:
                        for d in doc_ids:
                            if d in member_set:
                                involved_ids.add(d)

            involved_sorted = sorted(involved_ids & member_set)
            involved_names = [
                self._doc_names.get(d, f"文件{d}") for d in involved_sorted]

            if len(involved_sorted) > 2:
                names_str = "、".join(involved_names)
                best_desc = (
                    f"人员 {name} 同时出现在 {len(involved_sorted)} 份文件中"
                    f"（{names_str}）"
                )

            evidences = self._dedup_evidences(evidences)
            if not self._has_verifiable_evidences(evidences):
                continue

            results.append({
                "dimension": "D1",
                "level": best_level,
                "description": best_desc,
                "detail": best_detail or None,
                "evidences": evidences,
                "certainty": best_certainty if best_certainty > 0 else None,
                "involved_doc_ids": involved_sorted,
                "involved_doc_names": involved_names,
            })

        return results

    # ------------------------------------------------------------------
    # D2: 企业关联 - 用关系描述去重
    # ------------------------------------------------------------------

    def _fuse_d2_company(
        self,
        items: list[tuple[dict, int, int]],
        member_set: set[int],
    ) -> list[dict]:
        relation_bucket: dict[str, list[tuple[dict, int, int]]] = defaultdict(list)

        for f, da, db in items:
            key = self._extract_relation_key(f.get("description", ""))
            relation_bucket[key].append((f, da, db))

        results: list[dict] = []
        for key, bucket in relation_bucket.items():
            involved_ids: set[int] = set()
            evidences: list[dict] = []
            best_level = "unknown"
            best_certainty = 0.0
            best_desc = ""
            best_detail = ""

            for f, da, db in bucket:
                involved_ids.add(da)
                involved_ids.add(db)
                evidences += self._collect_evidences(f, da, db)
                lvl = f.get("level", "unknown")
                if _LEVEL_PRIORITY.get(lvl, 3) < _LEVEL_PRIORITY.get(best_level, 3):
                    best_level = lvl
                cert = f.get("certainty") or 0.0
                if cert > best_certainty:
                    best_certainty = cert
                desc = f.get("description", "")
                if len(desc) > len(best_desc):
                    best_desc = desc
                det = f.get("detail") or ""
                if len(det) > len(best_detail):
                    best_detail = det

            involved_sorted = sorted(involved_ids & member_set)
            involved_names = [
                self._doc_names.get(d, f"文件{d}") for d in involved_sorted]
            evidences = self._dedup_evidences(evidences)
            if not self._has_verifiable_evidences(evidences):
                continue

            results.append({
                "dimension": "D2",
                "level": best_level,
                "description": best_desc,
                "detail": best_detail or None,
                "evidences": evidences,
                "certainty": best_certainty if best_certainty > 0 else None,
                "involved_doc_ids": involved_sorted,
                "involved_doc_names": involved_names,
            })

        return results

    # ------------------------------------------------------------------
    # D3-D8: 通用维度 - description 前缀相似度匹配
    # ------------------------------------------------------------------

    def _fuse_generic(
        self,
        dim: str,
        items: list[tuple[dict, int, int]],
        member_set: set[int],
    ) -> list[dict]:
        buckets: list[list[tuple[dict, int, int]]] = []

        for f, da, db in items:
            desc = f.get("description", "")
            placed = False
            for bucket in buckets:
                rep_desc = bucket[0][0].get("description", "")
                if self._desc_similar(desc, rep_desc):
                    bucket.append((f, da, db))
                    placed = True
                    break
            if not placed:
                buckets.append([(f, da, db)])

        results: list[dict] = []
        for bucket in buckets:
            involved_ids: set[int] = set()
            evidences: list[dict] = []
            best_level = "unknown"
            best_certainty = 0.0
            best_similarity = 0.0
            best_desc = ""
            best_detail = ""

            for f, da, db in bucket:
                involved_ids.add(da)
                involved_ids.add(db)
                evidences += self._collect_evidences(f, da, db)
                lvl = f.get("level", "unknown")
                if _LEVEL_PRIORITY.get(lvl, 3) < _LEVEL_PRIORITY.get(best_level, 3):
                    best_level = lvl
                cert = f.get("certainty") or 0.0
                if cert > best_certainty:
                    best_certainty = cert
                sim = f.get("similarity") or 0.0
                if sim > best_similarity:
                    best_similarity = sim
                desc = f.get("description", "")
                if len(desc) > len(best_desc):
                    best_desc = desc
                det = f.get("detail") or ""
                if len(det) > len(best_detail):
                    best_detail = det

            involved_sorted = sorted(involved_ids & member_set)
            involved_names = [
                self._doc_names.get(d, f"文件{d}") for d in involved_sorted]
            evidences = self._dedup_evidences(evidences)
            if not self._has_verifiable_evidences(evidences):
                continue

            results.append({
                "dimension": dim,
                "level": best_level,
                "description": best_desc,
                "detail": best_detail or None,
                "evidences": evidences,
                "certainty": best_certainty if best_certainty > 0 else None,
                "similarity": best_similarity if best_similarity > 0 else None,
                "involved_doc_ids": involved_sorted,
                "involved_doc_names": involved_names,
            })

        return results

    # ------------------------------------------------------------------
    # 维度信号项：有得分但无正式证据的维度，群组级保留为参考条目
    # ------------------------------------------------------------------

    def _fuse_dimension_signals(
        self,
        items: list[tuple[dict, int, int]],
        member_set: set[int],
        covered_dims: set[str],
    ) -> list[dict]:
        """将 pair 级维度信号项按维度合并为群组级信号（每维度至多 1 条）。

        仅对 covered_dims（已有正式融合证据的维度）之外的维度产出，避免重复。
        信号项无可复核引文，不经过 _has_verifiable_evidences 门槛。
        """
        if not items:
            return []

        dim_bucket: dict[str, list[tuple[dict, int, int]]] = defaultdict(list)
        for f, da, db in items:
            dim = (f.get("dimension") or "unknown").upper()
            if dim in covered_dims:
                continue
            dim_bucket[dim].append((f, da, db))

        results: list[dict] = []
        for dim, bucket in sorted(dim_bucket.items()):
            involved_ids: set[int] = set()
            best_certainty = 0.0
            best_desc = ""
            best_detail = ""
            signal_evidences: list[dict] = []
            for f, da, db in bucket:
                involved_ids.add(da)
                involved_ids.add(db)
                cert = f.get("certainty") or 0.0
                if cert > best_certainty:
                    best_certainty = cert
                desc = f.get("description", "")
                if len(desc) > len(best_desc):
                    best_desc = desc
                det = f.get("detail") or ""
                if len(det) > len(best_detail):
                    best_detail = det
                # 维度信号项的页码/单边定位 best-effort 带出（不经引文门槛）
                for ev in (f.get("evidences") or []):
                    if isinstance(ev, dict):
                        signal_evidences.append(ev)

            involved_sorted = sorted(involved_ids & member_set)
            involved_names = [
                self._doc_names.get(d, f"文件{d}") for d in involved_sorted]
            results.append({
                "dimension": dim,
                "level": "unknown",
                "description": best_desc,
                "detail": best_detail or None,
                "evidences": self._dedup_evidences(signal_evidences),
                "certainty": best_certainty if best_certainty > 0 else None,
                "involved_doc_ids": involved_sorted,
                "involved_doc_names": involved_names,
                "is_dimension_signal": True,
            })
        return results

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_person_name(desc: str) -> str:
        """从 D1 描述中提取人名"""
        m = re.search(r"人员\s*[：:「]?\s*([^\s（(，、]+)", desc)
        if m:
            return m.group(1).strip("」")
        m = re.search(r"([^\s（(，、]{2,4})\s*(?:同时出现|出现在|重叠|共享)", desc)
        if m:
            return m.group(1)
        return ""

    @staticmethod
    def _extract_relation_key(desc: str) -> str:
        """从 D2 描述中提取关系键（简化为前30字符）"""
        normalized = re.sub(r"\s+", "", desc[:60])
        return normalized[:30] if normalized else desc[:30]

    @staticmethod
    def _desc_similar(a: str, b: str, threshold: float = 0.6) -> bool:
        """判断两条描述是否语义相似"""
        if not a or not b:
            return False
        prefix_a = a[:40]
        prefix_b = b[:40]
        ratio = SequenceMatcher(None, prefix_a, prefix_b).ratio()
        return ratio >= threshold

    def _collect_evidences(self, f: dict, doc_a: int, doc_b: int) -> list[dict]:
        """从一条 finding dict 中收集所有 evidence ref"""
        result: list[dict] = []

        existing = f.get("evidences") or []
        for ev in existing:
            if isinstance(ev, dict) and sanitize_quote(ev.get("quote")):
                ev_copy = dict(ev)
                ev_copy["quote"] = sanitize_quote(ev_copy.get("quote"))
                if not ev_copy.get("doc_name"):
                    did = ev_copy.get("doc_id")
                    if did is not None:
                        ev_copy["doc_name"] = self._doc_names.get(
                            int(did), f"文件{did}")
                result.append(ev_copy)

        if not result:
            qa = sanitize_quote(f.get("quote_a"))
            pa = f.get("page_a")
            if qa:
                result.append({
                    "doc_id": doc_a,
                    "doc_name": self._doc_names.get(doc_a, f"文件{doc_a}"),
                    "quote": qa,
                    "location": pa,
                })
            qb = sanitize_quote(f.get("quote_b"))
            pb = f.get("page_b")
            if qb:
                result.append({
                    "doc_id": doc_b,
                    "doc_name": self._doc_names.get(doc_b, f"文件{doc_b}"),
                    "quote": qb,
                    "location": pb,
                })

        return result

    @staticmethod
    def _dedup_evidences(evidences: list[dict]) -> list[dict]:
        """按 (doc_id, quote前20字) 去重"""
        seen: set[str] = set()
        result: list[dict] = []
        for ev in evidences:
            did = ev.get("doc_id") or 0
            quote = (ev.get("quote") or "")[:20]
            key = f"{did}|{quote}"
            if key not in seen:
                seen.add(key)
                result.append(ev)
        return result

    @staticmethod
    def _has_verifiable_evidences(evidences: list[dict]) -> bool:
        """正式群组证据必须至少携带一条原文摘录。"""
        return any(
            isinstance(ev, dict)
            and ev.get("doc_id") is not None
            and sanitize_quote(ev.get("quote"))
            for ev in evidences
        )

    def _single_finding_to_group(
        self, f: dict, doc_a: int, doc_b: int,
    ) -> dict:
        """将单条 pair finding 转为群组级 finding（保留原始2文档引用）"""
        involved = sorted({doc_a, doc_b})
        involved_names = [
            self._doc_names.get(d, f"文件{d}") for d in involved]
        evidences = self._collect_evidences(f, doc_a, doc_b)

        return {
            "dimension": f.get("dimension", "unknown"),
            "level": f.get("level", "unknown"),
            "description": f.get("description", ""),
            "detail": f.get("detail"),
            "evidences": evidences,
            "certainty": f.get("certainty"),
            "similarity": f.get("similarity"),
            "involved_doc_ids": involved,
            "involved_doc_names": involved_names,
        }
