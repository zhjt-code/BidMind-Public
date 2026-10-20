"""PromptRegistry 接入 helper + 跨进程热更新 + 任务快照 单元测试。

目的: 固化本轮 A (接入) + B (Pub/Sub 广播) + snapshot (任务级版本绑定) 的核心行为,
防止未来回归。全部使用内存 PromptRegistry 实例 + _CachedNode 注入, 不依赖 DB / Redis。

覆盖:
1) resolve_system_prompt / resolve_user_template 未命中 fallback 到代码默认
2) resolve_system_prompt / resolve_user_template 命中 Registry 返回 DB 内容
3) registry_first 两边 format 成功路径
4) registry_first format 失败回退代码默认 (KeyError 降级)
5) resolve_snapshot 返回 (sys_id, user_id) tuple 并预热 _version_cache
6) current_snapshot contextvar 生效: cache 被刷新后 get_prompt 仍返回绑定时版本
7) 清除 contextvar 后 get_prompt 返回最新 cache
8) install_task_snapshot 正确绑定 + bind_task_snapshot async ctx manager 正确 set/reset
9) publish_invalidate(None, ...) 不抛错; 成功路径 publish 正确 payload
10) _handle_invalidate_message 对非法 JSON / 未知类型安全降级
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.ai import prompt_registry as PR
from app.core.ai.prompt_registry import (
    INVALIDATE_CHANNEL,
    NodeMeta,
    PromptRegistry,
    _CachedNode,
    bind_task_snapshot,
    call_with_fallback,
    current_snapshot,
    install_task_snapshot,
    registry_first,
    resolve_system_prompt,
    resolve_user_template,
)


@pytest.fixture
def fresh_registry():
    """用隔离的 PromptRegistry 替换模块单例, 测试结束自动恢复。"""
    original = PR._registry
    new_reg = PromptRegistry()
    PR._registry = new_reg
    try:
        yield new_reg
    finally:
        PR._registry = original


def _inject_node(
    reg: PromptRegistry,
    node_key: str,
    system_text: str,
    user_text: str,
    system_version_id: int | None = None,
    user_version_id: int | None = None,
) -> None:
    reg._cache[node_key] = _CachedNode(
        node_id=len(reg._cache) + 1,
        meta=NodeMeta(node_key=node_key, name=node_key),
        system_text=system_text,
        user_text=user_text,
        system_version_id=system_version_id,
        user_version_id=user_version_id,
    )


# ═══════════════════════════════════════════════
# 1-2) resolve_* fallback 与命中
# ═══════════════════════════════════════════════


def test_resolve_system_prompt_fallback(fresh_registry: PromptRegistry) -> None:
    assert resolve_system_prompt("nonexistent.node", "CODE_SYS") == "CODE_SYS"


def test_resolve_user_template_fallback(fresh_registry: PromptRegistry) -> None:
    assert resolve_user_template("nonexistent.node", "CODE_USER_{a}") == "CODE_USER_{a}"


def test_resolve_prompts_hit_db(fresh_registry: PromptRegistry) -> None:
    _inject_node(
        fresh_registry, "bid_review.review_item", "DB_SYS", "DB_USER_{a}",
        system_version_id=11, user_version_id=22,
    )
    assert resolve_system_prompt("bid_review.review_item", "CODE_SYS") == "DB_SYS"
    assert resolve_user_template("bid_review.review_item", "CODE_USER") == "DB_USER_{a}"


# ═══════════════════════════════════════════════
# 3-4) registry_first 行为
# ═══════════════════════════════════════════════


def test_registry_first_success(fresh_registry: PromptRegistry) -> None:
    _inject_node(
        fresh_registry, "bid_review.review_item", "DB_SYS", "DB_USER_{a}",
        system_version_id=11, user_version_id=22,
    )
    sys_m, usr_m = registry_first(
        "bid_review.review_item", "CODE_SYS", "CODE_USER_{a}", a="hello"
    )
    assert sys_m == "DB_SYS"
    assert usr_m == "DB_USER_hello"


def test_registry_first_fallback_on_missing(fresh_registry: PromptRegistry) -> None:
    sys_f, usr_f = registry_first(
        "nonexistent.node", "FB_SYS", "FB_USER_{a}", a="world"
    )
    assert sys_f == "FB_SYS"
    assert usr_f == "FB_USER_world"


def test_registry_first_fallback_on_format_error(
    fresh_registry: PromptRegistry,
) -> None:
    # DB user 要求 {missing_key}, 但调用方只传 a; 应回退代码默认并成功 format。
    _inject_node(
        fresh_registry, "bid_review.review_item", "DB_SYS", "DB_USER_{missing_key}",
    )
    sys_m, usr_m = registry_first(
        "bid_review.review_item", "CODE_SYS", "CODE_USER_{a}", a="hello"
    )
    assert sys_m == "DB_SYS"  # system 无 {missing_key}, 不触发 format 失败
    # user 的 DB 模板 format 失败, 回退代码默认模板并成功 format
    assert usr_m == "CODE_USER_hello"


# ═══════════════════════════════════════════════
# 5) resolve_snapshot 预热 version_cache
# ═══════════════════════════════════════════════


def test_resolve_snapshot_returns_tuple_and_prewarms_cache(
    fresh_registry: PromptRegistry,
) -> None:
    _inject_node(
        fresh_registry, "bid_review.review_item", "DB_SYS", "DB_USER_{a}",
        system_version_id=11, user_version_id=22,
    )
    snap = fresh_registry.resolve_snapshot(
        ["bid_review.review_item", "nonexistent.node"]
    )
    assert snap == {"bid_review.review_item": (11, 22)}
    # _version_cache 应已预热
    assert (
        fresh_registry._version_cache[("bid_review.review_item", 11, "system")]
        == "DB_SYS"
    )
    assert (
        fresh_registry._version_cache[("bid_review.review_item", 22, "user")]
        == "DB_USER_{a}"
    )


# ═══════════════════════════════════════════════
# 6-7) current_snapshot contextvar 生效
# ═══════════════════════════════════════════════


def test_current_snapshot_locks_version_against_cache_refresh(
    fresh_registry: PromptRegistry,
) -> None:
    _inject_node(
        fresh_registry, "bid_review.review_item", "DB_SYS", "DB_USER_{a}",
        system_version_id=11, user_version_id=22,
    )
    snap = fresh_registry.resolve_snapshot(["bid_review.review_item"])
    token = current_snapshot.set(snap)
    try:
        # 模拟任务中途 cache 被 refresh 到新版本
        fresh_registry._cache["bid_review.review_item"] = _CachedNode(
            node_id=1,
            meta=NodeMeta(node_key="bid_review.review_item", name="x"),
            system_text="NEW_SYS",
            user_text="NEW_USER_{a}",
            system_version_id=99,
            user_version_id=100,
        )
        resolved = fresh_registry.get_prompt("bid_review.review_item")
        # 仍然返回绑定时版本
        assert resolved.system_message == "DB_SYS"
        assert resolved.user_template == "DB_USER_{a}"
    finally:
        current_snapshot.reset(token)

    # 清除 snapshot 后返回最新 cache
    resolved2 = fresh_registry.get_prompt("bid_review.review_item")
    assert resolved2.system_message == "NEW_SYS"
    assert resolved2.user_template == "NEW_USER_{a}"


# ═══════════════════════════════════════════════
# 8) install_task_snapshot / bind_task_snapshot
# ═══════════════════════════════════════════════


def test_install_task_snapshot_binds_non_empty(
    fresh_registry: PromptRegistry,
) -> None:
    _inject_node(
        fresh_registry, "bid_review.review_item", "SYS", "USER",
        system_version_id=1, user_version_id=2,
    )
    token = install_task_snapshot("bid_review")
    assert token is not None
    snap = current_snapshot.get()
    assert snap is not None and "bid_review.review_item" in snap
    current_snapshot.reset(token)


def test_install_task_snapshot_unknown_flow_returns_none(
    fresh_registry: PromptRegistry,
) -> None:
    assert install_task_snapshot("nonexistent_flow") is None


def test_bind_task_snapshot_async_context_manager(
    fresh_registry: PromptRegistry,
) -> None:
    _inject_node(
        fresh_registry, "writing.hard_clause", "SYS", "USER",
        system_version_id=1, user_version_id=2,
    )

    async def _run():
        async with bind_task_snapshot("writing"):
            assert current_snapshot.get() is not None
        # 退出 ctx 后 contextvar 已 reset
        assert current_snapshot.get() is None

    asyncio.run(_run())


# ═══════════════════════════════════════════════
# 9) publish_invalidate
# ═══════════════════════════════════════════════


def test_publish_invalidate_none_redis_safe(
    fresh_registry: PromptRegistry,
) -> None:
    # Redis 不可用场景: 不抛错
    async def _run():
        await fresh_registry.publish_invalidate(None, "any.node")

    asyncio.run(_run())


def test_publish_invalidate_success_payload(
    fresh_registry: PromptRegistry,
) -> None:
    class _FakeRedis:
        def __init__(self):
            self.published: list[tuple[str, str]] = []

        async def publish(self, channel: str, msg: str) -> None:
            self.published.append((channel, msg))

    fake = _FakeRedis()

    async def _run():
        await fresh_registry.publish_invalidate(fake, "bid_review.review_item")
        await fresh_registry.publish_invalidate(fake, None)

    asyncio.run(_run())

    assert len(fake.published) == 2
    ch1, raw1 = fake.published[0]
    assert ch1 == INVALIDATE_CHANNEL
    assert json.loads(raw1) == {
        "type": "refresh",
        "node_key": "bid_review.review_item",
    }
    ch2, raw2 = fake.published[1]
    assert ch2 == INVALIDATE_CHANNEL
    assert json.loads(raw2) == {"type": "reload"}


def test_start_subscriber_uses_dedicated_pubsub_client(
    fresh_registry: PromptRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakePubSub:
        def __init__(self) -> None:
            self.closed = False
            self.subscribed: list[str] = []

        async def subscribe(self, channel: str) -> None:
            self.subscribed.append(channel)

        async def get_message(self, **_: object) -> None:
            await asyncio.sleep(0.01)
            return None

        async def unsubscribe(self, channel: str) -> None:
            assert channel == INVALIDATE_CHANNEL

        async def aclose(self) -> None:
            self.closed = True

    class _FakeRedis:
        def __init__(self) -> None:
            self.pubsubs: list[_FakePubSub] = []
            self.closed = False

        def pubsub(self) -> _FakePubSub:
            pubsub = _FakePubSub()
            self.pubsubs.append(pubsub)
            return pubsub

        async def aclose(self) -> None:
            self.closed = True

    dedicated = _FakeRedis()
    fallback = _FakeRedis()

    async def _fake_create_pubsub_client() -> _FakeRedis:
        return dedicated

    async def _run() -> None:
        import app.core.redis as redis_mod

        monkeypatch.setattr(
            redis_mod,
            "create_redis_pubsub_client",
            _fake_create_pubsub_client,
        )
        await fresh_registry.start_invalidate_subscriber(fallback)
        await asyncio.sleep(0.02)
        assert fresh_registry._invalidate_sub_client is dedicated
        assert fresh_registry._invalidate_sub_owns_client is True
        assert dedicated.pubsubs
        assert dedicated.pubsubs[0].subscribed == [INVALIDATE_CHANNEL]
        await fresh_registry.stop_invalidate_subscriber()

    asyncio.run(_run())

    assert dedicated.closed is True
    assert fallback.closed is False


# ═══════════════════════════════════════════════
# 10) _handle_invalidate_message 容错
# ═══════════════════════════════════════════════


def test_handle_invalidate_message_safe_on_invalid_input(
    fresh_registry: PromptRegistry,
) -> None:
    async def _run():
        # 非法 JSON -> WARNING, 不抛
        await fresh_registry._handle_invalidate_message({"data": "not-json"})
        # 未知类型 -> DEBUG, 不抛
        await fresh_registry._handle_invalidate_message(
            {"data": json.dumps({"type": "unknown_type"})}
        )
        # 空 data -> WARNING, 不抛
        await fresh_registry._handle_invalidate_message({})

    asyncio.run(_run())


# ═══════════════════════════════════════════════
# 11) call_with_fallback: 位置参数 <-> 命名占位符自动映射 (根治)
# ═══════════════════════════════════════════════


def test_call_with_fallback_positional_binds_to_named_placeholder(
    fresh_registry: PromptRegistry,
) -> None:
    """核心回归场景: Registry user_template 含命名占位符 {node_title},
 业务调用传位置参数 -> call_with_fallback 应自动按 code_fn 签名将位置参数
 映射成 kwargs 再 format, 不再走 的静默 fallback 路径。"""
    def _code_summarize(node_title: str, content_preview: str) -> tuple[str, str]:
        return "CODE_SYS", f"CODE_USER:{node_title}:{content_preview[:20]}"

    _inject_node(
        fresh_registry,
        "writing.section_summary",
        "DB_SYS",
        "章节:{node_title} 内容:{content_preview}",
    )

    # 位置参数调用 (历史 wrapper 的典型场景 -- 现场)
    sys_msg, user_msg = call_with_fallback(
        "writing.section_summary", _code_summarize, "技术方案", "实施内容预览"
    )
    assert sys_msg == "DB_SYS"
    assert user_msg == "章节:技术方案 内容:实施内容预览"


def test_call_with_fallback_mixed_args_kwargs_bind(
    fresh_registry: PromptRegistry,
) -> None:
    """混合位置 + 关键字调用场景: inspect.bind 应按 code_fn 签名正确合并。"""
    def _code_fill(node_title: str, target_word_count: int, extra: str = "") -> tuple[str, str]:
        return "CS", f"CU:{node_title}:{target_word_count}:{extra}"

    _inject_node(
        fresh_registry,
        "writing.data_fill",
        "DBSYS",
        "标题:{node_title} 字数:{target_word_count} 额外:{extra}",
    )

    sys_msg, user_msg = call_with_fallback(
        "writing.data_fill", _code_fill, "人员表", target_word_count=500, extra="清单"
    )
    assert sys_msg == "DBSYS"
    assert user_msg == "标题:人员表 字数:500 额外:清单"


def test_call_with_fallback_formats_registry_system_message(
    fresh_registry: PromptRegistry,
) -> None:
    """: Registry system_message 中的动态占位符也必须按业务参数渲染。"""
    def _code_free_writing(
        node_title: str,
        industry_profile_excerpt: str,
    ) -> tuple[str, str]:
        return (
            f"CODE_SYS:{industry_profile_excerpt}",
            f"CODE_USER:{node_title}",
        )

    _inject_node(
        fresh_registry,
        "writing.free_writing",
        "DB_SYS_PROFILE:{industry_profile_excerpt}",
        "DB_USER_TITLE:{node_title}",
    )

    sys_msg, user_msg = call_with_fallback(
        "writing.free_writing",
        _code_free_writing,
        node_title="技术方案",
        industry_profile_excerpt="PROFILE_SENTINEL",
    )

    assert sys_msg == "DB_SYS_PROFILE:PROFILE_SENTINEL"
    assert "{industry_profile_excerpt}" not in sys_msg
    assert user_msg == "DB_USER_TITLE:技术方案"


def test_call_with_fallback_missing_in_registry_uses_code_default(
    fresh_registry: PromptRegistry,
) -> None:
    """Registry 未命中场景, 走 code_fn 原样调用 (保持 wrapper 原语义)。"""
    def _code_fn(title: str, count: int) -> tuple[str, str]:
        return "FB_SYS", f"FB_USER:{title}:{count}"

    sys_msg, user_msg = call_with_fallback(
        "writing.nonexistent_node", _code_fn, "T1", count=9
    )
    assert sys_msg == "FB_SYS"
    assert user_msg == "FB_USER:T1:9"


def test_call_with_fallback_bad_registry_template_falls_back(
    fresh_registry: PromptRegistry,
) -> None:
    """Registry 命中但模板用了 code_fn 签名里没有的变量 -> 回退代码默认。"""
    def _code_fn(x: str) -> tuple[str, str]:
        return "CS", f"CU:{x}"

    _inject_node(
        fresh_registry,
        "writing.bad_tpl_node",
        "DB_SYS",
        "{x} {unknown_extra}",
    )

    sys_msg, user_msg = call_with_fallback(
        "writing.bad_tpl_node", _code_fn, x="hello"
    )
    # KeyError 'unknown_extra' -> fallback 到代码默认
    assert sys_msg == "CS"
    assert user_msg == "CU:hello"


def test_call_with_fallback_incompatible_signature_uses_code_fn(
    fresh_registry: PromptRegistry,
) -> None:
    """调用者传的参数与 code_fn 签名不匹配时, 让 code_fn 抛出真实错误。"""
    def _code_fn(required_arg: str) -> tuple[str, str]:
        return "S", f"U:{required_arg}"

    _inject_node(
        fresh_registry,
        "writing.sig_mismatch",
        "DB_SYS",
        "{required_arg}",
    )

    # 缺失 required_arg: inspect.bind 抛 TypeError -> 回落到 code_fn 执行
    with pytest.raises(TypeError):
        call_with_fallback("writing.sig_mismatch", _code_fn)
