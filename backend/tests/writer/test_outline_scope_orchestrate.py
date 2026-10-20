from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.writer.outline_scope import attach_scope_titles_to_outline
from app.core.writer.outline_scope.chapter_retriever import (
    select_related_chunks_for_chapter as _select_related_chunks_for_chapter,
)
from app.core.writer.outline_scope.contracts import ScopeTitle
from app.schemas.writing import OutlineNodeGenerated


def _node(
    title: str,
    *,
    node_type: str = "free_writing",
    depth: int = 1,
    volume_type: str | None = "technical",
    children: list[OutlineNodeGenerated] | None = None,
) -> OutlineNodeGenerated:
    return OutlineNodeGenerated(
        title=title,
        depth=depth,
        sort_order=0,
        node_type=node_type,
        is_required=1,
        score_weight=None,
        children=children or [],
        is_skeleton=True,
        volume_type=volume_type,
    )


def _chunk(page_num: int, chunk_index: int, content: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=chunk_index,
        page_num=page_num,
        chunk_index=chunk_index,
        content=content,
    )


@pytest.mark.asyncio
async def test_attach_scope_titles_to_outline_enriches_each_free_writing_from_related_text(
    monkeypatch,
) -> None:
    qualify = _node("施工资质/准入")
    catchall = _node("其他资料（投标人需要补充的资料，格式由投标人自拟）")
    letter = _node("投标函", node_type="fixed_template")
    tree = [_node("技术标", depth=0, children=[letter, qualify, catchall])]

    chunks = [
        _chunk(4, 1, "防腐效能耦合机制分析测试\n招标范围:性能测试。\n2.7.投标人资格要求"),
        _chunk(8, 2, "投标人资格条件\n电子与智能化工程资质"),
        _chunk(24, 4, "第三章 建设内容\n地层水离子特征分析测试\n智慧园区平台建设"),
    ]

    async def _fake_extract(related, _llm, *, chapter_title=""):
        text = "\n".join(getattr(item, "content", "") or "" for item in related)
        if "施工资质" in chapter_title:
            if "地层水" in text:
                return [ScopeTitle("地层水离子特征分析测试", "项目内容")]
            return [ScopeTitle("电子与智能化工程资质", "资格要求")]
        if "其他资料" in chapter_title:
            return []
        if any(token in chapter_title for token in ("项目内容", "建设内容", "研究内容")):
            if "地层水" in text or "智慧园区" in text:
                return [ScopeTitle("地层水离子特征分析测试", "项目内容")]
        return []

    monkeypatch.setattr(
        "app.core.writer.outline_scope._load_tender_document",
        AsyncMock(return_value=SimpleNamespace(id=9)),
    )
    monkeypatch.setattr(
        "app.core.writer.outline_scope._load_chunks",
        AsyncMock(return_value=chunks),
    )
    monkeypatch.setattr(
        "app.core.writer.outline_scope.load_document_toc_map",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.core.writer.outline_scope._retrieve_related_chunks",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.core.writer.outline_scope.extract_scope_titles",
        _fake_extract,
    )

    attached = await attach_scope_titles_to_outline(
        tree=tree,
        db=SimpleNamespace(),
        llm_client=SimpleNamespace(),
        project_id=3443,
    )

    assert attached == 2
    assert [child.title for child in qualify.children] == ["电子与智能化工程资质"]
    assert [child.title for child in catchall.children] == ["地层水离子特征分析测试"]
    assert letter.children == []
    assert [child.title for child in tree[0].children] == [
        "投标函",
        "施工资质/准入",
        "其他资料（投标人需要补充的资料，格式由投标人自拟）",
    ]


@pytest.mark.asyncio
async def test_unclaimed_demand_titles_go_to_scheme_not_catchall(
    monkeypatch,
) -> None:
    qualify = _node("施工资质/准入")
    scheme = _node("技术方案")
    catchall = _node("其他资料（投标人需要补充的资料，格式由投标人自拟）")
    tree = [_node("技术标", depth=0, children=[qualify, scheme, catchall])]

    chunks = [
        _chunk(8, 2, "投标人资格条件\n电子与智能化工程资质"),
        _chunk(24, 4, "第三章 建设内容\n地层水离子特征分析测试"),
    ]

    async def _fake_extract(related, _llm, *, chapter_title=""):
        text = "\n".join(getattr(item, "content", "") or "" for item in related)
        if "施工资质" in chapter_title:
            return [ScopeTitle("电子与智能化工程资质", "资格要求")]
        if "技术方案" in chapter_title:
            return []
        if any(token in chapter_title for token in ("项目内容", "建设内容", "研究内容")):
            if "地层水" in text:
                return [ScopeTitle("地层水离子特征分析测试", "项目内容")]
        return []

    monkeypatch.setattr(
        "app.core.writer.outline_scope._load_tender_document",
        AsyncMock(return_value=SimpleNamespace(id=9)),
    )
    monkeypatch.setattr(
        "app.core.writer.outline_scope._load_chunks",
        AsyncMock(return_value=chunks),
    )
    monkeypatch.setattr(
        "app.core.writer.outline_scope.load_document_toc_map",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.core.writer.outline_scope._retrieve_related_chunks",
        AsyncMock(return_value=[]),
    )

    def _select(*, chapter_title, chunks, toc_map=None, retrieved=None):
        if chapter_title == "技术方案":
            return [item for item in chunks if "建设内容" in (item.content or "")]
        return _select_related_chunks_for_chapter(
            chapter_title=chapter_title,
            chunks=chunks,
            toc_map=toc_map,
            retrieved=retrieved,
        )

    monkeypatch.setattr(
        "app.core.writer.outline_scope.select_related_chunks_for_chapter",
        _select,
    )
    monkeypatch.setattr(
        "app.core.writer.outline_scope.extract_scope_titles",
        _fake_extract,
    )

    attached = await attach_scope_titles_to_outline(
        tree=tree,
        db=SimpleNamespace(),
        llm_client=SimpleNamespace(),
        project_id=3443,
    )

    assert attached == 2
    assert [child.title for child in qualify.children] == ["电子与智能化工程资质"]
    assert [child.title for child in scheme.children] == ["地层水离子特征分析测试"]
    assert catchall.children == []


@pytest.mark.asyncio
async def test_unclaimed_demand_titles_do_not_enter_unrelated_service_chapter(
    monkeypatch,
) -> None:
    qualify = _node("施工资质/准入")
    service = _node("售后服务")
    catchall = _node("其他资料（投标人需要补充的资料，格式由投标人自拟）")
    tree = [_node("技术标", depth=0, children=[qualify, service, catchall])]

    chunks = [
        _chunk(8, 2, "投标人资格条件\n电子与智能化工程资质"),
        _chunk(24, 4, "第三章 建设内容\n地层水离子特征分析测试"),
    ]

    async def _fake_extract(related, _llm, *, chapter_title=""):
        text = "\n".join(getattr(item, "content", "") or "" for item in related)
        if "施工资质" in chapter_title:
            return [ScopeTitle("电子与智能化工程资质", "资格要求")]
        if any(token in chapter_title for token in ("项目内容", "建设内容", "研究内容")):
            if "地层水" in text:
                return [ScopeTitle("地层水离子特征分析测试", "项目内容")]
        return []

    monkeypatch.setattr(
        "app.core.writer.outline_scope._load_tender_document",
        AsyncMock(return_value=SimpleNamespace(id=9)),
    )
    monkeypatch.setattr(
        "app.core.writer.outline_scope._load_chunks",
        AsyncMock(return_value=chunks),
    )
    monkeypatch.setattr(
        "app.core.writer.outline_scope.load_document_toc_map",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.core.writer.outline_scope._retrieve_related_chunks",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.core.writer.outline_scope.extract_scope_titles",
        _fake_extract,
    )

    attached = await attach_scope_titles_to_outline(
        tree=tree,
        db=SimpleNamespace(),
        llm_client=SimpleNamespace(),
        project_id=3443,
    )

    assert attached == 2
    assert [child.title for child in qualify.children] == ["电子与智能化工程资质"]
    assert service.children == []
    assert [child.title for child in catchall.children] == ["地层水离子特征分析测试"]
