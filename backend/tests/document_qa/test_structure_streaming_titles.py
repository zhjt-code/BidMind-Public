"""版面只做检测 + 逐页流式 + 标题文字只取文本层。

取文夹具: 文本层页(doc_title) 与扫描页(paragraph_title) 是同一机制的两种实体;
扫描页不在质检范围, 取不到文本层文字的标题留空, 不再截小图识别。
"""

from __future__ import annotations

import io
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.document_qa import structure_client
from app.core.document_qa.structure_pool import StructurePool

_OCR = Path(__file__).resolve().parents[2].parent / "ocr"
if str(_OCR) not in sys.path:
    sys.path.insert(0, str(_OCR))


def _use_instances(monkeypatch, urls: list[str]) -> None:
    pool = StructurePool(urls, fail_threshold=2, cooldown_sec=60.0)
    monkeypatch.setattr(structure_client, "structure_urls", lambda: urls)
    monkeypatch.setattr("app.core.document_qa.structure_pool.get_structure_pool", lambda: pool)


# ── 逐页流式: 渲染只在调用线程, 在飞页数有上限 ─────────────────────────


class _FakeDoc:
    def __init__(self, page_count: int) -> None:
        self.page_count = page_count

    def __enter__(self) -> "_FakeDoc":
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


def test_pages_render_in_caller_thread_within_bounded_window(monkeypatch) -> None:
    urls = ["http://s1", "http://s2", "http://s3"]
    _use_instances(monkeypatch, urls)
    lock = threading.Lock()
    state = {"inflight": 0, "max_inflight": 0}
    render_threads: set[str] = set()

    def render(_doc, page_num: int) -> str:
        render_threads.add(threading.current_thread().name)
        with lock:
            state["inflight"] += 1
            state["max_inflight"] = max(state["max_inflight"], state["inflight"])
        return f"img-{page_num}"

    def post(_url: str, _image: str, _timeout: float) -> list[dict]:
        time.sleep(0.02)
        with lock:
            state["inflight"] -= 1
        return []

    monkeypatch.setattr(structure_client, "_open_pdf", lambda _path: _FakeDoc(12))
    monkeypatch.setattr(structure_client, "_render_page", render)
    monkeypatch.setattr(structure_client, "_post_page", post)

    pages = structure_client.fetch_structure_pages("unused.pdf")

    assert pages is not None and set(pages) == set(range(1, 13))
    assert render_threads == {threading.current_thread().name}
    assert state["max_inflight"] <= len(urls) * structure_client._INFLIGHT_PER_INSTANCE


# ── 取文: 文本层页直接取字, 扫描页标题留空 ─────────────────────────────


@pytest.fixture()
def text_pdf(tmp_path):
    fitz = pytest.importorskip("fitz")
    path = tmp_path / "text.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "第一章 总则", fontsize=20, fontname="china-s")
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture()
def scanned_pdf(tmp_path):
    fitz = pytest.importorskip("fitz")
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (595, 842), "white")
    ImageDraw.Draw(image).rectangle((72, 80, 300, 110), fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    path = tmp_path / "scanned.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=buffer.getvalue())
    doc.save(str(path))
    doc.close()
    return path


_TITLE_BBOX = [0.10, 0.08, 0.60, 0.14]


def test_text_layer_title_uses_text_inside_box(monkeypatch, text_pdf) -> None:
    _use_instances(monkeypatch, ["http://s1"])
    monkeypatch.setattr(
        structure_client,
        "_post_page",
        lambda *_args: [{"label": "doc_title", "text": "", "bbox": _TITLE_BBOX, "level": 1}],
    )

    pages = structure_client.fetch_structure_pages(str(text_pdf))

    title = pages[1][0]
    assert title["text"] == "第一章 总则"
    assert "crop_png" not in title


def test_scanned_title_stays_empty_without_recognition(monkeypatch, scanned_pdf) -> None:
    _use_instances(monkeypatch, ["http://s1"])
    monkeypatch.setattr(
        structure_client,
        "_post_page",
        lambda *_args: [
            {"label": "paragraph_title", "text": "", "bbox": _TITLE_BBOX, "level": 3},
            {"label": "text", "text": "", "bbox": [0.1, 0.3, 0.9, 0.5], "level": None},
        ],
    )

    pages = structure_client.fetch_structure_pages(str(scanned_pdf))

    title, body = pages[1]
    assert title["text"] == ""
    assert set(title) == {"label", "text", "bbox", "level"}
    assert set(body) == {"label", "text", "bbox", "level"}


# ── 构模: 显式传入的版面结果不再重复加载 ───────────────────────────────


def test_explicit_none_structure_is_not_reloaded(monkeypatch, text_pdf) -> None:
    from app.core.document_qa import model_builder

    monkeypatch.setattr(
        model_builder,
        "_load_structure_pages",
        lambda _path: (_ for _ in ()).throw(AssertionError("不得重复加载版面")),
    )
    parsed = SimpleNamespace(
        pages=[SimpleNamespace(page_num=1, text="第一章 总则", images=[], tables=[], width=595.0, height=842.0)],
        page_count=1,
        filename="text.pdf",
    )

    model = model_builder.build_document_model(
        parsed, str(text_pdf), document_id=1, bid_part=None, structure_pages=None,
    )

    assert model.page_count == 1


# ── 版面服务: 检测结果只有标签与坐标, 自上而下排序 ─────────────────────


def test_layout_boxes_are_normalized_and_ordered() -> None:
    from structure_blocks import blocks_from_layout_result

    result = {"res": {"boxes": [
        {"label": "text", "score": 0.9, "coordinate": [100, 800, 900, 1200]},
        {"label": "paragraph_title", "score": 0.95, "coordinate": [100, 200, 600, 260]},
        {"label": "table", "score": 0.97, "coordinate": [50, 1300, 950, 1900]},
        {"label": "", "coordinate": [0, 0, 10, 10]},
    ]}}

    blocks = blocks_from_layout_result(result, width=1000, height=2000)

    assert [block["label"] for block in blocks] == ["paragraph_title", "text", "table"]
    assert blocks[0] == {
        "label": "paragraph_title", "text": "", "bbox": [0.1, 0.1, 0.6, 0.13], "level": 3, "order": 0,
    }
    assert blocks[2]["bbox"] == [0.05, 0.65, 0.95, 0.95]
