"""
职责: Q6 编号连续性检查——章节/图/表/附件/印刷页码等有序标识的缺号、重号、倒序
不做: 不校验目录页、不校验引用可解析性
机制: 家族由 rules/sequences.yaml 配置(章节家族来自标题树), 同一条序列差分链
      覆盖全部家族(图/表/附件是夹具, 不是专用路径);
      重号只在页距<=duplicate_max_page_gap 的邻近簇内判定(印刷页码除外);
      有版面的页章节编号只读表外标题, 图/表/附件只读拼好的题注, 印刷页码只取页眉页脚页码区
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Optional

from app.core.document_qa.region_text import (
    KIND_FURNITURE,
    layout_pages,
    page_texts,
    region_lines,
)
from app.core.document_qa.text_shapes import is_index_or_leader_line
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

CODE = "numbering"

_MAX_FINDINGS_PER_FAMILY = 5
_CN_DIGITS = "一二三四五六七八九十"

# 印刷页码: 页底部带短文本, 四形态共用同一序列差分链
_PAGE_NUM_BOTTOM_Y0 = 0.88
_PAGE_NUM_MAX_CHARS = 16
_PAGE_NUM_OVERFLOW = 20
_PAGE_NUM_PATTERNS = (
    re.compile(r"^(\d{1,4})$"),
    re.compile(r"^第\s*(\d{1,4})\s*页$"),
    re.compile(r"^(\d{1,4})\s*/\s*\d{1,4}$"),
    re.compile(r"^[-–—−]\s*(\d{1,4})\s*[-–—−]$"),
)


@dataclass(frozen=True)
class NumberOccurrence:
    """一次编号出现: 家族内分组键 + 序号值 + 定位"""

    group: str          # 父前缀(章节 "3.1" 的组是 "3"; 图 "5-2" 的组是 "5"; 无前缀为 "")
    value: int          # 序列末位数值
    display: str        # 原始编号文本
    page: int
    excerpt: str


def _cn_to_int(token: str) -> Optional[int]:
    token = token.strip()
    if token.isdigit():
        return int(token)
    # 仅 ASCII 字母走 A=1 分支(CJK 字符 isalpha() 同样为 True, 必须排除)
    if len(token) == 1 and token.isascii() and token.isalpha():
        return ord(token.upper()) - ord("A") + 1
    try:
        from pycnnum import cn2num

        value = cn2num(token)
        return int(value) if value else None
    except Exception:  # noqa: BLE001
        return None


def _parse_compound(raw: str) -> Optional[tuple[str, int]]:
    """解析复合编号 -> (组键, 末位值)。支持 5-2 / 5.2 / 3.1.2 / 三 / B。"""
    normalized = raw.strip().replace("－", "-").replace("–", "-")
    parts = re.split(r"[-.]", normalized)
    last = _cn_to_int(parts[-1])
    if last is None:
        return None
    group = ".".join(parts[:-1])
    return group, last


def _caption_lines(model: DocumentModel):
    """编号家族候选行, 按页序产出。

    有版面的页只取拼好的题注和表外标题(表格单元格里同形的文字不是题注);
    其余页取页文本行 + 块首行(抽取通道丢掉空格时块层仍可能保留)。
    """
    from app.core.document_qa.author_spans import caption_lines

    layout = layout_pages(model)
    if layout:
        yielded: set[tuple[int, str]] = set()
        for page_num, text in caption_lines(model):
            stripped = text.strip()
            if stripped and (page_num, stripped) not in yielded:
                yielded.add((page_num, stripped))
                yield page_num, stripped
        for page_num in sorted(set(model.body_texts) - layout):
            for line in (model.body_texts[page_num] or "").split("\n"):
                stripped = line.strip()
                if stripped and (page_num, stripped) not in yielded:
                    yielded.add((page_num, stripped))
                    yield page_num, stripped
        return
    yielded = set()
    for page_num in sorted(set(model.body_texts) | layout):
        if page_num in layout:
            lines = []
        else:
            lines = (model.body_texts[page_num] or "").split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped and (page_num, stripped) not in yielded:
                yielded.add((page_num, stripped))
                yield page_num, stripped
    for block in model.blocks:
        if block.page in layout:
            continue
        stripped = (block.text.split("\n", 1)[0] or "").strip()
        if stripped and (block.page, stripped) not in yielded:
            yielded.add((block.page, stripped))
            yield block.page, stripped


def _same_caption_channel(left: str, right: str) -> bool:
    """抽取通道丢掉空格后, 同一 caption 会变成前缀/超集关系, 不是两个对象。

    ponytail: 上限是同页同号且去空格后一串是另一串前缀; 真·两对象若标题互为前缀会漏报,
    升级为按编号后 token 对齐再比。
    """
    compact_left = "".join((left or "").split())
    compact_right = "".join((right or "").split())
    if not compact_left or not compact_right:
        return False
    return (
        compact_left == compact_right
        or compact_left.startswith(compact_right)
        or compact_right.startswith(compact_left)
    )


def _collect_family_occurrences(
    model: DocumentModel,
    patterns: list[re.Pattern[str]],
) -> list[NumberOccurrence]:
    occurrences: list[NumberOccurrence] = []
    for page_num, stripped in _caption_lines(model):
        if is_index_or_leader_line(stripped):
            continue  # 目录/索引行(行尾独立页码), 非正文 caption
        for pattern in patterns:
            matched = pattern.match(stripped + " ")
            if not matched:
                continue
            parsed = _parse_compound(matched.group(1))
            if parsed is None:
                continue
            group, value = parsed
            if any(
                occ.page == page_num and occ.group == group and occ.value == value
                and _same_caption_channel(occ.excerpt, stripped)
                for occ in occurrences
            ):
                break
            occurrences.append(NumberOccurrence(
                group=group, value=value,
                display=matched.group(1), page=page_num,
                excerpt=stripped[:60],
            ))
            break
    return occurrences


def _infer_heading_kind(number: str, kind: Optional[str]) -> str:
    if kind:
        return kind
    if re.match(rf"^第([{_CN_DIGITS}百\d]+)[章节部分篇卷]$", number):
        return "unit"
    if re.fullmatch(r"\d+(?:\.\d+)+", number):
        return "decimal"
    if re.fullmatch(r"\d+", number):
        return "top_digit"
    if number.startswith("附"):
        return "attachment"
    return "cn_digit"


def _list_rank(kind: Optional[str], number: str) -> tuple[int, int]:
    """编号嵌套秩: 越小越能当父。中文括号节包阿拉伯括号列表, 不用句式局部 level。"""
    token = (number or "").strip()
    kind = kind or ""
    if kind == "unit":
        senior = 0 if any(mark in token for mark in ("章", "部分", "篇", "卷")) else 1
        return (0, senior)
    if kind == "cn_digit":
        return (1, 0)
    if kind == "decimal":
        return (2, token.count("."))
    if kind in ("top_digit", "top_space", "attachment"):
        return (3, 0)
    if kind == "paren":
        if re.fullmatch(rf"[{_CN_DIGITS}]+", token):
            return (4, 0)
        return (5, 0)
    return (6, 0)


def _heading_occurrences(model: DocumentModel) -> list[NumberOccurrence]:
    """章节家族: 从表外标题取编号。稳定小数按编号父前缀分组, 可重启句式仍带父路径。"""
    from app.core.document_qa.author_spans import author_headings

    occurrences: list[NumberOccurrence] = []
    unit_re = re.compile(rf"^第([{_CN_DIGITS}百\d]+)[章节部分篇卷]$")
    ancestors: list = []
    for heading in author_headings(model):
        number = (heading.number or "").strip()
        if not number:
            continue
        kind = _infer_heading_kind(number, heading.kind)
        current_rank = _list_rank(kind, number)
        while ancestors and _list_rank(
            _infer_heading_kind(ancestors[-1].number or "", ancestors[-1].kind),
            ancestors[-1].number or "",
        ) >= current_rank:
            ancestors.pop()
        parent_path = ">".join(item.display for item in ancestors)
        excerpt = heading.display[:60]
        unit_matched = unit_re.match(number)
        if unit_matched:
            value = _cn_to_int(unit_matched.group(1))
            if value is not None:
                occurrences.append(NumberOccurrence(
                    group=f"unit|{parent_path}", value=value, display=number,
                    page=heading.page, excerpt=excerpt,
                ))
            ancestors.append(heading)
            continue
        if re.fullmatch(r"\d+(?:\.\d+)*", number):
            parts = number.split(".")
            numbering_parent = ".".join(parts[:-1])
            if kind == "decimal":
                group = f"decimal|{numbering_parent}"
            else:
                group = f"{kind}|{parent_path}|{numbering_parent}"
            occurrences.append(NumberOccurrence(
                group=group,
                value=int(parts[-1]),
                display=number, page=heading.page, excerpt=excerpt,
            ))
            ancestors.append(heading)
            continue
        value = _cn_to_int(number.rstrip("、.．"))
        if value is not None:
            occurrences.append(NumberOccurrence(
                group=f"{kind}|{parent_path}", value=value, display=number,
                page=heading.page, excerpt=excerpt,
            ))
        ancestors.append(heading)
    return occurrences


def _readable_member(group_key: str, value: int) -> str:
    """缺号文案用编号父前缀, 禁止把 kind|祖先路径泄漏给用户。"""
    tail = (group_key or "").split("|")[-1]
    if re.fullmatch(r"\d+(?:\.\d+)*", tail or ""):
        return f"{tail}.{value}"
    return str(value)


def _split_ascending_runs(members: list[NumberOccurrence]) -> list[list[NumberOccurrence]]:
    """升序游程切分: 值下降或回到序列起点(1)则开新游程。

    可重启家族(一/（一）/1)常在新章节直接从 1 再数, 中间未必出现更大号。
    """
    runs: list[list[NumberOccurrence]] = []
    current: list[NumberOccurrence] = []
    previous: Optional[int] = None
    for member in members:
        restart = previous is not None and (
            member.value < previous or member.value == 1
        )
        if restart:
            if current:
                runs.append(current)
            current = [member]
        else:
            current.append(member)
        previous = member.value
    if current:
        runs.append(current)
    return runs


def _page_gap_clusters(
    members: list[NumberOccurrence],
    max_page_gap: Optional[int],
) -> list[list[NumberOccurrence]]:
    """按页距把同号出现收成邻近簇; 间隔大于阈值视为另一编号实例。

    max_page_gap 为 None 时整组视为一簇(整本唯一序列, 如印刷页码)。
    簇长度 < 2 不返回。
    """
    ordered = sorted(members, key=lambda item: (item.page, item.excerpt))
    if not ordered:
        return []
    if max_page_gap is None:
        return [ordered] if len(ordered) >= 2 else []
    clusters: list[list[NumberOccurrence]] = []
    current: list[NumberOccurrence] = []
    for item in ordered:
        if current and item.page - current[-1].page > max_page_gap:
            if len(current) >= 2:
                clusters.append(current)
            current = [item]
        else:
            current.append(item)
    if len(current) >= 2:
        clusters.append(current)
    return clusters


def _diff_sequence(
    label: str,
    occurrences: list[NumberOccurrence],
    *,
    restartable: bool = False,
    max_duplicate_page_gap: Optional[int] = 2,
    ignored_missing: Optional[set[int]] = None,
) -> list[Finding]:
    """序列差分: 组内缺号(certain)/重号(possible)/倒序(possible)。

    可重启家族跨页同号视为另一列表实例, 不报 DOC-NUM-002; 同页不同摘录仍报。
    稳定家族重号只在页距<=max_duplicate_page_gap 的邻近簇内落发现;
    max_duplicate_page_gap=None 表示不按页距切开(印刷页码)。
    """
    findings: list[Finding] = []
    groups: dict[str, list[NumberOccurrence]] = {}
    for occ in occurrences:
        groups.setdefault(occ.group, []).append(occ)

    scoped: list[list[NumberOccurrence]] = []
    for _group_key, members in groups.items():
        if restartable:
            scoped.extend(_split_ascending_runs(members))
        else:
            scoped.append(members)

    for members in scoped:
        if len(members) < 2:
            continue
        group_key = members[0].group
        values = [m.value for m in members]
        seen = set(values)
        lo, hi = min(seen), max(seen)

        missing = sorted(set(range(lo, hi + 1)) - seen)
        if ignored_missing:
            missing = [value for value in missing if value not in ignored_missing]
        if missing and len(missing) <= 20:
            missing_text = "、".join(_readable_member(group_key, v) for v in missing[:10])
            lo_text = _readable_member(group_key, lo)
            hi_text = _readable_member(group_key, hi)
            neighbors = sorted(members, key=lambda m: m.value)
            findings.append(Finding(
                rule_code="DOC-NUM-001",
                category=QaCategory.NUMBERING,
                severity=QaSeverity.P1,
                certainty=QaCertainty.CERTAIN,
                title=f"{label}编号不连续",
                message=f"{label}编号序列缺少 {missing_text}(现有 {lo_text} 至 {hi_text})",
                suggestion="确认对应内容是否漏排, 或编号书写有误",
                evidence=Evidence(
                    kind="sequence",
                    items=tuple(
                        EvidenceItem(excerpt=m.excerpt, page=m.page)
                        for m in neighbors[:6]
                    ),
                    expected=f"{lo_text}..{hi_text} 连续",
                    actual=f"缺 {missing_text}",
                ),
                anchor=f"num_missing:{label}:{group_key}:{missing_text}",
                closed_measurement=True,
            ))

        duplicated: dict[int, list[NumberOccurrence]] = {}
        for member in members:
            duplicated.setdefault(member.value, []).append(member)
        for value, dup_members in duplicated.items():
            if len(dup_members) < 2:
                continue
            dup_pages = {m.page for m in dup_members}
            if restartable and len(dup_pages) >= 2:
                continue  # 可重启家族跨页同号: 另一列表实例, 不是同一对象续排
            for cluster in _page_gap_clusters(dup_members, max_duplicate_page_gap):
                cluster_pages = {m.page for m in cluster}
                distinct_excerpts = {m.excerpt for m in cluster}
                if len(cluster_pages) >= 2:
                    # 邻近跨页同号: 可能是续排同一对象, possible
                    certainty = QaCertainty.POSSIBLE
                    message = (
                        f"{label}编号「{cluster[0].display}」在第"
                        f"{'、'.join(str(p) for p in sorted(cluster_pages)[:4])}页重复出现"
                        "(跨页续排同一对象属正常情况)"
                    )
                elif len(distinct_excerpts) >= 2:
                    # 同页两个不同对象共用同一编号("表7-2 归档检查表"与"表7-2 命名规则检查表")
                    certainty = QaCertainty.PROBABLE
                    message = (
                        f"第{cluster[0].page}页出现两个不同的"
                        f"「{label}{cluster[0].display}」, 疑似编号未递增"
                    )
                else:
                    continue  # 同页同文本(解析重复行), 不报
                first, last = cluster[0], cluster[-1]
                findings.append(Finding(
                    rule_code="DOC-NUM-002",
                    category=QaCategory.NUMBERING,
                    severity=QaSeverity.P2,
                    certainty=certainty,
                    title=f"{label}编号重复",
                    message=message,
                    suggestion="确认是否为两个不同对象使用了同一编号",
                    evidence=Evidence(
                        kind="pair",
                        items=(
                            EvidenceItem(
                                excerpt=first.excerpt, page=first.page, role="primary",
                            ),
                            EvidenceItem(
                                excerpt=last.excerpt, page=last.page, role="comparison",
                            ),
                        ),
                    ),
                    anchor=f"num_dup:{label}:{group_key}:{value}",
                    closed_measurement=True,
                ))

        max_so_far: Optional[int] = None
        seen_before: set[int] = set()
        for member in members:
            if (
                max_so_far is not None
                and member.value < max_so_far
                and member.value not in seen_before
            ):
                findings.append(Finding(
                    rule_code="DOC-NUM-003",
                    category=QaCategory.NUMBERING,
                    severity=QaSeverity.P3,
                    certainty=QaCertainty.POSSIBLE,
                    title=f"{label}编号顺序倒置",
                    message=(
                        f"{label}编号「{member.display}」(第{member.page}页)"
                        f"出现在更大编号之后"
                    ),
                    suggestion="确认内容排列顺序是否正确",
                    evidence=Evidence(
                        kind="single",
                        items=(EvidenceItem(excerpt=member.excerpt, page=member.page),),
                    ),
                    anchor=f"num_order:{label}:{group_key}:{member.display}",
                    closed_measurement=True,
                ))
                break
            seen_before.add(member.value)
            max_so_far = member.value if max_so_far is None else max(max_so_far, member.value)

    return findings[:_MAX_FINDINGS_PER_FAMILY]


def _parse_printed_page(text: str) -> Optional[int]:
    stripped = text.strip()
    if not stripped or len(stripped) > _PAGE_NUM_MAX_CHARS:
        return None
    for pattern in _PAGE_NUM_PATTERNS:
        matched = pattern.fullmatch(stripped)
        if matched:
            return int(matched.group(1))
    return None


def _page_number_occurrences(model: DocumentModel) -> list[NumberOccurrence]:
    """页底部带采集印刷页码: 有版面的页只取页眉页脚页码区, 其余页取 blocks; 每页取最靠底一条。封面空窗不入序列。"""
    best: dict[int, tuple[float, str, int]] = {}

    def consider(page: int, y0: float, text: str) -> None:
        if y0 <= _PAGE_NUM_BOTTOM_Y0:
            return
        value = _parse_printed_page(text)
        if value is None:
            return
        previous = best.get(page)
        if previous is None or y0 > previous[0]:
            best[page] = (y0, text.strip(), value)

    layout = layout_pages(model)
    for block in model.blocks:
        if block.page in layout:
            continue
        if block.spans:
            for span in block.spans:
                consider(block.page, span.bbox[1], span.text)
        else:
            consider(block.page, block.bbox[1], block.text)
    for line in region_lines(model, KIND_FURNITURE):
        consider(line.page, line.bbox[1], line.text)

    limit = (model.page_count or 0) + _PAGE_NUM_OVERFLOW
    occurrences: list[NumberOccurrence] = []
    for page_num in sorted(best):
        _y0, display, value = best[page_num]
        if value < 1 or (limit > _PAGE_NUM_OVERFLOW and value > limit):
            continue
        occurrences.append(NumberOccurrence(
            group="printed", value=value, display=display,
            page=page_num, excerpt=display,
        ))
    return occurrences


_ECHO_MIN_CHARS = 200


def _long_page_digests(model: DocumentModel) -> dict[int, str]:
    """够长的页正文哈希。空白页不参与「两页相同」, 避免空对空被当成复制页。"""
    digests: dict[int, str] = {}
    for page_num, text in page_texts(model).items():
        if len(normalize_for_hash(text)) < _ECHO_MIN_CHARS:
            continue
        digests[page_num] = paragraph_hash(text)
    return digests


def _explained_printed_gaps(
    model: DocumentModel,
    occurrences: list[NumberOccurrence],
) -> set[int]:
    """空白页、图片页、与邻页正文相同的页, 其应有印刷页码不算缺号。"""
    digests = _long_page_digests(model)
    silent = {
        page for page, digest in digests.items()
        if digests.get(page - 1) == digest or digests.get(page + 1) == digest
    }
    for page in model.pages:
        blank = page.char_count == 0 and page.image_count == 0 and page.table_count == 0
        if page.image_only or blank:
            silent.add(page.page_num)
    known = {item.page: item.value for item in occurrences}
    if not known:
        return set()
    offset = Counter(page - value for page, value in known.items()).most_common(1)[0][0]
    known_values = set(known.values())
    return {
        page - offset
        for page in silent
        if page - offset >= 1 and (page - offset) not in known_values
    }


def _drop_copied_page_duplicates(
    model: DocumentModel,
    findings: list[Finding],
) -> list[Finding]:
    """两页正文哈希相同则重号是复制页的回声, 不另报。同页不同摘录仍保留。"""
    digests = _long_page_digests(model)
    kept: list[Finding] = []
    for finding in findings:
        if finding.rule_code != "DOC-NUM-002":
            kept.append(finding)
            continue
        pages = {item.page for item in finding.evidence.items if item.page is not None}
        hashes = [digests.get(page) for page in pages]
        if len(pages) >= 2 and all(hashes) and len(set(hashes)) == 1:
            continue
        kept.append(finding)
    return kept


def check_numbering(model: DocumentModel, rules: dict) -> CheckerOutcome:
    findings: list[Finding] = []

    restartable_kinds = set(
        ((rules.get("heading") or {}).get("restartable_kinds")) or
        ("cn_digit", "paren", "top_digit", "top_space")
    )
    seq_rules = rules.get("sequences") or {}
    raw_gap = seq_rules.get("duplicate_max_page_gap", 2)
    max_dup_gap = 2 if raw_gap is None else int(raw_gap)
    heading_occ = _heading_occurrences(model)
    restartable_occ = [item for item in heading_occ if item.group.split("|", 1)[0] in restartable_kinds]
    stable_occ = [item for item in heading_occ if item.group.split("|", 1)[0] not in restartable_kinds]
    findings.extend(_diff_sequence(
        "章节", stable_occ, max_duplicate_page_gap=max_dup_gap,
    ))
    findings.extend(_diff_sequence(
        "章节", restartable_occ, restartable=True, max_duplicate_page_gap=max_dup_gap,
    ))

    # 配置家族(图/表/附件/附录…)
    for family in seq_rules.get("families", []):
        patterns = [re.compile(p) for p in family.get("patterns", [])]
        label = str(family.get("label") or family.get("key") or "")
        occurrences = _collect_family_occurrences(model, patterns)
        findings.extend(_diff_sequence(
            label, occurrences, max_duplicate_page_gap=max_dup_gap,
        ))

    printed = _page_number_occurrences(model)
    findings.extend(_diff_sequence(
        "印刷页码", printed, max_duplicate_page_gap=None,
        ignored_missing=_explained_printed_gaps(model, printed),
    ))

    return CheckerOutcome(
        code=CODE, status=CheckerStatus.OK,
        findings=_drop_copied_page_duplicates(model, findings),
    )
