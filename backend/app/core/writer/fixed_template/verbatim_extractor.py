"""固定模板布局保真提取（verbatim 通道）。

职责: 按页码范围从招标 PDF 词级坐标重组视觉行, 输出布局保真的模板原文,
 并提供把保真文本写入 fixed_template 快照的增补入口。
不做: 不切块、不做语义改写、不猜测编号归属、不调用 LLM、不访问数据库。

设计要点:
- 行重组用"两遍法": 先按严格中心距把词聚成基础行(真实文本行之间永不合并),
 再把纯编号碎片行(如 ``1 .`` / ``10`` / ``60``)按最大垂直重叠归入宿主行。
 招标模板的条目编号常以半行垂直偏移渲染, pdfplumber 默认 y_tolerance 会把
 编号拆离宿主行(根因); 编号碎片与宿主行存在正向重叠, 按重叠回拼
 可无损还原视觉行, 且不会把相邻真实文本行链式串联。
- 表格区域用 ``page.find_tables`` 网格重建 <table> HTML, 区域内词不进入行流。
- 仅做零风险的行内前缀归一(``1） .`` -> ``1）``), 其余内容一律原样保留。
- 降噪复用 core/parser 公共策略(不自建启发式): 水印字符经 ``watermark_policy``
 字符级预过滤(同源), 页眉页脚经 ``page_chrome_policy`` 的跨页重复
 文本签名做行级剔除(数字不敏感, "第N页"类页脚可命中); 学习失败一律降级为
 不过滤, 保持本通道"任何失败只降级不阻断"契约。
"""

from __future__ import annotations

import re
from typing import Any, Optional

from loguru import logger

from app.core.parser.page_chrome_policy import (
    learn_page_chrome,
    normalize_text_signature,
)
from app.core.parser.watermark_policy import (
    WatermarkPolicy,
    learn_watermark_policy,
)
from app.core.writer.fixed_template.features import (
    extract_field_slots,
    extract_signature_slots,
)
from app.core.writer.fixed_template.titles import (
    infer_fixed_template_key,
    normalize_template_title,
)
from app.core.writer.numbering import strip_existing_number

_VERBATIM_SOURCE_TAG = "layout_lines"
_VERBATIM_MAX_CHARS = 12000
_LINE_OVERLAP_MIN_PT = 1.5
_HEADER_BAND_RATIO = 0.15
_FOOTER_BAND_RATIO = 0.85
_CHROME_SAMPLE_PAGES = 12
_CHROME_REPEAT_MIN = 2
_MIN_TEMPLATE_CHARS = 30

# 半行偏移编号回拼后产生的前缀碎片, 仅做零风险归一:
#   "1 ） ." / "1 ）" / "1)" -> "1）"
_STUB_PREFIX_RE = re.compile(r"^(\d{1,2})\s*[）)]\s*[.．]?\s*")
# "1 . 技术标" -> "1. 技术标"（编号与点号被拆词后回拼的空格）
_DOT_STUB_PREFIX_RE = re.compile(r"^(\d{1,2})\s+[.．]\s*")
_PAGE_NUMBER_RE = re.compile(r"^\d{1,4}$")
_NUMBERED_HEADING_RE = re.compile(r"^第?[一二三四五六七八九十百0-9]+\s*[、.．]")
_SENTENCE_TAIL_CHARS = ("。", "；", ";", "，", ",", "：", ":", "、")


class VerbatimTemplateSource:
    """打开一份招标 PDF, 按页码范围提供布局保真的模板原文。"""

    def __init__(self, pdf_path: str) -> None:
        import pdfplumber

        self._pdf = pdfplumber.open(pdf_path)
        self._page_cache: dict[int, list[tuple[float, str]]] = {}
        self._watermark_policy, self._chrome_signatures = self._learn_noise_policies()

    def close(self) -> None:
        try:
            self._pdf.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> "VerbatimTemplateSource":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ---------------- 公共入口 ----------------

    def extract_template_text(
        self,
        *,
        title: str,
        page_start: int,
        page_end: int,
        template_key: Optional[str] = None,
    ) -> Optional[str]:
        """提取 [page_start, page_end] 内以 title 为起点的模板原文。

        起点: 首个命中模板标题的视觉行(该行本身不进入输出, 标题由章节节点渲染);
        终点: 下一个"编号 + 其它已知模板标题"行, 或范围末尾。
        未找到标题行时返回整段范围行流(去页眉页脚), 交由下游标题去重兜底。
        """
        lines = self.extract_range_lines(page_start, page_end)
        if not lines:
            return None
        key = template_key or infer_fixed_template_key(title)
        start_index = _find_title_line_index(lines, title, key)
        title_ordinal = (
            _cn_ordinal_value(lines[start_index]) if start_index is not None else None
        )
        body = lines[start_index + 1:] if start_index is not None else lines
        end_index = _find_next_template_heading(body, key, title_ordinal=title_ordinal)
        if end_index is not None:
            body = body[:end_index]
        text = "\n".join(body).strip()
        if len(text) < _MIN_TEMPLATE_CHARS:
            return None
        if len(text) > _VERBATIM_MAX_CHARS:
            text = text[:_VERBATIM_MAX_CHARS].rstrip()
        return text

    def extract_range_lines(self, page_start: int, page_end: int) -> list[str]:
        """返回页码范围内的视觉行(含 <table> HTML 块), 已剔除页眉页脚页码。"""
        total = len(self._pdf.pages)
        result: list[str] = []
        for page_no in range(max(1, page_start), min(total, page_end) + 1):
            for _top, text in self._page_items(page_no):
                result.append(text)
        return result

    # ---------------- 页级解析 ----------------

    def _page_items(self, page_no: int) -> list[tuple[float, str]]:
        if page_no in self._page_cache:
            return self._page_cache[page_no]
        page = self._pdf.pages[page_no - 1]
        height = float(page.height or 0) or 1.0
        # 水印字符级预过滤(无规则时原页短路返回), 后续表格/词提取均基于净化页
        page = self._watermark_policy.filter_page(page)

        table_items: list[tuple[float, str]] = []
        table_bboxes: list[tuple[float, float, float, float]] = []
        try:
            for table in page.find_tables() or []:
                html = _table_to_html(table)
                if html:
                    table_bboxes.append(tuple(table.bbox))
                    table_items.append((float(table.bbox[1]), html))
        except Exception as exc:  # noqa: BLE001
            logger.debug("verbatim 表格提取失败, 降级为行流: page={}, err={}", page_no, exc)

        words = [
            w for w in (page.extract_words() or [])
            if not _word_in_bboxes(w, table_bboxes)
        ]
        line_items: list[tuple[float, str]] = []
        for row in _cluster_words_by_overlap(words):
            top = min(float(w["top"]) for w in row)
            bottom = max(float(w["bottom"]) for w in row)
            text = _render_line_text(row)
            if not text:
                continue
            if self._is_chrome_line(text, top, bottom, height):
                continue
            line_items.append((top, text))

        items = sorted(line_items + table_items, key=lambda item: item[0])
        self._page_cache[page_no] = items
        return items

    def _is_chrome_line(
        self, text: str, top: float, bottom: float, page_height: float,
    ) -> bool:
        compact = re.sub(r"\s+", "", text)
        if not compact:
            return True
        in_footer_band = bottom >= page_height * _FOOTER_BAND_RATIO
        if in_footer_band and _PAGE_NUMBER_RE.fullmatch(compact):
            return True
        if not self._chrome_signatures:
            return False
        in_header_band = top <= page_height * _HEADER_BAND_RATIO
        if not (in_header_band or in_footer_band):
            return False
        return normalize_text_signature(text) in self._chrome_signatures

    def _learn_noise_policies(self) -> tuple[WatermarkPolicy, set[str]]:
        """采样学习公共降噪策略: 水印字符组(页无关) + 页眉页脚文本签名集合。

 复用 core/parser 公共实现(的防误删保护已内建):
 - 水印按"字体+字号+颜色"属性组判定, 与页码无关, 采样学习可泛化到全文档;
 - 页眉页脚取跨页重复文本签名(数字归一)做行级剔除, 阈值/区带与本通道
 历史行为对齐(重复>=2 页、顶/底 15% 区带);
 任何学习失败都降级为"不过滤", 保持零阻断契约。
 """
        sampled = self._sample_pages()
        if len(sampled) < 2:
            return WatermarkPolicy(), set()

        watermark_policy = WatermarkPolicy()
        try:
            watermark_policy = learn_watermark_policy(sampled)
        except Exception as exc:  # noqa: BLE001
            logger.warning("verbatim 水印策略学习失败(降级不过滤): {}", exc)

        signatures: set[str] = set()
        try:
            chrome = learn_page_chrome(
                sampled,
                min_repeat_pages=_CHROME_REPEAT_MIN,
                repeat_ratio=0.0,
                candidate_band_ratio=_HEADER_BAND_RATIO,
            )
            for decision in chrome.decisions.values():
                signatures |= decision.chrome_text_signatures
        except Exception as exc:  # noqa: BLE001
            logger.warning("verbatim 页眉页脚策略学习失败(降级不过滤): {}", exc)
        return watermark_policy, signatures

    def _sample_pages(self) -> list[Any]:
        """步进采样至多约 _CHROME_SAMPLE_PAGES 页, 控制策略学习开销。"""
        pages = self._pdf.pages
        if len(pages) <= _CHROME_SAMPLE_PAGES:
            return list(pages)
        step = max(1, len(pages) // _CHROME_SAMPLE_PAGES)
        return [pages[index] for index in range(0, len(pages), step)]


class SnapshotVerbatimEnricher:
    """把布局保真文本增补进 fixed_template 快照, 供约束保存链路调用。"""

    def __init__(self, pdf_path: Optional[str]) -> None:
        self._pdf_path = pdf_path
        self._source: Optional[VerbatimTemplateSource] = None
        self._failed = False

    def enrich_snapshot(self, metadata_json: Optional[dict[str, Any]]) -> bool:
        """就地为 metadata_json 中的快照补写 raw_text_verbatim, 返回是否写入。

        任何失败(PDF 缺失/解析异常/定界失败)都只记日志不抛出,
        保持旧链路行为不变。
        """
        if not isinstance(metadata_json, dict):
            return False
        snapshot = metadata_json.get("fixed_template_snapshot")
        if not isinstance(snapshot, dict):
            return False
        page_start = snapshot.get("source_page_start")
        page_end = snapshot.get("source_page_end")
        title = str(snapshot.get("title") or "").strip()
        if not title or page_start is None:
            return False
        source = self._open_source()
        if source is None:
            return False
        try:
            verbatim = source.extract_template_text(
                title=title,
                page_start=int(page_start),
                page_end=int(page_end if page_end is not None else page_start),
                template_key=snapshot.get("template_key"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "fixed_template verbatim 提取失败: title={!r}, page={}-{}, err={}",
                title, page_start, page_end, exc,
            )
            return False
        if not verbatim:
            return False
        snapshot["raw_text_verbatim"] = verbatim
        snapshot["verbatim_source"] = _VERBATIM_SOURCE_TAG
        snapshot["field_slots"] = list(extract_field_slots(verbatim))
        snapshot["signature_slots"] = list(extract_signature_slots(verbatim))
        if "<table" in verbatim:
            snapshot["has_table"] = True
        return True

    def close(self) -> None:
        if self._source is not None:
            self._source.close()
            self._source = None

    def _open_source(self) -> Optional[VerbatimTemplateSource]:
        if self._failed or not self._pdf_path:
            return None
        if self._source is None:
            try:
                self._source = VerbatimTemplateSource(self._pdf_path)
            except Exception as exc:  # noqa: BLE001
                self._failed = True
                logger.warning(
                    "fixed_template verbatim PDF 打开失败, 降级 chunk 链路: path={}, err={}",
                    self._pdf_path, exc,
                )
                return None
        return self._source


# ---------------- 纯函数: 行聚类与文本重组 ----------------

_BASE_LINE_CENTER_TOLERANCE = 3.0
# 纯编号/标点碎片行: 数字、中文数字、货币、括号、顿号点号等, 无正文汉字
_FRAGMENT_TEXT_RE = re.compile(
    r"^[0-9零一二三四五六七八九十百%％¥￥、.．,，:：()_\-\s]{1,12}$"
)


def _cluster_words_by_overlap(words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """两遍法把词聚成视觉行。

    第一遍按严格中心距聚基础行, 保证相邻真实文本行永不合并;
    第二遍把纯编号/标点碎片行(半行偏移渲染的 ``1 .`` / ``10`` / ``60`` 等)
    按最大垂直重叠归入宿主行, 还原编号与正文的原始行归属,
    且不会经碎片"桥接"把两条真实文本行串联。
    """
    base_rows: list[list[dict[str, Any]]] = []
    for word in sorted(words, key=lambda w: (float(w["top"]), float(w["x0"]))):
        center = (float(word["top"]) + float(word["bottom"])) / 2
        placed = False
        for row in base_rows:
            row_center = sum(
                (float(w["top"]) + float(w["bottom"])) / 2 for w in row
            ) / len(row)
            if abs(center - row_center) <= _BASE_LINE_CENTER_TOLERANCE:
                row.append(word)
                placed = True
                break
        if not placed:
            base_rows.append([word])

    hosts: list[dict[str, Any]] = []
    fragments: list[dict[str, Any]] = []
    for row in base_rows:
        text = "".join(str(w["text"]) for w in row)
        item = {
            "words": row,
            "top": min(float(w["top"]) for w in row),
            "bottom": max(float(w["bottom"]) for w in row),
        }
        if _is_fragment_text(re.sub(r"\s+", "", text)):
            fragments.append(item)
        else:
            hosts.append(item)

    for fragment in fragments:
        best_index, best_rank = None, None
        for index, host in enumerate(hosts):
            overlap = min(fragment["bottom"], host["bottom"]) - max(
                fragment["top"], host["top"],
            )
            if overlap < _LINE_OVERLAP_MIN_PT:
                continue
            # 水平槽位适配优先: 碎片(填空值/编号)在原版式中落在宿主行的
            # 空隙里; 与宿主行既有词水平相撞的候选行大概率不是真宿主。
            collision = _has_horizontal_collision(fragment["words"], host["words"])
            rank = (1 if collision else 0, -overlap)
            if best_rank is None or rank < best_rank:
                best_index, best_rank = index, rank
        if best_index is not None:
            hosts[best_index]["words"].extend(fragment["words"])
        else:
            hosts.append(fragment)

    hosts.sort(key=lambda item: item["top"])
    return [item["words"] for item in hosts]


def _is_fragment_text(compact: str) -> bool:
    """判断整行是否为编号/填空值碎片(无正文汉字, 或单个数字形字母)。"""
    if not compact:
        return False
    if _FRAGMENT_TEXT_RE.fullmatch(compact):
        return True
    # 数字形字母碎片: 源文档误植的 l/I/O 等单字符(如 "（l）" 的 l)
    return len(compact) == 1 and compact.isascii() and compact.isalnum()


def _has_horizontal_collision(
    fragment_words: list[dict[str, Any]],
    host_words: list[dict[str, Any]],
) -> bool:
    for frag in fragment_words:
        f_x0 = float(frag["x0"]) + 0.5
        f_x1 = float(frag["x1"]) - 0.5
        for host in host_words:
            if f_x0 < float(host["x1"]) and float(host["x0"]) < f_x1:
                return True
    return False


def _render_line_text(row: list[dict[str, Any]]) -> str:
    """行内按 x 排序拼接, 词间保留单空格, 再做零风险前缀归一。"""
    ordered = sorted(row, key=lambda w: float(w["x0"]))
    text = " ".join(str(w["text"]) for w in ordered).strip()
    text = re.sub(r"\s+", " ", text)
    text = _STUB_PREFIX_RE.sub(r"\1）", text)
    text = _DOT_STUB_PREFIX_RE.sub(r"\1. ", text)
    return text.strip()


def _word_in_bboxes(
    word: dict[str, Any],
    bboxes: list[tuple[float, float, float, float]],
) -> bool:
    if not bboxes:
        return False
    cx = (float(word["x0"]) + float(word["x1"])) / 2
    cy = (float(word["top"]) + float(word["bottom"])) / 2
    return any(
        x0 <= cx <= x1 and top <= cy <= bottom
        for x0, top, x1, bottom in bboxes
    )


_EDGE_CLUSTER_TOLERANCE = 2.0


def _table_to_html(table: Any) -> Optional[str]:
    """把 pdfplumber Table 转成带 rowspan/colspan 的保真 <table> HTML。: ``table.extract`` 密集网格会把合并单元格摊成大量空格,
 结构严重失真。此处按物理单元格 bbox 与网格边线重建跨度:
 每个物理 cell 输出一个 <td>, 其 rowspan/colspan 由覆盖的网格行列数决定,
 文本取密集网格中该 cell 左上角位置的值。
 """
    from html import escape

    try:
        cells = [tuple(float(v) for v in cell) for cell in (table.cells or [])]
        dense = table.extract() or []
    except Exception:  # noqa: BLE001
        return None
    if not cells or not dense:
        return None
    xs = _cluster_edges([c[0] for c in cells] + [c[2] for c in cells])
    ys = _cluster_edges([c[1] for c in cells] + [c[3] for c in cells])
    n_cols, n_rows = len(xs) - 1, len(ys) - 1
    if n_cols < 2 or n_rows < 1:
        return None

    placed: dict[int, list[tuple[int, int, int, str]]] = {}
    for x0, top, x1, bottom in cells:
        c0, c1 = _edge_index(xs, x0), _edge_index(xs, x1)
        r0, r1 = _edge_index(ys, top), _edge_index(ys, bottom)
        if None in (c0, c1, r0, r1) or c1 <= c0 or r1 <= r0:
            continue
        text = ""
        if r0 < len(dense) and c0 < len(dense[r0] or []):
            text = re.sub(
                r"\s+", " ", str(dense[r0][c0] or "").replace("\n", " ")
            ).strip()
        placed.setdefault(r0, []).append((c0, r1 - r0, c1 - c0, text))
    if not placed:
        return None

    parts = ["<table>"]
    for row_index in range(n_rows):
        row_cells = sorted(placed.get(row_index, []))
        cells_html = []
        for c0, rowspan, colspan, text in row_cells:
            attrs = ""
            if rowspan > 1:
                attrs += f' rowspan="{rowspan}"'
            if colspan > 1:
                attrs += f' colspan="{colspan}"'
            cells_html.append(f"<td{attrs}>{escape(text)}</td>")
        parts.append("<tr>" + "".join(cells_html) + "</tr>")
    parts.append("</table>")
    return "".join(parts)


def _cluster_edges(values: list[float]) -> list[float]:
    """把近似重合的网格边线坐标聚为单一边线, 返回升序边线列表。"""
    result: list[float] = []
    for value in sorted(values):
        if result and value - result[-1] <= _EDGE_CLUSTER_TOLERANCE:
            continue
        result.append(value)
    return result


def _edge_index(edges: list[float], value: float) -> Optional[int]:
    """返回坐标对应的边线下标(容差内最近), 无匹配返回 None。"""
    best_index, best_distance = None, _EDGE_CLUSTER_TOLERANCE + 0.1
    for index, edge in enumerate(edges):
        distance = abs(edge - value)
        if distance < best_distance:
            best_index, best_distance = index, distance
    return best_index


# ---------------- 纯函数: 模板定界 ----------------


def _find_title_line_index(
    lines: list[str], title: str, template_key: Optional[str],
) -> Optional[int]:
    """返回首个命中模板标题的行下标, 未命中返回 None。"""
    norm_title = normalize_template_title(title)
    if not norm_title:
        return None
    for index, line in enumerate(lines):
        if line.startswith("<table"):
            continue
        stripped = strip_existing_number(line)
        norm_line = normalize_template_title(stripped)
        if not norm_line or len(norm_line) > 30:
            continue
        if norm_line == norm_title or norm_line in norm_title or norm_title in norm_line:
            return index
        if template_key and infer_fixed_template_key(stripped) == template_key:
            return index
    return None


_CN_ORDINAL_MAP = {ch: idx + 1 for idx, ch in enumerate("一二三四五六七八九")}
_CN_ORDINAL_PREFIX_RE = re.compile(r"^([一二三四五六七八九十]{1,3})、")


def _cn_ordinal_value(line: str) -> Optional[int]:
    """解析行首中文序号("三、xxx" -> 3), 无序号返回 None。"""
    compact = re.sub(r"\s+", "", line or "")
    match = _CN_ORDINAL_PREFIX_RE.match(compact)
    if not match:
        return None
    token = match.group(1)
    if token == "十":
        return 10
    if len(token) == 1:
        return _CN_ORDINAL_MAP.get(token)
    if token.startswith("十") and len(token) == 2:
        return 10 + (_CN_ORDINAL_MAP.get(token[1]) or 0)
    if token.endswith("十") and len(token) == 2:
        return (_CN_ORDINAL_MAP.get(token[0]) or 0) * 10
    return None


def _find_next_template_heading(
    body: list[str],
    current_key: Optional[str],
    *,
    title_ordinal: Optional[int] = None,
) -> Optional[int]:
    """在正文行中找下一个模板/小节标题行, 作为终点边界。

    两级判定:
    1. "编号 + 其它已知模板标题"(词典命中, 最可靠);
    2. 中文序号连号短标题(如起点"二、开标一览表" -> 终点"三、开户许可证"):
       投标文件格式章节各附件按中文序号排列, 仅当行首序号恰为起点序号 +1
       且为无句读短行时判定为下一小节, 避免误切模板内部的中文序号列表。
    """
    for index, line in enumerate(body):
        if line.startswith("<table"):
            continue
        compact = re.sub(r"\s+", "", line)
        if len(compact) > 30:
            continue
        if _NUMBERED_HEADING_RE.match(compact):
            inferred = infer_fixed_template_key(strip_existing_number(line))
            if inferred and inferred != current_key:
                return index
        if (
            title_ordinal is not None
            and not compact.endswith(_SENTENCE_TAIL_CHARS)
            and _cn_ordinal_value(compact) == title_ordinal + 1
        ):
            return index
    return None


def enrich_snapshots_verbatim(
    pdf_path: Optional[str],
    metadata_list: list[dict[str, Any]],
) -> int:
    """批量为 fixed_template 快照增补保真文本, 返回成功条数。

    同步阻塞函数(内部走 pdfplumber), 调用方应在线程池中执行。
    """
    if not metadata_list:
        return 0
    enricher = SnapshotVerbatimEnricher(pdf_path)
    try:
        return sum(
            1 for metadata in metadata_list
            if enricher.enrich_snapshot(metadata)
        )
    finally:
        enricher.close()


__all__ = [
    "SnapshotVerbatimEnricher",
    "VerbatimTemplateSource",
    "enrich_snapshots_verbatim",
]
