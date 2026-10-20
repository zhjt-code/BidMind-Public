"""
职责: Q5 重复检查——归一化哈希精确层 + 长度分桶近重复层
不做: 不做语义重复、不判定重复是否违规(输出疑似冗余)、段落层不比表格行(表头与续表天然重复)
"""

from __future__ import annotations

from rapidfuzz import fuzz

from app.core.document_qa.author_spans import (
    GridRow,
    grid_rows,
    outline_nodes,
    prose_spans,
    role_page_texts,
)
from app.core.document_qa.contracts import (
    CheckerOutcome,
    CheckerStatus,
    DocumentModel,
    Evidence,
    EvidenceItem,
    Finding,
    QaCategory,
    QaCertainty,
    QaSeverity,
    normalize_for_hash,
    paragraph_hash,
)

CODE = "duplicate"

_MIN_PARAGRAPH_CHARS = 60   # 归一化后长度阈值(短文本/表头/固定用语天然重复)
_MIN_PAGE_CHARS = 200
_CHROME_PAGE_RATIO = 0.3    # 出现页数占比过高视为漏网页眉页脚类内容, 不报
_MAX_FINDINGS = 12
_AGGREGATE_MIN_HITS = 5     # 重复次数达此阈值的段落归并为一条聚合发现(填充/模板段降噪)
_NEAR_RATIO = 92            # rapidfuzz.ratio 阈值(非精确相同)
_NEAR_LEN_RATIO = 0.2       # 长度分桶: ±20%
_NEAR_BUCKET_CAP = 80       # ponytail: 桶内 pairwise 上限(O(n²) 截断); 升级 MinHash/倒排


def _row_is_multi_field(row: GridRow) -> bool:
    """单行且没有空白分隔, 只是一个单元格或半句, 不是整行数据重复。"""
    if len(row.lines) >= 2:
        return True
    return any(char.isspace() for char in row.text)


def _row_bbox(row: GridRow):
    boxes = [line.bbox for line in row.lines]
    if not boxes:
        return None
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _row_context(rows: list[GridRow], index: int) -> str:
    parts: list[str] = []
    if index > 0:
        parts.append("上一行：" + rows[index - 1].text[:80])
    parts.append("本行：" + rows[index].text[:120])
    if index + 1 < len(rows):
        parts.append("下一行：" + rows[index + 1].text[:80])
    return " ".join(parts)


def _duplicate_row_findings(model: DocumentModel, *, remaining: int) -> list[Finding]:
    """同一页表格内, 多字段数据行完全相同才报。续表表头不在本函数的跨页范围。"""
    if remaining <= 0:
        return []
    findings: list[Finding] = []
    grouped: dict[tuple, list[GridRow]] = {}
    order: list[tuple] = []
    for row in grid_rows(model):
        if row.table_id not in grouped:
            order.append(row.table_id)
            grouped[row.table_id] = []
        grouped[row.table_id].append(row)
    previous_header = ""
    previous_page: int | None = None
    for table_id in order:
        rows = grouped[table_id]
        page_num = table_id[0]
        header = normalize_for_hash(rows[0].text) if rows else ""
        continued = previous_page is not None and page_num == previous_page + 1 and header == previous_header
        previous_header = header
        previous_page = page_num
        seen: dict[str, int] = {}
        reported: set[str] = set()
        for index, row in enumerate(rows):
            if continued and index == 0:
                continue
            if not _row_is_multi_field(row):
                continue
            digest = normalize_for_hash(row.text)
            if len(digest) < 4 or index == 0:
                continue
            if digest not in seen:
                seen[digest] = index
                continue
            if digest in reported:
                continue
            reported.add(digest)
            first = seen[digest]
            findings.append(Finding(
                rule_code="DOC-DUP-005",
                category=QaCategory.DUPLICATION,
                severity=QaSeverity.P2,
                certainty=QaCertainty.POSSIBLE,
                title="表内数据行重复",
                message=f"第{page_num}页表格中有内容相同的数据行",
                suggestion="对照上一行和下一行, 确认是否为误复制",
                evidence=Evidence(
                    kind="pair",
                    items=(
                        EvidenceItem(
                            excerpt=_row_context(rows, first),
                            page=page_num,
                            role="primary",
                            bbox=_row_bbox(rows[first]),
                        ),
                        EvidenceItem(
                            excerpt=_row_context(rows, index),
                            page=page_num,
                            role="comparison",
                            bbox=_row_bbox(row),
                        ),
                    ),
                ),
                anchor=f"dup_row:{page_num}:{digest[:16]}",
                closed_measurement=False,
            ))
            if len(findings) >= remaining:
                return findings
    return findings


def _repeated_heading_findings(
    model: DocumentModel,
    rules: dict,
    page_hashes: dict[str, list[int]],
    *,
    remaining: int,
) -> list[Finding]:
    """同一大纲标题出现在两页。整页已经相同的页不再另报标题重复。"""
    if remaining <= 0:
        return []
    copied_pages: set[int] = set()
    for pages in page_hashes.values():
        if len(pages) >= 2:
            copied_pages.update(pages)
    grouped: dict[str, list] = {}
    for heading in outline_nodes(model, rules):
        key = normalize_for_hash(heading.display)
        if len(key) < 4:
            continue
        grouped.setdefault(key, []).append(heading)
    findings: list[Finding] = []
    for key, headings in grouped.items():
        pages = [item.page for item in headings]
        if len(set(pages)) < 2:
            continue
        if set(pages) <= copied_pages:
            continue
        first, second = headings[0], headings[1]
        findings.append(Finding(
            rule_code="DOC-DUP-006",
            category=QaCategory.DUPLICATION,
            severity=QaSeverity.P2,
            certainty=QaCertainty.PROBABLE,
            title="章节标题重复",
            message=f"标题「{first.display}」在第{first.page}页和第{second.page}页重复出现",
            suggestion="删除误复制的标题, 或改成不同的小节名",
            evidence=Evidence(
                kind="pair",
                items=(
                    EvidenceItem(excerpt=first.display, page=first.page, bbox=first.bbox, role="primary"),
                    EvidenceItem(
                        excerpt=second.display, page=second.page, bbox=second.bbox, role="comparison",
                    ),
                ),
            ),
            anchor=f"dup_heading:{key[:24]}",
            closed_measurement=True,
        ))
        if len(findings) >= remaining:
            break
    return findings


def check_duplicate(model: DocumentModel, rules: dict) -> CheckerOutcome:
    findings: list[Finding] = []
    total_pages = max(1, len(model.pages))
    paragraphs: dict[int, list[str]] = {}
    for span in prose_spans(model):
        paragraphs.setdefault(span.page, []).append(span.text)

    # 1) 段落级精确重复
    occurrences: dict[str, list[tuple[int, str]]] = {}
    for page_num in sorted(paragraphs):
        for paragraph in paragraphs[page_num]:
            normalized = normalize_for_hash(paragraph)
            if len(normalized) < _MIN_PARAGRAPH_CHARS:
                continue
            occurrences.setdefault(paragraph_hash(paragraph), []).append(
                (page_num, paragraph)
            )

    # 高频复用段(>=阈值次): 聚合为一条发现, 防模板/填充段落刷屏淹没真埋点
    frequent_groups: list[tuple[str, list[tuple[int, str]]]] = []

    for digest, hits in occurrences.items():
        if len(hits) < 2:
            continue
        pages_hit = {page for page, _ in hits}
        if len(pages_hit) >= total_pages * _CHROME_PAGE_RATIO and len(pages_hit) >= 3:
            continue  # 漏网的页眉页脚/固定声明类内容
        if len(hits) >= _AGGREGATE_MIN_HITS:
            frequent_groups.append((digest, hits))
            continue
        first_page, first_text = hits[0]
        second_page, second_text = hits[1]
        findings.append(Finding(
            rule_code="DOC-DUP-001",
            category=QaCategory.DUPLICATION,
            severity=QaSeverity.P2,
            certainty=QaCertainty.PROBABLE,
            title="段落疑似冗余",
            message=(
                f"同一段落({len(first_text)}字)在第"
                f"{'、'.join(str(p) for p in sorted(pages_hit)[:5])}页重复出现"
                f"{len(hits)}次(引用/固定声明属正常情况)"
            ),
            suggestion="确认是否为无意的内容重复",
            evidence=Evidence(
                kind="pair",
                items=(
                    EvidenceItem(excerpt=first_text[:120], page=first_page, role="primary"),
                    EvidenceItem(excerpt=second_text[:120], page=second_page, role="comparison"),
                ),
            ),
            anchor=f"dup_para:{digest[:16]}",
            closed_measurement=True,
        ))
        if len(findings) >= _MAX_FINDINGS:
            break

    # 1b) 高频复用段聚合(单条汇总, 每组给出首个位置)
    if frequent_groups:
        frequent_groups.sort(key=lambda item: -len(item[1]))
        max_hits = len(frequent_groups[0][1])
        findings.append(Finding(
            rule_code="DOC-DUP-003",
            category=QaCategory.DUPLICATION,
            severity=QaSeverity.P3,
            certainty=QaCertainty.POSSIBLE,
            title="模板段落大量复用",
            message=(
                f"检测到 {len(frequent_groups)} 组段落被大量复用"
                f"(每组重复 {_AGGREGATE_MIN_HITS} 次以上, 最高 {max_hits} 次), "
                "疑似模板/填充内容, 建议核对是否为无意义堆砌"
            ),
            suggestion="确认这些段落是否为有意的固定表述, 否则精简正文",
            evidence=Evidence(
                kind="sequence",
                items=tuple(
                    EvidenceItem(
                        excerpt=hits[0][1][:120],
                        page=hits[0][0],
                    )
                    for _, hits in frequent_groups[:5]
                ),
            ),
            anchor="dup_frequent:" + ",".join(
                digest[:8] for digest, _ in frequent_groups[:5]
            ),
            closed_measurement=True,
        ))

    # 2) 整页精确重复
    texts = role_page_texts(model)
    page_hashes: dict[str, list[int]] = {}
    for page_num in sorted(texts):
        text = texts[page_num] or ""
        normalized = normalize_for_hash(text)
        if len(normalized) < _MIN_PAGE_CHARS:
            continue
        page_hashes.setdefault(paragraph_hash(text), []).append(page_num)
    for digest, pages_hit in page_hashes.items():
        if len(pages_hit) < 2:
            continue
        first, second = pages_hit[0], pages_hit[1]
        preview = (texts.get(first) or "")[:120]
        findings.append(Finding(
            rule_code="DOC-DUP-002",
            category=QaCategory.DUPLICATION,
            severity=QaSeverity.P2,
            certainty=QaCertainty.PROBABLE,
            title="整页内容重复",
            message=f"第{first}页与第{'、'.join(str(p) for p in pages_hit[1:4])}页文本内容完全相同",
            suggestion="确认是否为重复装订/重复排版",
            evidence=Evidence(
                kind="pair",
                items=(
                    EvidenceItem(excerpt=preview, page=first, role="primary"),
                    EvidenceItem(excerpt=preview, page=second, role="comparison"),
                ),
            ),
            anchor=f"dup_page:{digest[:16]}",
            closed_measurement=True,
        ))
        if len(findings) >= _MAX_FINDINGS:
            break

    if len(findings) < _MAX_FINDINGS:
        findings.extend(_duplicate_row_findings(
            model, remaining=_MAX_FINDINGS - len(findings),
        ))
    if len(findings) < _MAX_FINDINGS:
        findings.extend(_repeated_heading_findings(
            model, rules, page_hashes, remaining=_MAX_FINDINGS - len(findings),
        ))

    exact_digests = {digest for digest, hits in occurrences.items() if len(hits) >= 2}
    if len(findings) < _MAX_FINDINGS:
        findings.extend(_near_duplicate_findings(
            paragraphs, exact_digests, remaining=_MAX_FINDINGS - len(findings),
        ))

    return CheckerOutcome(code=CODE, status=CheckerStatus.OK, findings=findings)


def _near_duplicate_findings(
    paragraphs: dict[int, list[str]],
    exact_digests: set[str],
    remaining: int,
) -> list[Finding]:
    """近重复: 归一化后长段落按长度 ±20% 分窗, 窗内 ratio 达阈值且非精确相同。

    ponytail: 窗内 O(n²), 每条最多比较 _NEAR_BUCKET_CAP 条; 超限截断漏报近重复。
    """
    if remaining <= 0:
        return []
    candidates: list[tuple[int, str, str, str, int]] = []
    for page_num in sorted(paragraphs):
        for paragraph in paragraphs[page_num]:
            normalized = normalize_for_hash(paragraph)
            nlen = len(normalized)
            if nlen < _MIN_PARAGRAPH_CHARS:
                continue
            digest = paragraph_hash(paragraph)
            if digest in exact_digests:
                continue
            candidates.append((page_num, paragraph, normalized, digest, nlen))
    candidates.sort(key=lambda item: item[4])

    findings: list[Finding] = []
    reported: set[tuple[str, str]] = set()
    total = len(candidates)
    for index, left in enumerate(candidates):
        compared = 0
        max_len = int(left[4] * (1 + _NEAR_LEN_RATIO))
        for other in range(index + 1, total):
            right = candidates[other]
            if right[4] > max_len:
                break
            compared += 1
            if compared > _NEAR_BUCKET_CAP:
                break
            pair = (left[3], right[3]) if left[3] < right[3] else (right[3], left[3])
            if pair in reported:
                continue
            if fuzz.ratio(left[2], right[2]) < _NEAR_RATIO:
                continue
            reported.add(pair)
            findings.append(Finding(
                rule_code="DOC-DUP-004",
                category=QaCategory.DUPLICATION,
                severity=QaSeverity.P3,
                certainty=QaCertainty.POSSIBLE,
                title="段落近重复",
                message=(
                    f"第{left[0]}页与第{right[0]}页段落高度相似"
                    f"(约 {len(left[1])} 字, 非精确相同)"
                ),
                suggestion="确认是否为复制后微改的冗余段落",
                evidence=Evidence(
                    kind="pair",
                    items=(
                        EvidenceItem(excerpt=left[1][:120], page=left[0], role="primary"),
                        EvidenceItem(excerpt=right[1][:120], page=right[0], role="comparison"),
                    ),
                ),
                anchor=f"dup_near:{pair[0][:12]}:{pair[1][:12]}",
            ))
            if len(findings) >= remaining:
                return findings
    return findings
