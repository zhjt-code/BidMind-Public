"""评分项提取 VLM 视觉通道 MVP 验证脚本。

职责: 将评分样本 PDF 整页渲染为高清图片, 送 vision 池 VLM 按层级本体 +
      JSON Lines 行式契约输出评分细项, 与训练集真值对比产出名称/二元组
      P/R/F1 与总分闭合率, 并与确定性链基线并列呈现; --external 模式对
      无真值外部样本做泛化实测(仅自洽闭合, 不跑确定性链)。
不做: 不落库、不接生产提取链路、不修改被评估代码、
      不读取冻结验证集语料与真值 (sample_corpus_holdout_round2 绝对禁止)。

用法 (在 backend 目录下执行):
    ..\\.venv\\Scripts\\python scripts\\evaluate_scoring_vlm_mvp.py --limit 1
    ..\\.venv\\Scripts\\python scripts\\evaluate_scoring_vlm_mvp.py --rescore-from-raw
    ..\\.venv\\Scripts\\python scripts\\evaluate_scoring_vlm_mvp.py --external ^
        --samples-dir "<外部样本目录>" --name-template "验证评分表结构-{index}.pdf"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Literal, Optional

_BACKEND_DIR = Path(__file__).parent.parent
_PROJECT_ROOT = _BACKEND_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from scripts.audit_scoring_legacy_baseline import (  # noqa: E402
    CORPUS_DIR,
    DOWNGRADED_HOLDOUT_CORPUS_DIR,
    DOWNGRADED_HOLDOUT_KEYS,
    TRAINING_KEYS,
    TRUTH_PATH,
    _ensure_test_env,
    compute_item_metrics,
    compute_item_pair_metrics,
    compute_closure_status,
    load_downgraded_holdout_truth,
    parse_truth_markdown,
    run_deterministic_extraction_for_sample,
)

DEFAULT_RAW_DIR = _BACKEND_DIR / "scratch" / "vlm_mvp"
DEFAULT_REPORT_PATH = _PROJECT_ROOT / "docs" / "reports" / "scoring_vlm_mvp_report.md"
PDF_NAME_TEMPLATE = "评分表结构-{index}.pdf"
MAX_PAGES_HARD_CAP = 30

SYSTEM_PROMPT = """你是评标办法结构化提取专家, 任务是从招标文件评分办法的整页图片中提取全部评分细项。

评分体系的通用层级模型(自外向内, 任一层都可能缺省):
1. 采购包/标段(lot): 一个文件可含多个包, 各包有独立评分体系;
2. 评分方案(scheme): 同一包内可能并列多套独立评分模板(如按项目类型选用不同详评模板), 各自独立满分, 绝不可混算;
3. 评分卷/部分(volume): 如技术卷、商务卷、资信卷、价格卷, 常带各自满分与权重;
4. 子卷/分组(group): 卷下的中间分组, 如技术卷下的"安装、质量、调试";
5. 评分细项(item): 最终计分行, 是你要输出的基本单位。

必须识别的特殊形态:
- 框架表+明细表两层: 框架表只声明各卷满分/权重(如"技术40%/价格60%"), 明细行在后续表格; 同一分值不得两层重复输出——有对应明细时框架行只进 header 的 volume_totals, 无任何明细的卷才把框架行作为 item 输出;
- 原始满分与折算权重并列: 如"技术卷满分100分, 按40%权重折算"——item 分值记卷内原始分值, 折算权重只记入 volume_totals 的 weight, 禁止自行折算;
- 调整项: 总分之外的额外加分(bonus)/扣分(penalty)行, 不计入基础满分;
- 价格公式行: 整个价格评分输出一条 item(fact_type=price_formula), 公式与基准价说明并入其 score_rules;
- 档次行: "优/良/一般"分档或阶段性说明共享同一分值单元格时, 它们是评分标准而非子项, 不得拆分;
- 转置表: 卷名为列、权重为行的横排形态, 同样要识别;
- 文字段落式评分条款: 不在表格中, 以"总分N分, 其中A占x分…"句式写在正文里, 同样要提取;
- 跨页续表: 同一表格延续到后续页, 续页行不得遗漏;
- 排除项: 资格性/符合性/响应性/初步评审等以"通过/不通过、合格/不合格"为结论且无分值的审查表, 不属于评分体系, 整表跳过。

输出采用 JSON Lines 格式: 每行一个独立的JSON对象, 不输出任何其他文字、解释或markdown围栏。"""

USER_PROMPT_TEMPLATE = """请仔细阅读图片中的评分办法, 按 JSON Lines 逐行输出提取结果。

第一行输出总体信息(header):
{{"type": "header", "declared_total": 100, "volume_totals": [{{"label": "技术卷", "total": 100, "weight": 0.4}}]}}

之后每个评分细项独立一行(item):
{{"type": "item", "score_item_name": "评分项名称", "score_weight": 10, "group_path": ["采购包1", "技术卷", "安装"], "fact_type": "base_score", "score_rules": ["评分标准原文"], "source_page": 1}}

字段与提取规则(必须遵守):
1. score_item_name 与 group_path 各层必须逐字照抄图片原文, 禁止改写、概括、补全或翻译。
2. group_path 为该项自外向内的层级路径(包→方案→卷→子卷), 无该层则省略该层,
   顶层无任何分组时用空数组 []; 路径只含上级分组标题, 不得包含该评分项自身名称。
3. 以评分表的真实计分行为单位, 每行一条 item; 分组标题/卷标题不是评分项,
   只能进入其下各项的 group_path。若同一计分行存在多级名称列(如"评审内容"与
   "评审因素"), score_item_name 取语义最具体的那一列。
4. 层级结构处理:
   - 父项名称单元格下有多个各自带分值的计分子行时, 输出每个子行,
     score_item_name 写成"父项名称-子行名称"; 父项行本身不再单独输出。
   - 相邻多行共享同一个分值单元格(合并单元格)时, 它们同属一个评分项,
     只输出一条, 不要按说明行拆分。
   - "评分标准"列文字中列举的细则(如"其中A占3分、B占5分")只是评分规则,
     不是独立评分项, 禁止据此拆分出新条目。
5. score_weight 为该项满分数值: "0-5分"取5, 档次分取最高档, "10分"取10。
   权重为0或不计分的行不要输出。
6. fact_type 取值: base_score(构成满分的常规评分项) / bonus(仅限原文明示
   "总分之外额外加分"的项) / penalty(扣分项, score_weight填0) /
   price_formula(价格评分项)。注意: 构成满分的行一律 base_score,
   不得因名称含"加分"就标 bonus。
7. 价格评分只输出一条 item, 不得把公式说明行重复计分。
8. header 的 declared_total: 原文明确声明的评分总分, 未声明填 null;
   volume_totals: 各卷/各部分声明的满分与权重, 未声明 weight 填 null。
   多套评分方案并存时, volume_totals 逐套列出并在 label 中带上方案名。
9. source_page: 该行所在的PDF页码。{page_hint}
10. 多包/多方案文档必须全部提取, 各项通过 group_path 区分归属, 不要遗漏续页。
11. 逐行输出JSON对象本身, 每行一个, 不要数组包装、不要围栏、不要解释。

本批共{image_count}张图片, 依次对应PDF第{page_start}至{page_end}页。"""

RETRY_SUFFIX = (
    "\n\n注意: 上一次输出无法解析。请严格按 JSON Lines 逐行输出——每行一个完整的"
    "JSON对象(第一行header, 之后每行一个item), 不要markdown围栏、不要解释文字。"
)


# ════════════════════════════════════════════════════════════════════════════
# VLM 输出契约 (镜像 ExtractedScoringItem 字段子集, 宽松纠偏)
# ════════════════════════════════════════════════════════════════════════════


def _build_models():
    """惰性构建 Pydantic 契约(先于 app 依赖导入 pydantic 是安全的)。

    返回具名空间: .item 单条评分项 / .volume 卷声明 / .result 单对象回退契约。
    """
    from types import SimpleNamespace

    from pydantic import BaseModel, Field, field_validator

    class VlmVolumeTotal(BaseModel):
        label: str = ""
        total: Optional[float] = None
        weight: Optional[float] = None

        @field_validator("total", "weight", mode="before")
        @classmethod
        def coerce_number(cls, v):
            return _coerce_float(v)

    class VlmScoringItem(BaseModel):
        score_item_name: str = Field(..., min_length=1)
        score_weight: float = Field(default=0.0, ge=0)
        group_path: list[str] = Field(default_factory=list)
        volume_label: Optional[str] = None
        fact_type: Literal["base_score", "bonus", "penalty", "price_formula"] = "base_score"
        score_rules: list[str] = Field(default_factory=list)
        source_page: Optional[int] = None

        @field_validator("score_weight", mode="before")
        @classmethod
        def coerce_weight(cls, v):
            value = _coerce_float(v)
            return 0.0 if value is None or value < 0 else value

        @field_validator("fact_type", mode="before")
        @classmethod
        def coerce_fact_type(cls, v):
            valid = {"base_score", "bonus", "penalty", "price_formula"}
            return v if v in valid else "base_score"

        @field_validator("score_rules", mode="before")
        @classmethod
        def coerce_rules(cls, v):
            if isinstance(v, str):
                return [v]
            return v if isinstance(v, list) else []

        @field_validator("group_path", mode="before")
        @classmethod
        def coerce_group_path(cls, v):
            if isinstance(v, str):
                return [v] if v.strip() else []
            if isinstance(v, list):
                return [str(part).strip() for part in v if str(part).strip()]
            return []

        def model_post_init(self, __context) -> None:
            # volume_label 缺省时由层级路径末层派生, 保持与旧raw文件/指标口径兼容。
            if not self.volume_label and self.group_path:
                self.volume_label = self.group_path[-1]

    class VlmScoringResult(BaseModel):
        declared_total: Optional[float] = None
        volume_totals: list[VlmVolumeTotal] = Field(default_factory=list)
        items: list[VlmScoringItem] = Field(default_factory=list)

        @field_validator("declared_total", mode="before")
        @classmethod
        def coerce_total(cls, v):
            return _coerce_float(v)

        @field_validator("items", mode="before")
        @classmethod
        def drop_invalid_items(cls, v):
            if not isinstance(v, list):
                return []
            return [entry for entry in v if isinstance(entry, dict) and entry.get("score_item_name")]

    return SimpleNamespace(item=VlmScoringItem, volume=VlmVolumeTotal, result=VlmScoringResult)


def _coerce_float(value) -> Optional[float]:
    """把 '10分'、'0-5' 等宽松写法纠偏为数值(取末尾数字), 无法解析返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    matches = re.findall(r"\d+(?:\.\d+)?", str(value))
    return float(matches[-1]) if matches else None


def extract_json_block(text: str) -> Optional[dict]:
    """从模型回复中提取首个平衡的JSON对象, 容忍思考段与代码块围栏。"""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    candidates = [fence.group(1)] if fence else []
    start = cleaned.find("{")
    if start >= 0:
        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(cleaned)):
            char = cleaned[idx]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(cleaned[start : idx + 1])
                    break
    for candidate in candidates:
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    return None


def extract_jsonl_records(text: str) -> tuple[Optional[dict], list[dict], int]:
    """解析 JSON Lines 响应, 返回 (header, item_dicts, broken_lines)。

    截断韧性是行式设计的核心收益: 响应尾部被 max_tokens 腰斩时,
    已完整输出的行仍全部可解析, 只损失尾行(计入 broken_lines)。
    容忍思考段、markdown围栏与行尾逗号。
    """
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    cleaned = re.sub(r"```(?:json)?", "\n", cleaned)
    header: Optional[dict] = None
    items: list[dict] = []
    broken = 0
    for raw_line in cleaned.splitlines():
        line = raw_line.strip().rstrip(",")
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            broken += 1
            continue
        if not isinstance(record, dict):
            continue
        if record.get("score_item_name"):
            items.append(record)
        elif header is None and (
            record.get("type") == "header"
            or "declared_total" in record
            or "volume_totals" in record
        ):
            header = record
    return header, items, broken


# ════════════════════════════════════════════════════════════════════════════
# 渲染与 VLM 调用
# ════════════════════════════════════════════════════════════════════════════


def render_pdf_pages(pdf_path: Path, dpi: int) -> list[str]:
    """整本渲染样本PDF(样本即评分区摘录件), 返回逐页 base64 PNG。"""
    import fitz

    from app.core.parser.page_renderer import render_page_to_base64

    images: list[str] = []
    with fitz.open(str(pdf_path)) as doc:
        page_count = min(doc.page_count, MAX_PAGES_HARD_CAP)
        if doc.page_count > MAX_PAGES_HARD_CAP:
            print(f"  [warn] {pdf_path.name} 共{doc.page_count}页, 截断至{MAX_PAGES_HARD_CAP}页")
        for page_index in range(page_count):
            images.append(render_page_to_base64(doc[page_index], dpi=dpi))
    return images


async def call_vlm_for_sample(
    client,
    images: list[str],
    *,
    max_pages_per_call: int,
    max_tokens: int,
    models,
) -> dict:
    """按页批次调用VLM并合并结果; 返回 {parsed, raw_responses, calls, parse_failures, broken_lines}。"""
    merged_items: list = []
    merged_volume_totals: list = []
    declared_total: Optional[float] = None
    raw_responses: list[str] = []
    parse_failures = 0
    broken_lines = 0
    calls = 0

    # 工作队列 (page_start, batch); 整批零产出(极端截断/拒答)且批内>1页时对半拆分,
    # 用更小的输出预算换取完整输出(与 chunk_budget_policy 的截断自愈同思路)。
    # JSON Lines 行式输出下, 普通尾部截断只损失尾行, 无需拆批。
    pending: list[tuple[int, list[str]]] = [
        (start + 1, images[start : start + max_pages_per_call])
        for start in range(0, len(images), max_pages_per_call)
    ]

    while pending:
        page_start, batch = pending.pop(0)
        page_end = page_start + len(batch) - 1
        prompt = USER_PROMPT_TEMPLATE.format(
            page_hint=f"本批第1张图片=第{page_start}页, 依次类推。",
            image_count=len(batch),
            page_start=page_start,
            page_end=page_end,
        )

        header: Optional[dict] = None
        item_dicts: list[dict] = []
        for attempt in range(2):
            calls += 1
            text = await client.chat_with_images(
                prompt if attempt == 0 else prompt + RETRY_SUFFIX,
                batch,
                system_message=SYSTEM_PROMPT,
                temperature=0.1,
                max_tokens=max_tokens,
            )
            raw_responses.append(text)
            header, item_dicts, broken = extract_jsonl_records(text)
            if header is None and not item_dicts:
                # 回退: 模型忽略行式要求输出了单个大JSON对象
                fallback = extract_json_block(text)
                if isinstance(fallback, dict):
                    header = {
                        "declared_total": fallback.get("declared_total"),
                        "volume_totals": fallback.get("volume_totals") or [],
                    }
                    item_dicts = [
                        entry
                        for entry in (fallback.get("items") or [])
                        if isinstance(entry, dict)
                    ]
            if item_dicts or header is not None:
                broken_lines += broken
                break
            parse_failures += 1

        if not item_dicts and header is None:
            if len(batch) >= 2:
                half = len(batch) // 2
                pending.insert(0, (page_start + half, batch[half:]))
                pending.insert(0, (page_start, batch[:half]))
                print(f"  [split] 第{page_start}-{page_end}页零产出, 对半拆分重试")
            continue

        for entry in item_dicts:
            try:
                merged_items.append(models.item(**entry))
            except Exception:  # noqa: BLE001 - 单行畸形不拖垮整批
                broken_lines += 1
        if header:
            for vt in header.get("volume_totals") or []:
                if isinstance(vt, dict):
                    merged_volume_totals.append(models.volume(**vt))
            if declared_total is None:
                declared_total = _coerce_float(header.get("declared_total"))

    parsed = {
        "declared_total": declared_total,
        "volume_totals": [vt.model_dump() for vt in merged_volume_totals],
        "items": [item.model_dump() for item in merged_items],
    }
    return {
        "parsed": parsed,
        "raw_responses": raw_responses,
        "calls": calls,
        "parse_failures": parse_failures,
        "broken_lines": broken_lines,
    }


# ════════════════════════════════════════════════════════════════════════════
# 逐样本评估
# ════════════════════════════════════════════════════════════════════════════


def vlm_total_score(items: list[dict]) -> float:
    """按事实层口径求总分: 调整项(bonus/penalty)不计基础满分。"""
    return sum(
        float(item.get("score_weight") or 0.0)
        for item in items
        if item.get("fact_type") in ("base_score", "price_formula")
    )


def folded_total_from_volumes(volume_totals: list[dict]) -> Optional[float]:
    """按卷声明计算折算后总分(sum(卷满分×权重)); 任一卷缺满分或权重则不可折算。

 对齐生产折算轴语义(折算轴对称化): 事实层保留卷内原始满分,
 "技术卷100×0.4+价格卷100×0.6=100" 与业务口径"技术40+价格60"等价。
 """
    if not volume_totals:
        return None
    folded = 0.0
    for vt in volume_totals:
        total = vt.get("total")
        weight = vt.get("weight")
        if total is None or weight is None or not (0 < float(weight) <= 1):
            return None
        folded += float(total) * float(weight)
    return round(folded, 4)


_STANDARD_ROW_MARKERS = ("评分标准", "计算公式", "得分计算", "评分办法")
_COMPOSITION_TABLE_MARKERS = ("分值构成", "评分构成", "分值组成", "评分分值")


def _compact_name(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _drop_duplicate_parent_rows(items: list[dict]) -> list[dict]:
    """对齐事实层红线一(不重复计分)的确定性去重, 名称须已净化。

    1. 父组行: 某项净化名出现在其他项的 group_path 中 → 它是别人的祖先分组,
       其分值已由子项承载, 删除(如"项目管理大纲部分30" vs 其下明细各项)。
    2. 标准行: 与另一项同分值、名称为其扩展前缀且含"评分标准/计算公式"类
       标记词 → 是同一评分项的说明行复述, 删除较长者(如"投标报价评分标准"
       vs "投标报价")。
    """
    from app.core.extractor.scoring.scoring_item_naming import (
        strip_scoring_name_decoration,
    )

    ancestor_names: set[str] = set()
    for item in items:
        own_name = _compact_name(item["score_item_name"])
        for part in item.get("group_path") or []:
            cleaned_part = _compact_name(strip_scoring_name_decoration(str(part)))
            # 模型常把项自身名称也写进自己的路径末位, 该自引用不构成"祖先"证据,
            # 不排除会使全部项互相毁灭(实测样本12: 13项→0项)。
            if cleaned_part and cleaned_part != own_name:
                ancestor_names.add(cleaned_part)

    kept: list[dict] = []
    for item in items:
        if _compact_name(item["score_item_name"]) in ancestor_names:
            continue
        kept.append(item)

    result: list[dict] = []
    for item in kept:
        name = _compact_name(item["score_item_name"])
        is_standard_dup = False
        for other in kept:
            if other is item:
                continue
            other_name = _compact_name(other["score_item_name"])
            if (
                name != other_name
                and name.startswith(other_name)
                and float(item.get("score_weight") or 0.0)
                == float(other.get("score_weight") or 0.0)
                and any(marker in name for marker in _STANDARD_ROW_MARKERS)
            ):
                is_standard_dup = True
                break
        if not is_standard_dup:
            result.append(item)
    return _dedupe_exact_and_composition(result)


def _dedupe_exact_and_composition(items: list[dict]) -> list[dict]:
    """对齐生产 _dedupe_exact 语义的两条精确去重, 名称须已净化。

 1. 完全重复: (净化名, 分值, fact_type, group_path) 全同的行只保留首个
 (跨页重复表头行, 形态; 多包/多方案合法同名项因路径不同不受影响)。
 2. 构成表副本: 路径含"分值构成"类标记的行, 若明细中存在同名同分项,
 构成表行是汇总层副本, 删除(框架/明细两层不得双计, 红线一)。
 """
    seen: set[tuple] = set()
    deduped: list[dict] = []
    for item in items:
        identity = (
            _compact_name(item["score_item_name"]),
            float(item.get("score_weight") or 0.0),
            item.get("fact_type"),
            tuple(_compact_name(str(p)) for p in item.get("group_path") or []),
            # 规则文本参与身份: 同表对称因素(名分同、规则不同)是合法独立行,
            # 不得误删(教训); 真跨页重复行的规则文本逐字相同。
            tuple(_compact_name(str(r)) for r in item.get("score_rules") or []),
        )
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(item)

    name_score_paths: dict[tuple, set[tuple]] = {}
    for item in deduped:
        key = (
            _compact_name(item["score_item_name"]),
            float(item.get("score_weight") or 0.0),
        )
        path = tuple(_compact_name(str(p)) for p in item.get("group_path") or [])
        name_score_paths.setdefault(key, set()).add(path)

    result: list[dict] = []
    for item in deduped:
        path_parts = [str(p) for p in item.get("group_path") or []]
        in_composition_table = any(
            marker in part for part in path_parts for marker in _COMPOSITION_TABLE_MARKERS
        )
        if in_composition_table:
            key = (
                _compact_name(item["score_item_name"]),
                float(item.get("score_weight") or 0.0),
            )
            if len(name_score_paths.get(key, set())) >= 2:
                continue
        result.append(item)
    return result


def effective_items(items: list[dict]) -> list[dict]:
    """事实层同口径后处理: 复用生产命名净化 + 过滤0分base_score行 + 层级去重。

 1. 名称经 scoring_item_naming.strip_scoring_name_decoration 净化
 (单一权威源: 剥离行序号前缀与尾部括号/裸分值, 生产链同口径);
 2. 0分的 base_score 行(符合性/占位行)不计为评分项,
 对齐 normalize_score_weight 的语义(≤0 → 非计分项); penalty/bonus 调整项保留;
 3. 父组行/标准行去重(_drop_duplicate_parent_rows, 对齐红线一)。
 """
    from app.core.extractor.scoring.scoring_item_naming import (
        strip_scoring_name_decoration,
    )

    kept: list[dict] = []
    for item in items:
        if item.get("fact_type") == "base_score" and float(
            item.get("score_weight") or 0.0
        ) <= 0:
            continue
        cleaned = dict(item)
        cleaned["score_item_name"] = strip_scoring_name_decoration(
            item.get("score_item_name") or ""
        )
        kept.append(cleaned)
    return _drop_duplicate_parent_rows(kept)


async def evaluate_sample(
    key: str,
    pdf_path: Path,
    truth_entry: dict,
    corpus_dir: Optional[Path],
    client,
    args,
    models,
) -> dict:
    """单样本: 渲染 → VLM → 指标; 有真值时同步跑确定性基线作对照。

    truth_entry 为空(泛化外部样本)时只做提取与自洽闭合(提取总分 vs 模型读到的声明总分),
    不做真值指标、不触碰确定性链语料。
    """
    truth_items = truth_entry.get("items", [])
    declared_total = truth_entry.get("declared_total", 100.0)
    truth_names = [item["item_name"] for item in truth_items]
    record: dict = {
        "key": key,
        "truth_count": len(truth_items),
        "declared_total": declared_total,
        "no_truth": not truth_items,
        "error": None,
    }

    raw_dir = Path(args.raw_dir)
    raw_file = raw_dir / f"{key}.json"
    try:
        started = time.monotonic()
        if args.rescore_from_raw:
            if not raw_file.exists():
                record["error"] = "raw_missing"
                return record
            saved = json.loads(raw_file.read_text(encoding="utf-8"))
            parsed = saved["parsed"]
            record["page_count"] = saved.get("page_count", 0)
            record["latency_s"] = saved.get("latency_s", 0)
            record["calls"] = saved.get("calls", 0)
            record["parse_failures"] = saved.get("parse_failures", 0)
        else:
            images = await asyncio.to_thread(render_pdf_pages, pdf_path, args.dpi)
            record["page_count"] = len(images)
            if args.dry_run:
                record["error"] = "dry_run"
                return record
            outcome = await call_vlm_for_sample(
                client,
                images,
                max_pages_per_call=args.max_pages_per_call,
                max_tokens=args.max_tokens,
                models=models,
            )
            record["latency_s"] = round(time.monotonic() - started, 1)
            record["calls"] = outcome["calls"]
            record["parse_failures"] = outcome["parse_failures"]
            record["broken_lines"] = outcome.get("broken_lines", 0)

            parsed = outcome["parsed"]
            raw_dir.mkdir(parents=True, exist_ok=True)
            raw_file.write_text(
                json.dumps(
                    {
                        "parsed": parsed,
                        "raw_responses": outcome["raw_responses"],
                        "page_count": record["page_count"],
                        "latency_s": record["latency_s"],
                        "calls": record["calls"],
                        "parse_failures": record["parse_failures"],
                        "broken_lines": record["broken_lines"],
                    },
                    ensure_ascii=False,
                    indent=1,
                ),
                encoding="utf-8",
            )

        items = effective_items(parsed["items"])
        vlm_total = vlm_total_score(items)
        record["extracted_count"] = len(items)
        record["extracted_total"] = round(vlm_total, 2)
        record["vlm_declared"] = parsed.get("declared_total")

        if record["no_truth"]:
            # 泛化外部样本: 自洽闭合 = 提取总分 vs 模型自己读到的声明总分
            if record["vlm_declared"]:
                record["self_gap"] = round(vlm_total - float(record["vlm_declared"]), 2)
                record["self_closed"] = abs(record["self_gap"]) <= 1.0
            return record

        vlm_names = [item["score_item_name"] for item in items]
        vlm_pairs = [
            (item["score_item_name"], float(item.get("score_weight") or 0.0))
            for item in items
        ]
        name_metrics = compute_item_metrics(truth_names, vlm_names)
        pair_metrics = compute_item_pair_metrics(truth_items, vlm_pairs)
        closure = compute_closure_status(vlm_total, declared_total)
        closed_folded = False
        if not closure["closed"]:
            # 原始轴不闭合时检查折算轴: 卷内原始满分×权重是否等于声明总分
            # (真值文档为业务折算读法, 事实层保留原始满分, 两口径均视为闭合)
            folded_item_total = folded_total_from_volumes(
                parsed.get("volume_totals") or []
            )
            if (
                folded_item_total is not None
                and abs(folded_item_total - declared_total) <= 1.0
                and abs(vlm_total - sum(
                    float(vt.get("total") or 0.0)
                    for vt in parsed.get("volume_totals") or []
                )) <= 1.0
            ):
                closed_folded = True
        record.update(
            {
                "name_f1": name_metrics["f1"],
                "pair_precision": pair_metrics["precision"],
                "pair_recall": pair_metrics["recall"],
                "pair_f1": pair_metrics["f1"],
                "pair_tp": pair_metrics["true_positive"],
                "closed": closure["closed"] or closed_folded,
                "closed_folded": closed_folded,
                "gap": closure["gap"],
            }
        )
    except Exception as exc:  # noqa: BLE001 - 逐样本隔离, 单样本失败不中断全量评估
        record["error"] = f"{type(exc).__name__}: {exc}"
        return record

    try:
        det = run_deterministic_extraction_for_sample(key, corpus_dir=corpus_dir)
        det_pair = compute_item_pair_metrics(truth_items, det.get("item_pairs", []))
        det_closure = compute_closure_status(det.get("total", 0.0), declared_total)
        record.update(
            {
                "det_count": det.get("item_count", 0),
                "det_pair_f1": det_pair["f1"],
                "det_closed": det_closure["closed"],
                "det_gap": det_closure["gap"],
            }
        )
    except Exception as exc:  # noqa: BLE001 - 基线失败不影响VLM侧结论
        record["det_error"] = f"{type(exc).__name__}: {exc}"

    return record


# ════════════════════════════════════════════════════════════════════════════
# 报告
# ════════════════════════════════════════════════════════════════════════════


def format_report(results: list[dict], model_desc: str, args) -> str:
    """生成 VLM vs 确定性链对比 Markdown 报告。"""
    from datetime import date

    valid = [r for r in results if not r.get("error")]

    def _avg(field: str) -> float:
        vals = [r[field] for r in valid if field in r]
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    closed = sum(1 for r in valid if r.get("closed"))
    det_valid = [r for r in results if "det_pair_f1" in r]
    det_closed = sum(1 for r in det_valid if r.get("det_closed"))

    lines = [
        "# 评分项提取 VLM 视觉通道 MVP 实测报告",
        "",
        f"**日期**: {date.today()} | **模型**: {model_desc} | "
        f"**dpi**: {args.dpi} | **每批页数**: {args.max_pages_per_call}",
        f"**样本数**: {len(results)}（错误 {len(results) - len(valid)}）",
        "",
        "## 汇总对比（pair口径 = 归一化名称+分值±0.01 二元组匹配）",
        "",
        "| 指标 | VLM视觉通道 | 确定性链基线 |",
        "|---|---:|---:|",
        f"| pair precision (均值) | {_avg('pair_precision'):.4f} | - |",
        f"| pair recall (均值) | {_avg('pair_recall'):.4f} | - |",
        f"| pair F1 (均值) | {_avg('pair_f1'):.4f} | {_avg('det_pair_f1'):.4f} |",
        f"| name F1 (均值) | {_avg('name_f1'):.4f} | - |",
        f"| 闭合样本 | {closed}/{len(valid)} | {det_closed}/{len(det_valid)} |",
        "",
        "## 逐样本对比",
        "",
        "| sample | truth | VLM项数 | VLM pairF1 | VLM gap | VLM闭合 | DET项数 | DET pairF1 | DET gap | DET闭合 | 页数 | 耗时s |",
        "|---|---:|---:|---:|---:|---|---:|---:|---:|---|---:|---:|",
    ]

    for r in results:
        if r.get("error"):
            lines.append(
                f"| {r['key']} | {r['truth_count']} | ERR({r['error'][:40]}) "
                f"| - | - | - | {r.get('det_count', '-')} | "
                f"{r.get('det_pair_f1', 0.0):.3f} | {r.get('det_gap', 0.0):+.1f} "
                f"| {'Yes' if r.get('det_closed') else 'No'} "
                f"| {r.get('page_count', '-')} | - |"
            )
            continue
        vlm_closed = "Yes*" if r.get("closed_folded") else ("Yes" if r["closed"] else "No")
        lines.append(
            f"| {r['key']} | {r['truth_count']} | {r['extracted_count']} "
            f"| {r['pair_f1']:.3f} | {r['gap']:+.1f} "
            f"| {vlm_closed} "
            f"| {r.get('det_count', '-')} | {r.get('det_pair_f1', 0.0):.3f} "
            f"| {r.get('det_gap', 0.0):+.1f} "
            f"| {'Yes' if r.get('det_closed') else 'No'} "
            f"| {r['page_count']} | {r.get('latency_s', 0)} |"
        )

    lines += [
        "",
        "## 说明",
        "",
        "- VLM通道: 样本PDF整页渲染 → vision池VLM → 结构化JSON, 无任何确定性解析参与。",
        "- 确定性链基线: 与 audit_scoring_legacy_baseline 同源(语料fixture重放, 无LLM)。",
        "- closed: |extracted_total - declared_total| <= 1.0 分; 带*为折算轴闭合",
        "  (卷内原始满分×权重=声明总分, 且提取项覆盖各卷原始满分——对齐事实层红线三)。",
        "- 原始模型响应存于 backend/scratch/vlm_mvp/ (不入库、不提交)。",
        "",
    ]
    return "\n".join(lines)


def format_external_report(results: list[dict], model_desc: str, args) -> str:
    """泛化外部样本报告: 无真值, 呈现提取规模与自洽闭合(提取总分 vs 模型读到的声明总分)。"""
    from datetime import date

    valid = [r for r in results if not r.get("error")]
    self_checked = [r for r in valid if "self_closed" in r]
    self_closed = sum(1 for r in self_checked if r["self_closed"])

    lines = [
        "# 评分项提取 VLM 视觉通道泛化实测（外部样本, 无真值）",
        "",
        f"**日期**: {date.today()} | **模型**: {model_desc} | "
        f"**dpi**: {args.dpi} | **每批页数**: {args.max_pages_per_call}",
        f"**样本目录**: {args.samples_dir}",
        f"**样本数**: {len(results)}（错误 {len(results) - len(valid)}）| "
        f"自洽闭合: {self_closed}/{len(self_checked)}（其余样本未声明总分）",
        "",
        "| sample | 项数 | 提取总分 | 声明总分(模型读取) | 自洽gap | 闭合 | 页数 | 耗时s | 断行 |",
        "|---|---:|---:|---:|---:|---|---:|---:|---:|",
    ]
    for r in results:
        if r.get("error"):
            lines.append(f"| {r['key']} | ERR({r['error'][:60]}) | - | - | - | - | - | - | - |")
            continue
        declared = r.get("vlm_declared")
        self_gap = r.get("self_gap")
        lines.append(
            f"| {r['key']} | {r['extracted_count']} | {r['extracted_total']:.1f} "
            f"| {declared if declared is not None else '-'} "
            f"| {f'{self_gap:+.1f}' if self_gap is not None else '-'} "
            f"| {('Yes' if r.get('self_closed') else 'No') if self_gap is not None else '-'} "
            f"| {r['page_count']} | {r.get('latency_s', 0)} | {r.get('broken_lines', 0)} |"
        )

    lines += [
        "",
        "## 说明",
        "",
        "- 外部样本无机器可读真值; 自洽闭合仅验证 VLM 输出的内部一致性,",
        "  不等价于正确性——正确性由人工抽样读图核对补充。",
        "- 本次评估未运行确定性链, 不触碰冻结验证集语料与真值。",
        "",
    ]
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════════════
# 入口
# ════════════════════════════════════════════════════════════════════════════


def build_arg_parser() -> argparse.ArgumentParser:
    argp = argparse.ArgumentParser(description="评分项VLM视觉通道MVP评估")
    argp.add_argument(
        "--samples-dir",
        required=True,
        help="外部评估 PDF 目录（必填；不得提交客户样本到仓库）",
    )
    argp.add_argument("--indexes", default="", help="样本编号如 1,5,9; 缺省为训练集全量")
    argp.add_argument("--include-downgraded-holdout", action="store_true")
    argp.add_argument(
        "--external",
        action="store_true",
        help="外部泛化模式: 不查真值、不跑确定性链, 按 --name-template 扫描样本目录",
    )
    argp.add_argument(
        "--name-template",
        default=PDF_NAME_TEMPLATE,
        help="PDF文件名模板, 如 验证评分表结构-{index}.pdf",
    )
    argp.add_argument("--config-id", default="", help="固定vision配置ID; 缺省用vision池调度器")
    argp.add_argument("--dpi", type=int, default=200)
    argp.add_argument("--max-pages-per-call", type=int, default=8)
    argp.add_argument("--max-tokens", type=int, default=6000)
    argp.add_argument("--concurrency", type=int, default=2)
    argp.add_argument("--limit", type=int, default=0, help="仅评估前N个样本(冒烟用)")
    argp.add_argument("--dry-run", action="store_true", help="只渲染不调LLM")
    argp.add_argument(
        "--rescore-from-raw",
        action="store_true",
        help="从已存raw响应离线重算指标, 不调LLM(用于迭代后处理规则)",
    )
    argp.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR))
    argp.add_argument("--report", default=str(DEFAULT_REPORT_PATH))
    return argp


def resolve_sample_specs(args) -> list[tuple[str, Path, dict, Optional[Path]]]:
    """构建 (key, pdf_path, truth_entry, corpus_dir) 清单。"""
    samples_dir = Path(args.samples_dir).expanduser().resolve()
    if not samples_dir.is_dir():
        raise SystemExit(f"samples directory does not exist: {samples_dir}")

    if args.external:
        # 外部泛化模式: 按模板扫描目录, 无真值、无确定性链语料
        specs_ext: list[tuple[str, Path, dict, Optional[Path]]] = []
        if args.indexes:
            indexes = [int(token) for token in args.indexes.split(",") if token.strip()]
        else:
            indexes = sorted(
                int(match.group(1))
                for pdf in samples_dir.glob(args.name_template.replace("{index}", "*"))
                if (match := re.search(r"-(\d+)\.pdf$", pdf.name))
            )
        for index in indexes:
            pdf_path = samples_dir / args.name_template.format(index=index)
            if not pdf_path.exists():
                print(f"SKIP {index}: {pdf_path} 不存在")
                continue
            specs_ext.append((f"{index:02d}", pdf_path, {}, None))
        if args.limit > 0:
            specs_ext = specs_ext[: args.limit]
        return specs_ext

    truth_data = parse_truth_markdown(TRUTH_PATH.read_text(encoding="utf-8"))

    specs: list[tuple[str, Path, dict, Optional[Path]]] = []
    keys = list(TRAINING_KEYS)
    if args.indexes:
        keys = [token.strip().zfill(2) for token in args.indexes.split(",") if token.strip()]

    downgraded_truth: dict = {}
    if args.include_downgraded_holdout or any(k in DOWNGRADED_HOLDOUT_KEYS for k in keys):
        downgraded_truth = load_downgraded_holdout_truth()
    if args.include_downgraded_holdout:
        keys.extend(k for k in DOWNGRADED_HOLDOUT_KEYS if k not in keys)

    for key in keys:
        pdf_path = samples_dir / args.name_template.format(index=int(key))
        if not pdf_path.exists():
            print(f"SKIP {key}: {pdf_path} 不存在")
            continue
        if key in DOWNGRADED_HOLDOUT_KEYS:
            truth_entry = downgraded_truth.get(key, {})
            corpus_dir = DOWNGRADED_HOLDOUT_CORPUS_DIR
        else:
            truth_entry = truth_data.get(key, {})
            corpus_dir = CORPUS_DIR
        if not truth_entry.get("items"):
            print(f"SKIP {key}: 无真值条目")
            continue
        specs.append((key, pdf_path, truth_entry, corpus_dir))

    if args.limit > 0:
        specs = specs[: args.limit]
    return specs


async def main_async(args) -> int:
    _ensure_test_env()

    from app.core.observability.traffic_class import (
        TRAFFIC_CLASS_ADMIN_TEST,
        current_traffic_class,
    )

    current_traffic_class.set(TRAFFIC_CLASS_ADMIN_TEST)

    from app.core.ai.ai_factory import get_client_by_config_id, get_vision_client

    if args.config_id:
        client = get_client_by_config_id(args.config_id, purpose="vision")
        model_desc = f"pinned:{args.config_id} ({getattr(client, 'model_name', '?')})"
    else:
        client = get_vision_client()
        model_desc = "vision池调度器(按优先级failover)"

    # 外部样本与训练集样本编号可能重叠(如都有01), raw目录默认隔离防覆盖
    if args.external and args.raw_dir == str(DEFAULT_RAW_DIR):
        args.raw_dir = str(DEFAULT_RAW_DIR) + "_external"

    specs = resolve_sample_specs(args)
    if not specs:
        print("无可评估样本")
        return 1
    print(f"[mvp] 模型: {model_desc}; 样本数: {len(specs)}; dpi={args.dpi}")

    models = _build_models()
    semaphore = asyncio.Semaphore(max(1, args.concurrency))

    async def _guarded(spec):
        key, pdf_path, truth_entry, corpus_dir = spec
        async with semaphore:
            record = await evaluate_sample(
                key, pdf_path, truth_entry, corpus_dir, client, args, models
            )
            if record.get("error"):
                status = record["error"]
            elif record.get("no_truth"):
                status = (
                    f"items={record.get('extracted_count', 0)} "
                    f"total={record.get('extracted_total', 0)} "
                    f"self_gap={record.get('self_gap', 'n/a')}"
                )
            else:
                status = (
                    f"pairF1={record.get('pair_f1', 0):.3f} gap={record.get('gap', 0):+.1f}"
                )
            print(f"[mvp] {key}: {status}")
            return record

    results = await asyncio.gather(*[_guarded(spec) for spec in specs])
    results = sorted(results, key=lambda r: r["key"])

    formatter = format_external_report if args.external else format_report
    report = formatter(results, model_desc, args)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(f"[mvp] 报告已写入: {report_path}")

    valid = [r for r in results if not r.get("error")]
    if valid and not args.external:
        avg_pair_f1 = sum(r["pair_f1"] for r in valid) / len(valid)
        closed = sum(1 for r in valid if r["closed"])
        print(f"[mvp] VLM pairF1均值={avg_pair_f1:.3f}, 闭合={closed}/{len(valid)}")
    return 0


def main() -> int:
    args = build_arg_parser().parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
