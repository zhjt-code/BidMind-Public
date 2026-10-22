"""全局特征分析模块

纯算法模块，不涉及 LLM 调用或 DB 写入。
从 S1b 信息提取结果中构建跨文档特征矩阵：
  - D1 人员-公司关联矩阵（跨标人员重叠）
  - D2 企业关系图（法人/电话/地址/银行账户重叠）
  - D4 报价分布分析（等差/围标/聚类模式）
  - D8 PDF元数据聚类（creator/author 分组）
  - 智能筛对建议（综合以上结果推荐必须做LLM的对）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import combinations
from typing import Optional

from loguru import logger

from app.core.collusion.normalizers import (
    address_building_kind,
    credit_code_kind,
    nearby_phone_kind,
    normalize_bank_account,
    normalize_cert_code,
    normalize_company_name,
    normalize_credit_code,
    normalize_id_tail,
)
from app.core.config import settings
from app.schemas.collusion import is_valid_person_name


@dataclass
class GlobalFeatureResult:
    """全局特征分析汇总结果"""
    personnel_overlaps: dict[str, list[int]] = field(default_factory=dict)
    company_edges: list[tuple[int, int, list[str]]] = field(default_factory=list)
    pricing_patterns: list[dict] = field(default_factory=list)
    metadata_clusters: list[set[int]] = field(default_factory=list)
    global_findings: list[dict] = field(default_factory=list)
    # 跨字段串联引擎产物（cross_field_correlator 命中）
    cross_field_hits: list = field(default_factory=list)


class GlobalFeatureAnalyzer:
    """全局特征分析器

    输入: extractions -- {doc_id: extraction_dict} (S1b 输出)
    输出: GlobalFeatureResult
    """

    def __init__(
        self,
        extractions: dict[int, dict],
        doc_names: Optional[dict[int, str]] = None,
    ) -> None:
        self._extractions = extractions
        self._doc_names = doc_names or {did: f"文件{did}" for did in extractions}

    def analyze(self) -> GlobalFeatureResult:
        """执行全部全局特征分析，返回汇总结果"""
        result = GlobalFeatureResult()

        result.personnel_overlaps = self._build_personnel_matrix()
        result.company_edges = self._build_company_relations()
        result.pricing_patterns = self._analyze_pricing()
        result.metadata_clusters = self._cluster_metadata()
        result.cross_field_hits = self._run_cross_field_correlation()
        result.global_findings = self._build_findings(result)

        logger.info(
            f"[GlobalFeatures] 分析完成: "
            f"人员重叠={len(result.personnel_overlaps)}组, "
            f"企业关联={len(result.company_edges)}对, "
            f"报价异常={len(result.pricing_patterns)}项, "
            f"元数据聚类={len(result.metadata_clusters)}组, "
            f"跨字段串联={len(result.cross_field_hits)}条"
        )
        return result

    def _run_cross_field_correlation(self) -> list:
        """跨字段串联引擎：识别 D8↔D2/D1、D2↔D1、subcontractor↔company 等
        跨主体跨维度的标识符串联，落地《招标投标法实施条例》第40条第6项。
        """
        try:
            from app.core.collusion.cross_field_correlator import CrossFieldCorrelator
            from app.core.collusion.identifier_index import build_index
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[CrossField] 模块加载失败，跳过跨字段串联: {exc}")
            return []

        try:
            indices = {
                did: build_index(did, self._doc_names.get(did, f"文件{did}"), ext)
                for did, ext in self._extractions.items()
            }
            correlator = CrossFieldCorrelator(indices, doc_names=self._doc_names)
            hits = correlator.correlate()
            return hits
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[CrossField] 跨字段串联执行失败，已跳过: {exc}")
            return []

    def _build_personnel_matrix(self) -> dict[str, list[int]]:
        """D1: 构建人员-公司矩阵，发现跨标重叠人员"""
        person_map: dict[str, list[int]] = {}

        for doc_id, ext in self._extractions.items():
            personnel = ext.get("personnel_list") or []
            for person in personnel:
                if not isinstance(person, dict):
                    continue
                name = (person.get("name") or "").strip()
                if not name or len(name) < 2 or not is_valid_person_name(name):
                    continue

                certs = person.get("certificate_numbers") or []
                if certs and isinstance(certs, list):
                    for cert in certs:
                        cert_str = normalize_cert_code(cert)
                        if cert_str and len(cert_str) >= 4:
                            key = f"{name}|{cert_str}"
                            person_map.setdefault(key, [])
                            if doc_id not in person_map[key]:
                                person_map[key].append(doc_id)

                id_tail = normalize_id_tail(person.get("id_number"))
                if id_tail:
                    key_id = f"{name}|ID:{id_tail}"
                    person_map.setdefault(key_id, [])
                    if doc_id not in person_map[key_id]:
                        person_map[key_id].append(doc_id)

                role = str(person.get("role") or "").strip()
                if role:
                    key_role = f"{name}|ROLE:{role}"
                    person_map.setdefault(key_role, [])
                    if doc_id not in person_map[key_role]:
                        person_map[key_role].append(doc_id)

                key_name_only = f"{name}|"
                person_map.setdefault(key_name_only, [])
                if doc_id not in person_map[key_name_only]:
                    person_map[key_name_only].append(doc_id)

        overlaps = {k: v for k, v in person_map.items() if len(v) >= 2}
        return overlaps

    def _build_company_relations(self) -> list[tuple[int, int, list[str]]]:
        """D2: 构建企业关系图，发现法人/电话/地址/银行重叠"""
        edges: list[tuple[int, int, list[str]]] = []
        doc_ids = list(self._extractions.keys())

        for id_a, id_b in combinations(doc_ids, 2):
            ca = self._extractions[id_a].get("company_info") or {}
            cb = self._extractions[id_b].get("company_info") or {}
            if not isinstance(ca, dict) or not isinstance(cb, dict):
                continue

            relations = []
            lr_a = (ca.get("legal_representative") or "").strip()
            lr_b = (cb.get("legal_representative") or "").strip()
            if lr_a and lr_b and lr_a == lr_b:
                relations.append(f"法定代表人相同: {lr_a}")

            company_a = normalize_company_name(ca.get("company_name"))
            company_b = normalize_company_name(cb.get("company_name"))
            if company_a and company_b and company_a == company_b:
                relations.append("企业名称核心词高度一致")

            # D2: 按精确情形生成精确叙述，消除"或"字硬编码
            credit_a = ca.get("unified_social_credit_code")
            credit_b = cb.get("unified_social_credit_code")
            credit_kind = credit_code_kind(credit_a, credit_b)
            if credit_kind == "exact":
                norm = normalize_credit_code(credit_a)
                relations.append(f"统一社会信用代码完全一致：{norm}")
            elif credit_kind == "prefix":
                norm = normalize_credit_code(credit_a)
                relations.append(
                    f"统一社会信用代码登记前缀一致（前 8 位 {norm[:8]}）"
                )

            phone_a = (ca.get("contact_phone") or "").strip()
            phone_b = (cb.get("contact_phone") or "").strip()
            phone_kind = nearby_phone_kind(phone_a, phone_b)
            if phone_kind == "exact":
                relations.append(f"联系电话完全相同：{phone_a}")
            elif phone_kind == "fuzzy":
                relations.append(f"联系电话号段相邻：{phone_a} / {phone_b}")

            addr_a = (ca.get("registered_address") or "").strip()
            addr_b = (cb.get("registered_address") or "").strip()
            if addr_a and addr_b and len(addr_a) >= 6 and addr_a == addr_b:
                relations.append(f"注册地址完全相同：{addr_a}")
            else:
                addr_kind = address_building_kind(addr_a, addr_b)
                if addr_kind == "exact":
                    relations.append(
                        "注册地址归一化后完全相同（同楼栋同地址主体）"
                    )

            bank_a = (ca.get("bank_account") or "").strip()
            bank_b = (cb.get("bank_account") or "").strip()
            if bank_a and bank_b:
                if normalize_bank_account(bank_a) == normalize_bank_account(bank_b):
                    relations.append(f"银行账户相同: {bank_a}")

            if relations:
                edges.append((id_a, id_b, relations))

        return edges

    def _analyze_pricing(self) -> list[dict]:
        """D4: 全局报价分布分析"""
        prices: dict[int, float] = {}
        for doc_id, ext in self._extractions.items():
            price_info = ext.get("price_info") or {}
            if not isinstance(price_info, dict):
                continue
            raw = price_info.get("total_price")
            if raw is None:
                continue
            parsed = self._parse_price(raw)
            if parsed is not None and parsed > 0:
                prices[doc_id] = parsed

        if len(prices) < 3:
            return []

        patterns: list[dict] = []
        sorted_items = sorted(prices.items(), key=lambda x: x[1])
        sorted_prices = [p for _, p in sorted_items]
        sorted_ids = [did for did, _ in sorted_items]

        diffs = [sorted_prices[i + 1] - sorted_prices[i] for i in range(len(sorted_prices) - 1)]
        if len(diffs) >= 2 and min(diffs) > 0:
            avg_diff = sum(diffs) / len(diffs)
            max_deviation = max(abs(d - avg_diff) for d in diffs)
            if max_deviation / avg_diff < 0.15:
                patterns.append({
                    "type": "equal_spacing",
                    "involved_doc_ids": sorted_ids,
                    "description": f"报价呈等差分布，差值约 {avg_diff:.0f}",
                    "evidence": f"价格序列: {[f'{p:.0f}' for p in sorted_prices]}",
                })

        if len(sorted_prices) >= 4:
            low = sorted_prices[0]
            second = sorted_prices[1]
            if second > 0 and low / second < 0.8:
                patterns.append({
                    "type": "outlier",
                    "involved_doc_ids": [sorted_ids[0]],
                    "description": (
                        f"{self._doc_names.get(sorted_ids[0], '?')} 报价 {low:.0f} "
                        f"远低于第二低 {second:.0f}，疑似围标主标方"
                    ),
                    "evidence": f"最低价/次低价 = {low / second:.2%}",
                })

        self._detect_price_clusters(prices, patterns)
        self._detect_price_precision_patterns(prices, patterns)

        return patterns

    def _detect_price_clusters(self, prices: dict[int, float], patterns: list[dict]) -> None:
        """检测报价中异常接近的聚类（价格差异<5%的文档组）"""
        doc_ids = list(prices.keys())
        threshold = 0.05

        clusters: list[set[int]] = []
        for id_a, id_b in combinations(doc_ids, 2):
            pa, pb = prices[id_a], prices[id_b]
            avg = (pa + pb) / 2
            if avg > 0 and abs(pa - pb) / avg < threshold:
                merged = False
                for cluster in clusters:
                    if id_a in cluster or id_b in cluster:
                        cluster.add(id_a)
                        cluster.add(id_b)
                        merged = True
                        break
                if not merged:
                    clusters.append({id_a, id_b})

        for cluster in clusters:
            if len(cluster) >= 2:
                ids = sorted(cluster)
                names = [self._doc_names.get(d, f"文件{d}") for d in ids]
                vals = [f"{prices[d]:.0f}" for d in ids]
                patterns.append({
                    "type": "cluster",
                    "involved_doc_ids": ids,
                    "description": f"报价异常接近（差异<5%）: {', '.join(names)}",
                    "evidence": f"价格: {', '.join(vals)}",
                })

    def _detect_price_precision_patterns(self, prices: dict[int, float], patterns: list[dict]) -> None:
        """检测价格尾数/小数位习惯一致。

        P1-4: 排除 "00" 尾数。整数报价的"末两位为 00"在实际投标中极为常见,
        不具备可疑性, 排除以避免误报。与 _algorithm_d4_score 保持一致。
        """
        if len(prices) < 3:
            return
        tails: dict[str, list[int]] = {}
        for doc_id, price in prices.items():
            tail = f"{int(round(price)) % 100:02d}"
            tails.setdefault(tail, []).append(doc_id)
        for tail, ids in tails.items():
            # P1-4: 与 _algorithm_d4_score 对齐, "00" 尾数不告警
            if tail == "00":
                continue
            if len(ids) >= 3:
                names = [self._doc_names.get(d, f"文件{d}") for d in ids]
                patterns.append({
                    "type": "precision_tail",
                    "involved_doc_ids": ids,
                    "description": f"报价尾数习惯一致（末两位均为 {tail}）: {', '.join(names)}",
                    "evidence": f"尾数 {tail} 在 {len(ids)} 份文件中重复出现",
                })

    def _cluster_metadata(self) -> list[set[int]]:
        """D8: 按PDF元数据聚类

        P1-3 行为变更：
        - creator / producer 字段值命中通用 PDF 工具黑名单时跳过聚类
          （否则"全员 Microsoft Word"等情况会造成 D8 误报）
        - author 字段不受黑名单影响（人名/账户名通常不在工具黑名单中）
        """
        from app.core.collusion.analyzer_parts.algorithmic import _is_generic_pdf_tool

        creator_groups: dict[str, set[int]] = {}
        author_groups: dict[str, set[int]] = {}
        producer_groups: dict[str, set[int]] = {}

        for doc_id, ext in self._extractions.items():
            meta = ext.get("metadata_info") or {}
            if not isinstance(meta, dict):
                continue

            creator = (meta.get("creator_tool") or "").strip()
            if creator and len(creator) >= 3 and not _is_generic_pdf_tool(creator):
                creator_groups.setdefault(creator, set()).add(doc_id)

            author = (meta.get("author") or "").strip()
            if author and len(author) >= 2:
                author_groups.setdefault(author, set()).add(doc_id)

            producer = (meta.get("producer") or "").strip()
            if producer and len(producer) >= 3 and not _is_generic_pdf_tool(producer):
                producer_groups.setdefault(producer, set()).add(doc_id)

        clusters = []
        for group in (
            list(creator_groups.values())
            + list(author_groups.values())
            + list(producer_groups.values())
        ):
            if len(group) >= 2 and group not in clusters:
                clusters.append(group)

        return clusters

    def _build_findings(self, result: GlobalFeatureResult) -> list[dict]:
        """将各维度结果转换为统一格式的全局发现列表"""
        findings: list[dict] = []

        for person_key, doc_ids in result.personnel_overlaps.items():
            parts = person_key.split("|", 1)
            name = parts[0]
            marker = parts[1] if len(parts) > 1 and parts[1] else None
            names = [self._doc_names.get(d, f"文件{d}") for d in doc_ids]
            desc = f"人员 {name}"
            if marker:
                if marker.startswith("ID:"):
                    desc += f"(身份证尾号: {marker.removeprefix('ID:')})"
                elif marker.startswith("ROLE:"):
                    desc += f"(岗位: {marker.removeprefix('ROLE:')})"
                else:
                    desc += f"(证书号: {marker})"
            desc += f" 同时出现在 {len(doc_ids)} 份投标文件中: {', '.join(names)}"
            findings.append({
                "type": "personnel_overlap",
                "involved_doc_ids": doc_ids,
                "involved_names": names,
                "description": desc,
            })

        for id_a, id_b, relations in result.company_edges:
            name_a = self._doc_names.get(id_a, f"文件{id_a}")
            name_b = self._doc_names.get(id_b, f"文件{id_b}")
            findings.append({
                "type": "company_relation",
                "involved_doc_ids": [id_a, id_b],
                "involved_names": [name_a, name_b],
                "description": f"{name_a} 与 {name_b}: {'; '.join(relations)}",
            })

        for pattern in result.pricing_patterns:
            ids = pattern.get("involved_doc_ids", [])
            names = [self._doc_names.get(d, f"文件{d}") for d in ids]
            findings.append({
                "type": "pricing_pattern",
                "involved_doc_ids": ids,
                "involved_names": names,
                "description": pattern.get("description", ""),
            })

        for cluster in result.metadata_clusters:
            ids = sorted(cluster)
            names = [self._doc_names.get(d, f"文件{d}") for d in ids]
            findings.append({
                "type": "metadata_cluster",
                "involved_doc_ids": ids,
                "involved_names": names,
                "description": f"PDF元数据聚类: {', '.join(names)} 的创建工具或作者信息一致",
            })

        # 跨字段串联命中（来自 _run_cross_field_correlation）
        if result.cross_field_hits:
            try:
                from app.core.collusion.cross_field_correlator import CrossFieldCorrelator
                findings.extend(
                    CrossFieldCorrelator.to_findings(result.cross_field_hits)
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[CrossField] cross_field_hits 转换失败: {exc}")

        return findings

    def _result_cache_pricing_patterns(self) -> list[dict]:
        """延迟缓存：避免重复计算 pricing_patterns。"""
        if not hasattr(self, "_cached_pricing"):
            self._cached_pricing = self._analyze_pricing()
        return self._cached_pricing

    def get_smart_filter_pairs(
        self,
        all_pairs: list[tuple[int, int]],
        candidate_scores: dict[tuple[int, int], float],
        algorithm_d1d2_hits: set[tuple[int, int]],
        *,
        include_pricing_patterns: bool = False,
    ) -> list[tuple[int, int]]:
        """基于全局特征 + 相似度 + 算法命中，产出必须做LLM分析的对

        Args:
            include_pricing_patterns: P1-1 灰度开关。启用时，将报价异常涉及的
                doc_ids 两两组合加入 must_llm；outlier 类型（仅 1 个 doc_id）与
                其他所有 doc 两两组合加入，覆盖"报价显著偏离"的陪标特征。
        """
        must_llm: set[tuple[int, int]] = set()
        all_pairs_set = set(all_pairs)

        threshold = settings.COLLUSION_SMART_FILTER_THRESHOLD
        for pair in all_pairs:
            score = candidate_scores.get(pair, 0.0)
            if score >= threshold:
                must_llm.add(pair)

        must_llm.update(algorithm_d1d2_hits)

        for _, doc_ids in self._result_cache_personnel_overlaps().items():
            if len(doc_ids) >= 2:
                for a, b in combinations(doc_ids, 2):
                    pair = (min(a, b), max(a, b))
                    if pair in all_pairs_set:
                        must_llm.add(pair)

        for id_a, id_b, _ in self._result_cache_company_edges():
            pair = (min(id_a, id_b), max(id_a, id_b))
            if pair in all_pairs_set:
                must_llm.add(pair)

        # P1-1: D4 报价异常全覆盖（灰度）
        if include_pricing_patterns:
            all_doc_ids = sorted({d for pair in all_pairs for d in pair})
            for pattern in self._result_cache_pricing_patterns():
                involved = list(pattern.get("involved_doc_ids") or [])
                ptype = pattern.get("type")
                if not involved:
                    continue
                if ptype == "outlier" and len(involved) == 1:
                    # outlier 主标方：与其他所有 doc 两两组合都应做 LLM 分析
                    outlier_id = involved[0]
                    for other in all_doc_ids:
                        if other == outlier_id:
                            continue
                        pair = (min(outlier_id, other), max(outlier_id, other))
                        if pair in all_pairs_set:
                            must_llm.add(pair)
                    continue
                for a, b in combinations(involved, 2):
                    pair = (min(a, b), max(a, b))
                    if pair in all_pairs_set:
                        must_llm.add(pair)

        max_pairs = settings.COLLUSION_MAX_LLM_PAIRS
        if len(must_llm) > max_pairs:
            ranked = sorted(
                must_llm,
                key=lambda p: candidate_scores.get(p, 0.0),
                reverse=True,
            )
            must_llm = set(ranked[:max_pairs])

        if not must_llm and all_pairs:
            top_n = min(10, len(all_pairs))
            ranked = sorted(
                all_pairs,
                key=lambda p: candidate_scores.get(p, 0.0),
                reverse=True,
            )
            must_llm = set(ranked[:top_n])

        return sorted(must_llm)

    def _result_cache_personnel_overlaps(self) -> dict[str, list[int]]:
        """延迟缓存：避免重复计算"""
        if not hasattr(self, "_cached_personnel"):
            self._cached_personnel = self._build_personnel_matrix()
        return self._cached_personnel

    def _result_cache_company_edges(self) -> list[tuple[int, int, list[str]]]:
        if not hasattr(self, "_cached_company"):
            self._cached_company = self._build_company_relations()
        return self._cached_company

    @staticmethod
    def _parse_price(raw) -> Optional[float]:
        """解析报价字符串为浮点数（实例方法保留以兼容旧调用）。"""
        return parse_price(raw)


def parse_price(raw) -> Optional[float]:
    """P1-1: 模块级公开的报价解析函数，供 analyzer._algorithm_compare 与
    _get_algorithm_hits 复用，避免重复实现。支持 int/float、带"万/亿/元/逗号"
    的字符串；无法解析返回 None。
    """
    if isinstance(raw, (int, float)):
        return float(raw)
    if not isinstance(raw, str):
        return None
    cleaned = re.sub(r'[,，\s元万亿]', '', raw)
    try:
        val = float(cleaned)
        if "万" in str(raw):
            val *= 10000
        elif "亿" in str(raw):
            val *= 100000000
        return val
    except (ValueError, TypeError):
        return None
