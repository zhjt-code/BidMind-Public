"""版面实例冷却、恢复, 以及失败页只换一台重试。"""

from __future__ import annotations

import httpx

from app.core.document_qa.structure_client import fetch_structure_pages
from app.core.document_qa.structure_pool import StructurePool


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_pool_cools_and_rejoins_after_probe() -> None:
    clock = _Clock()
    probes = {"http://s2": False}
    pool = StructurePool(
        ["http://s1", "http://s2", "http://s3"],
        fail_threshold=2,
        cooldown_sec=60.0,
        clock=clock,
        probe=lambda url: probes[url],
    )
    assert pool.acquire_instance() == "http://s1"
    pool.report_result("http://s2", ok=False)
    pool.report_result("http://s2", ok=False)
    held = [pool.acquire_instance() for _ in range(2)]
    assert "http://s2" not in held

    clock.now = 60.0
    probes["http://s2"] = False
    assert pool.acquire_instance(exclude=set(held)) is None

    clock.now = 120.0
    probes["http://s2"] = True
    assert pool.acquire_instance(exclude=set(held)) == "http://s2"


class _FakeDoc:
    def __init__(self, page_count: int) -> None:
        self.page_count = page_count

    def __enter__(self) -> "_FakeDoc":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _install_pages(monkeypatch, pages: list[tuple[int, str]]) -> None:
    images = dict(pages)
    monkeypatch.setattr(
        "app.core.document_qa.structure_client._open_pdf",
        lambda _path: _FakeDoc(len(images)),
    )
    monkeypatch.setattr(
        "app.core.document_qa.structure_client._render_page",
        lambda _doc, page_num: images[page_num],
    )


def _run(monkeypatch, post, pages: list[tuple[int, str]]):
    pool = StructurePool(["http://s1", "http://s2", "http://s3"], fail_threshold=2, cooldown_sec=60.0)
    monkeypatch.setattr(
        "app.core.document_qa.structure_client.structure_urls",
        lambda: ["http://s1", "http://s2", "http://s3"],
    )
    _install_pages(monkeypatch, pages)
    monkeypatch.setattr("app.core.document_qa.structure_client._post_page", post)
    monkeypatch.setattr(
        "app.core.document_qa.structure_pool.get_structure_pool",
        lambda: pool,
    )
    return pool, fetch_structure_pages("unused.pdf")


def test_http_503_retries_on_another_instance(monkeypatch) -> None:
    attempts: dict[str, int] = {}

    def post(url: str, image: str, _timeout: float) -> list[dict]:
        attempts[image] = attempts.get(image, 0) + 1
        if url == "http://s2":
            request = httpx.Request("POST", f"{url}/structure")
            response = httpx.Response(503, request=request)
            raise httpx.HTTPStatusError("503", request=request, response=response)
        return [{"image": image}]

    _pool, pages = _run(monkeypatch, post, [(1, "a"), (2, "b"), (3, "c")])
    assert pages is not None and set(pages) == {1, 2, 3}
    assert max(attempts.values()) <= 2


def test_timeout_and_connect_error_use_same_failover(monkeypatch) -> None:
    attempts: dict[str, int] = {}

    def post(url: str, image: str, _timeout: float) -> list[dict]:
        attempts[image] = attempts.get(image, 0) + 1
        if url == "http://s2" and image == "a":
            raise httpx.TimeoutException("timed out")
        if url == "http://s2" and image == "b":
            raise httpx.ConnectError("refused")
        return [{"image": image}]

    _pool, pages = _run(monkeypatch, post, [(1, "a"), (2, "b"), (3, "c")])
    assert pages is not None and set(pages) == {1, 2, 3}
    assert max(attempts.values()) <= 2


def test_page_failing_everywhere_falls_back_after_one_retry(monkeypatch) -> None:
    attempts: dict[str, int] = {}

    def post(url: str, image: str, _timeout: float) -> list[dict]:
        attempts[image] = attempts.get(image, 0) + 1
        if image == "bad":
            raise RuntimeError("503")
        return [{"image": image}]

    pool, pages = _run(monkeypatch, post, [(1, "ok"), (2, "bad"), (3, "ok2")])
    assert pages is not None and set(pages) == {1, 3}
    assert attempts["bad"] == 2
    assert all(count < 2 for count in pool._consecutive.values())


def test_all_instances_cooled_degrades_without_blocking(monkeypatch) -> None:
    pool = StructurePool(["http://s1", "http://s2", "http://s3"], fail_threshold=2, cooldown_sec=60.0)
    for url in ("http://s1", "http://s2", "http://s3"):
        pool.report_result(url, ok=False)
        pool.report_result(url, ok=False)
    monkeypatch.setattr(
        "app.core.document_qa.structure_client.structure_urls",
        lambda: ["http://s1", "http://s2", "http://s3"],
    )
    _install_pages(monkeypatch, [(1, "a")])
    monkeypatch.setattr(
        "app.core.document_qa.structure_pool.get_structure_pool",
        lambda: pool,
    )
    assert fetch_structure_pages("unused.pdf") is None
