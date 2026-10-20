"""目录页解析器

从投标/招标文件前几页提取目录结构,建立 关键词 -> 页码 的映射。
供检索阶段做结构导航: 根据招标要求关键词直接定位投标文件中的对应章节页码。

典型目录格式:
  一、投标函 ........................ 1
  二、法人授权书 .................... 5
  三、资质证书 ...................... 10
  四、人员证件 ...................... 15
    4.1 项目经理证件 ............... 17
"""

from dataclasses import dataclass
import json
import re
from typing import Any, Optional
from loguru import logger

from app.core.parser.page_coordinates import (
    normalize_toc_title,
    resolve_toc_page_coordinates,
)
from app.core.parser.pdf_parser import ParsedPage


# 目录行匹配: "标题 ...... 页码" 或 "标题 —— 页码"
_TOC_LINE = re.compile(
    r"(.{2,40}?)\s*[\.·…—\-]{2,}\s*(\d{1,4})"
)

# 中文数字序号 + 标题(如 "一、投标函")
_CN_SECTION = re.compile(
    r"[一二三四五六七八九十]+[、.]\s*(.+)"
)

# 停用词: 从标题中移除的无语义价值词
_STOP_WORDS = frozenset({
    "的", "及", "和", "与", "等", "其他", "附件", "目录",
    "投标文件", "正本", "副本", "页码", "序号",
})
_LOOKUP_DROP_CHARS = "的"


def _normalize_lookup_token(text: str) -> str:
    """归一化目录匹配 token,容忍少量助词差异。"""
    normalized = normalize_toc_title(text)
    normalized = normalized.translate(str.maketrans("", "", _LOOKUP_DROP_CHARS))
    return re.sub(r"\s+", "", normalized)


@dataclass(frozen=True)
class TocEntry:
    """目录条目"""

    title: str
    page_num: int


def _extract_keywords_from_title(title: str) -> list[str]:
    """从目录标题提取关键词

    Args:
        title: 目录条目标题

    Returns:
        list[str]: 关键词列表
    """
    # 去掉中文序号前缀
    m = _CN_SECTION.match(title)
    if m:
        title = m.group(1)

    # 去掉数字序号(如 "4.1")
    title = re.sub(r"^\d+[\.\s]+", "", title).strip()

    # 按标点和空格拆分,过滤停用词
    tokens = re.findall(r"[\u4e00-\u9fff\w]{2,}", title)
    keywords = [t for t in tokens if t not in _STOP_WORDS and len(t) >= 2]

    # 同时保留完整标题(如"项目经理证件")
    if len(title) >= 2 and title not in keywords:
        keywords.insert(0, title)

    return keywords


class TocParser:
    """目录页解析器

    示例:
        >>> parser = TocParser()
        >>> toc_map = parser.parse(pages)
        >>> pages = parser.lookup_pages("项目经理身份证", toc_map)
        >>> # [17, 18]
    """

    def extract_toc_entries(
        self,
        pages: list[ParsedPage],
        max_pages: int = 5,
    ) -> list[TocEntry]:
        """从前N页提取有序目录条目。"""
        entries: list[TocEntry] = []
        seen: set[tuple[str, int]] = set()

        for p in pages[:max_pages]:
            if not p.text:
                continue

            for line in p.text.split("\n"):
                m = _TOC_LINE.search(line)
                if not m:
                    continue

                title = m.group(1).strip()
                page_num = int(m.group(2))
                if page_num < 1 or page_num > 999:
                    continue
                key = (title, page_num)
                if key in seen:
                    continue
                seen.add(key)
                entries.append(TocEntry(title=title, page_num=page_num))

        entries.sort(key=lambda entry: (entry.page_num, entry.title))
        return entries

    def parse(
        self,
        pages: list[ParsedPage],
        max_pages: int = 5,
    ) -> dict[str, list[int]]:
        """从前N页提取目录, 返回 {关键词: [页码]} 映射

        Args:
            pages: 文档所有页面
            max_pages: 扫描前几页(默认5)

        Returns:
            dict[str, list[int]]: 关键词到页码的映射
        """
        toc_map: dict[str, list[int]] = {}
        entries = self.extract_toc_entries(pages, max_pages=max_pages)

        for entry in entries:
            keywords = _extract_keywords_from_title(entry.title)
            for kw in keywords:
                toc_map.setdefault(kw, [])
                if entry.page_num not in toc_map[kw]:
                    toc_map[kw].append(entry.page_num)

        if entries:
            logger.info(f"目录解析: 发现{len(entries)}个条目, {len(toc_map)}个关键词")
        else:
            logger.debug("目录解析: 未发现目录结构")

        return toc_map

    def parse_resolved(
        self,
        pages: list[ParsedPage],
        max_pages: int = 5,
    ) -> dict[str, Any]:
        """解析目录并返回带坐标系声明的 v2 toc_map。

        v2 结构同时保留目录印刷页码和 PDF 物理页码。只有 enabled=true
        时，下游才允许把 physical_page 用作 chunk 查询锚点。
        """
        entries = self.extract_toc_entries(pages, max_pages=max_pages)
        if not entries:
            logger.debug("目录解析: 未发现目录结构")
            return {}

        resolution = resolve_toc_page_coordinates(
            pages,
            entries,
            max_toc_pages=max_pages,
        )
        keyword_map: dict[str, list[dict[str, int | str | None]]] = {}
        for entry in resolution.entries:
            for keyword in _extract_keywords_from_title(entry.title):
                keyword_map.setdefault(keyword, [])
                keyword_map[keyword].append(entry.to_dict())

        toc_map: dict[str, Any] = {
            "version": 2,
            "coordinate_system": "physical_page",
            "offset": resolution.offset,
            "confidence": resolution.confidence,
            "enabled": resolution.is_reliable,
            "matched_count": resolution.matched_count,
            "total_entries": resolution.total_entries,
            "entries": [entry.to_dict() for entry in resolution.entries],
            "keywords": keyword_map,
        }
        logger.info(
            "目录解析(v2): 发现{}个条目, offset={}, confidence={}, enabled={}",
            len(entries),
            resolution.offset,
            resolution.confidence,
            resolution.is_reliable,
        )
        return toc_map

    def lookup_pages(
        self,
        description: str,
        toc_map: dict[str, list[int]],
    ) -> list[int]:
        """根据要求描述查找目录中对应的页码

        对描述中的关键词逐一匹配目录映射,返回命中的所有页码(去重排序)。

        Args:
            description: 招标要求描述
            toc_map: 目录映射

        Returns:
            list[int]: 匹配到的页码列表
        """
        if not toc_map:
            return []

        matched_pages: set[int] = set()

        # 提取描述中的关键词
        tokens = re.findall(r"[\u4e00-\u9fff\w]{2,}", description)

        for token in tokens:
            if token in _STOP_WORDS:
                continue
            # 精确匹配
            if token in toc_map:
                matched_pages.update(toc_map[token])
                continue
            # 模糊匹配: 目录关键词包含token或token包含目录关键词
            for kw, page_list in toc_map.items():
                if token in kw or kw in token:
                    matched_pages.update(page_list)

        return sorted(matched_pages)

    def lookup_physical_pages(
        self,
        description: str,
        toc_map: dict[str, Any],
    ) -> list[int]:
        """根据要求描述查找 v2 toc_map 中的 PDF 物理页码。

        legacy toc_map 坐标系不明，不能用于 chunk.page_num 查询。
        """
        if not isinstance(toc_map, dict):
            return []
        if toc_map.get("version") != 2 or not toc_map.get("enabled"):
            return []

        keywords = toc_map.get("keywords")
        if not isinstance(keywords, dict):
            return []

        matched_pages: set[int] = set()
        for entry in self._lookup_structured_entries(description, keywords):
            physical_page = entry.get("physical_page")
            if isinstance(physical_page, int) and physical_page > 0:
                matched_pages.add(physical_page)
        return sorted(matched_pages)

    def _lookup_structured_entries(
        self,
        description: str,
        keywords: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """从 v2 keywords 结构中按描述匹配目录条目。"""
        if not keywords:
            return []

        matched: list[dict[str, Any]] = []
        seen: set[tuple[str, int | None, int | None]] = set()
        tokens = re.findall(r"[\u4e00-\u9fff\w]{2,}", description)
        normalized_tokens = [normalize_toc_title(token) for token in tokens]
        loose_tokens = [_normalize_lookup_token(token) for token in tokens]

        def add_entries(items: Any) -> None:
            if not isinstance(items, list):
                return
            for item in items:
                if not isinstance(item, dict):
                    continue
                key = (
                    str(item.get("title") or ""),
                    item.get("printed_page") if isinstance(item.get("printed_page"), int) else None,
                    item.get("physical_page") if isinstance(item.get("physical_page"), int) else None,
                )
                if key in seen:
                    continue
                seen.add(key)
                matched.append(item)

        for token, normalized_token, loose_token in zip(tokens, normalized_tokens, loose_tokens):
            if token in _STOP_WORDS or not normalized_token:
                continue
            if token in keywords:
                add_entries(keywords[token])
                continue
            for keyword, entries in keywords.items():
                normalized_keyword = normalize_toc_title(keyword)
                loose_keyword = _normalize_lookup_token(keyword)
                if (
                    normalized_token in normalized_keyword
                    or normalized_keyword in normalized_token
                    or (loose_token and loose_token in loose_keyword)
                    or (loose_keyword and loose_keyword in loose_token)
                ):
                    add_entries(entries)

        return matched


def serialize_toc_map(toc_map: dict[str, Any]) -> str:
    """将目录映射序列化为JSON字符串"""
    if not toc_map:
        return ""
    return json.dumps(toc_map, ensure_ascii=False)


def deserialize_toc_map(toc_json: Optional[str]) -> dict[str, Any]:
    """从JSON字符串反序列化目录映射"""
    if not toc_json:
        return {}
    if isinstance(toc_json, dict):
        return toc_json
    try:
        return json.loads(toc_json)
    except (json.JSONDecodeError, TypeError):
        return {}
