"""离线 RAG 分块与检索基准。

本基准只调用现有分块组件，不访问数据库、不调用真实 embedding 模型。
它同时评估主文档结构化入口与历史 page_tuple 兼容入口，用于量化表格、
图片证据和章节元数据对召回质量的影响。
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.core.parser.chunk_planner import ChunkPlanner
from app.core.parser.image_extractor import (
    ExtractedImage,
    build_natural_evidence_chunk,
    generate_image_annotations,
    insert_annotation_near_anchor,
)
from app.core.parser.pdf_parser import ParsedPage
from app.core.parser.table_extractor import ExtractedTable
from app.core.parser.text_chunker import TextChunk
from app.core.writer.material_indexer import MaterialIndexer


BENCHMARK_ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = BENCHMARK_ROOT / "data" / "rag_gold" / "manifest.json"
DEFAULT_RESULT_PATH = BENCHMARK_ROOT / "results" / "rag_chunk_baseline_current.json"
TOP_K = 5


@dataclass(frozen=True)
class QueryScore:
    """单个查询在某个分块 profile 下的评分。"""

    query_id: str
    phrase_recall: float
    page_recall: float
    type_recall: float
    metadata_score: float
    hit_at_k: float
    mrr: float
    score: float
    top_chunk_indexes: list[int]
    top_pages: list[int | None]
    top_chunk_types: list[str]


@dataclass(frozen=True)
class ScenarioScore:
    """单个场景在某个分块 profile 下的评分。"""

    scenario_id: str
    category: str
    query_scores: list[QueryScore]
    score: float


@dataclass(frozen=True)
class ProfileScore:
    """一个分块 profile 的整体评分。"""

    profile: str
    description: str
    scenario_scores: list[ScenarioScore]
    score: float


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    """加载黄金样本 manifest。"""
    return json.loads(path.read_text(encoding="utf-8"))


def run_baseline(manifest_path: Path = MANIFEST_PATH) -> dict[str, Any]:
    """运行所有 profile 的离线基准并返回可序列化结果。"""
    manifest = load_manifest(manifest_path)
    profiles = [
        (
            "structured_main",
            "主文档结构化入口: ParsedPage + table_row_with_header + independent image_evidence",
            _build_structured_chunks,
        ),
        (
            "tuple_compat",
            "历史 page_tuple 兼容入口: 仅页文本 + 内联图片证据, 无结构化表格 chunk",
            _build_tuple_compat_chunks,
        ),
        (
            "material_indexer",
            "优化后写标素材入口: MaterialIndexer 直接消费 ParsedPage 结构",
            _build_material_indexer_chunks,
        ),
    ]

    profile_scores: list[ProfileScore] = []
    for profile_name, description, builder in profiles:
        scenario_scores = [
            _score_scenario(profile_name, scenario, builder(scenario))
            for scenario in manifest["scenarios"]
        ]
        profile_scores.append(
            ProfileScore(
                profile=profile_name,
                description=description,
                scenario_scores=scenario_scores,
                score=_average(item.score for item in scenario_scores),
            )
        )

    return {
        "version": manifest.get("version"),
        "top_k": TOP_K,
        "profiles": [_profile_to_dict(item) for item in profile_scores],
        "summary": {
            item.profile: {
                "score": round(item.score, 4),
                "scenario_count": len(item.scenario_scores),
            }
            for item in profile_scores
        },
    }


def write_baseline_result(result: dict[str, Any], path: Path = DEFAULT_RESULT_PATH) -> None:
    """写入基准结果 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _build_structured_chunks(scenario: dict[str, Any]) -> list[TextChunk]:
    planner = ChunkPlanner()
    pages = _build_pages(scenario)
    _inline_image_annotations(pages)
    chunks = planner.plan_parsed_pages(pages)
    _append_image_evidence_chunks(planner, pages, chunks)
    return chunks


def _build_tuple_compat_chunks(scenario: dict[str, Any]) -> list[TextChunk]:
    planner = ChunkPlanner()
    pages = _build_pages(scenario)
    page_texts: list[tuple[int, str]] = []
    for page in pages:
        page_text = page.text or ""
        if page.images:
            evidence = build_natural_evidence_chunk(
                page_num=page.page_num,
                images=page.images,
                context=page_text,
            )
            if evidence:
                page_text = f"{page_text}\n{evidence}"
        page_texts.append((page.page_num, page_text))
    return planner.plan_page_tuples(page_texts)


def _build_material_indexer_chunks(scenario: dict[str, Any]) -> list[TextChunk]:
    indexer = MaterialIndexer(db=None)  # type: ignore[arg-type]
    pages = _build_pages(scenario)
    _inline_image_annotations(pages)
    content = "\n\n".join(page.text or "" for page in pages)
    return indexer.plan_material_chunks(content=content, page_texts=pages)


def _build_pages(scenario: dict[str, Any]) -> list[ParsedPage]:
    pages: list[ParsedPage] = []
    for raw_page in scenario.get("pages", []):
        page_num = int(raw_page["page_num"])
        tables = [
            _build_table(page_num=page_num, table_data=item)
            for item in raw_page.get("tables", [])
        ]
        images = [
            ExtractedImage(
                image_index=idx,
                page_num=page_num,
                width=int(item.get("width", 900)),
                height=int(item.get("height", 1200)),
                image_format=str(item.get("image_format", "jpeg")),
                xref=idx,
                spatial_context=str(item.get("spatial_context", "")),
                visual_content=str(item.get("visual_content", "")),
            )
            for idx, item in enumerate(raw_page.get("images", []))
        ]
        pages.append(
            ParsedPage(
                page_num=page_num,
                text=str(raw_page.get("text", "")),
                tables=tables,
                images=images,
            )
        )
    return pages


def _build_table(page_num: int, table_data: dict[str, Any]) -> ExtractedTable:
    data = [[str(cell) for cell in row] for row in table_data.get("data", [])]
    col_count = max((len(row) for row in data), default=0)
    return ExtractedTable(
        table_index=int(table_data.get("table_index", 0)),
        page_num=page_num,
        data=data,
        row_count=len(data),
        col_count=col_count,
    )


def _inline_image_annotations(pages: list[ParsedPage]) -> None:
    for idx, page in enumerate(pages):
        if not page.images:
            continue
        window_text = _neighbor_context(pages, idx)
        annotation = generate_image_annotations(
            page.images,
            context=window_text or page.text,
        )
        if annotation:
            page.text = insert_annotation_near_anchor(page.text or "", annotation)


def _append_image_evidence_chunks(
    planner: ChunkPlanner,
    pages: list[ParsedPage],
    chunks: list[TextChunk],
) -> None:
    for idx, page in enumerate(pages):
        if not page.images:
            continue
        evidence = build_natural_evidence_chunk(
            page_num=page.page_num,
            images=page.images,
            context=_neighbor_context(pages, idx) or page.text,
        )
        if not evidence or len(evidence) < 20:
            continue
        chunk = planner.build_manual_chunk(
            content=evidence,
            page_num=page.page_num,
            chunk_type="image_evidence",
            parent_block_id=f"image-evidence:{page.page_num}",
            section_path="图片证据",
        )
        chunk.chunk_index = len(chunks)
        chunk.metadata["is_evidence"] = True
        chunks.append(chunk)


def _neighbor_context(pages: list[ParsedPage], index: int) -> str:
    parts: list[str] = []
    for offset in (-2, -1, 1, 2):
        ni = index + offset
        if 0 <= ni < len(pages):
            text = pages[ni].text or ""
            if text.strip():
                parts.append(text.strip())
    return "\n".join(parts)


def _score_scenario(
    profile_name: str,
    scenario: dict[str, Any],
    chunks: list[TextChunk],
) -> ScenarioScore:
    query_scores = [
        _score_query(profile_name, query, chunks)
        for query in scenario.get("queries", [])
    ]
    return ScenarioScore(
        scenario_id=str(scenario["id"]),
        category=str(scenario["category"]),
        query_scores=query_scores,
        score=_average(item.score for item in query_scores),
    )


def _score_query(
    profile_name: str,
    query: dict[str, Any],
    chunks: list[TextChunk],
) -> QueryScore:
    ranked = _rank_chunks(str(query["text"]), chunks)
    top = ranked[:TOP_K]
    union_text = "\n".join(chunk.content for _, chunk in top)

    must_phrases = [str(item) for item in query.get("must_phrases", [])]
    expected_pages = {int(item) for item in query.get("expected_pages", [])}
    expected_types = {str(item) for item in query.get("expected_chunk_types", [])}

    phrase_recall = _ratio(1 for phrase in must_phrases if phrase in union_text)
    if must_phrases:
        phrase_recall = sum(1 for phrase in must_phrases if phrase in union_text) / len(must_phrases)
    else:
        phrase_recall = 1.0

    top_pages = [_chunk_page(chunk) for _, chunk in top]
    page_recall = 1.0 if expected_pages and expected_pages.intersection(
        page for page in top_pages if page is not None
    ) else 0.0
    if not expected_pages:
        page_recall = 1.0

    top_types = [_chunk_type(chunk) for _, chunk in top]
    if expected_types:
        type_recall = len(expected_types.intersection(top_types)) / len(expected_types)
    else:
        type_recall = 1.0

    metadata_score = _metadata_score(top)
    mrr = _mrr(top, must_phrases, expected_pages)
    hit_at_k = 1.0 if phrase_recall > 0 and page_recall > 0 else 0.0

    score = (
        phrase_recall * 0.45
        + page_recall * 0.20
        + type_recall * 0.15
        + metadata_score * 0.10
        + mrr * 0.10
    )

    return QueryScore(
        query_id=str(query["id"]),
        phrase_recall=round(phrase_recall, 4),
        page_recall=round(page_recall, 4),
        type_recall=round(type_recall, 4),
        metadata_score=round(metadata_score, 4),
        hit_at_k=round(hit_at_k, 4),
        mrr=round(mrr, 4),
        score=round(score, 4),
        top_chunk_indexes=[chunk.chunk_index for _, chunk in top],
        top_pages=top_pages,
        top_chunk_types=top_types,
    )


def _rank_chunks(query: str, chunks: list[TextChunk]) -> list[tuple[float, TextChunk]]:
    query_tokens = _tokens(query)
    ranked: list[tuple[float, TextChunk]] = []
    for chunk in chunks:
        chunk_tokens = _tokens(chunk.content)
        overlap = len(query_tokens.intersection(chunk_tokens))
        denom = math.sqrt(max(len(query_tokens), 1) * max(len(chunk_tokens), 1))
        lexical = overlap / denom if denom else 0.0
        exact_boost = 0.0
        for token in query_tokens:
            if len(token) >= 3 and token in chunk.content:
                exact_boost += 0.01
        type_boost = 0.03 if _chunk_type(chunk) in {"table_row_with_header", "image_evidence"} else 0.0
        ranked.append((lexical + min(exact_boost, 0.1) + type_boost, chunk))
    ranked.sort(key=lambda item: (item[0], -item[1].chunk_index), reverse=True)
    return ranked


def _tokens(text: str) -> set[str]:
    raw = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", text)
    tokens: set[str] = set()
    for item in raw:
        if not item:
            continue
        tokens.add(item.lower())
        if re.fullmatch(r"[\u4e00-\u9fff]+", item):
            for size in (2, 3, 4):
                for idx in range(0, max(len(item) - size + 1, 0)):
                    tokens.add(item[idx: idx + size])
    return tokens


def _mrr(
    ranked: list[tuple[float, TextChunk]],
    phrases: list[str],
    expected_pages: set[int],
) -> float:
    for idx, (_, chunk) in enumerate(ranked, start=1):
        page = _chunk_page(chunk)
        page_ok = not expected_pages or page in expected_pages
        phrase_ok = not phrases or any(phrase in chunk.content for phrase in phrases)
        if page_ok and phrase_ok:
            return 1.0 / idx
    return 0.0


def _metadata_score(ranked: list[tuple[float, TextChunk]]) -> float:
    if not ranked:
        return 0.0
    scored = 0
    for _, chunk in ranked:
        meta = chunk.metadata or {}
        if meta.get("section_path") or meta.get("clause_no") or meta.get("parent_block_id"):
            scored += 1
    return scored / len(ranked)


def _chunk_page(chunk: TextChunk) -> int | None:
    page = chunk.metadata.get("page_num") or chunk.metadata.get("page")
    return int(page) if page is not None else None


def _chunk_type(chunk: TextChunk) -> str:
    return str(chunk.metadata.get("chunk_type") or "text")


def _average(values: Any) -> float:
    items = [float(item) for item in values]
    return round(sum(items) / len(items), 4) if items else 0.0


def _ratio(values: Any) -> float:
    items = list(values)
    return float(sum(items) / len(items)) if items else 1.0


def _profile_to_dict(profile: ProfileScore) -> dict[str, Any]:
    data = asdict(profile)
    data["score"] = round(profile.score, 4)
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="Run offline RAG chunk baseline.")
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_RESULT_PATH)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    result = run_baseline(args.manifest)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    if not args.no_write:
        write_baseline_result(result, args.output)


if __name__ == "__main__":
    main()
