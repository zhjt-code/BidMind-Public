"""LlmCenterService 子模块化（R2-14）Phase 1 快照契约测试

锁定 R2-14 拆分前后必须保持稳定的契约，防止子服务拆分破坏 API router 调用
方与现有 3 个 integration spec 的依赖：

A. 模块级符号导出（LlmCenterService 类 + _broadcast_invalidate async 函数）
B. LlmCenterService.__init__ 接受单参数 db + 7 个 sub-service 属性（双形态契约）
C. 24 个公共方法名 frozenset byte-equal（拆分后保留 thin forwarder）
D. _broadcast_invalidate 在 publish / rollback / update_node_config 三处源码锚点
E. 批量编排链路 _collect_pending_nodes 在 batch_validate / batch_validate_and_publish 源码锚点
F. audit_log 调用点（10 个 action 字面量保留）+ category="llm_center" 锚点
"""

from __future__ import annotations

import inspect
from typing import get_type_hints
from unittest.mock import MagicMock

import pytest

from app.services import llm_center_service as llm_center_module
from app.services.llm_center_service import (
    LlmCenterService,
    _broadcast_invalidate,
)


# ---------------------------------------------------------------------------
# A. 模块级符号导出（拆分后 llm_center_service.py 必须保留这些符号）
# ---------------------------------------------------------------------------


class TestModuleLevelSymbols:
    """A 类：模块级符号导出契约。"""

    def test_module_exports_required_top_level_names(self):
        """llm_center_service 模块顶层必须保留 LlmCenterService + _broadcast_invalidate。"""
        required = {"LlmCenterService", "_broadcast_invalidate"}
        missing = required - set(vars(llm_center_module).keys())
        assert not missing, f"llm_center_service 模块顶层缺失符号: {missing}"

    def test_llm_center_service_is_class(self):
        """LlmCenterService 必须是可实例化的 class。"""
        assert inspect.isclass(LlmCenterService)

    def test_broadcast_invalidate_is_async_function(self):
        """_broadcast_invalidate 必须是 async 函数（被 routers/llm_center.py:249 直接 import）。"""
        assert inspect.iscoroutinefunction(_broadcast_invalidate), (
            "_broadcast_invalidate 必须保持为 async 函数，"
            "routers/llm_center.py:249 通过 `from app.services.llm_center_service import _broadcast_invalidate` 使用"
        )

    def test_broadcast_invalidate_signature_accepts_optional_str(self):
        """_broadcast_invalidate(node_key: str | None) 签名必须保留。"""
        sig = inspect.signature(_broadcast_invalidate)
        params = list(sig.parameters.values())
        assert len(params) == 1, "签名必须为单参数"
        assert params[0].name == "node_key", "参数名必须为 node_key"


# ---------------------------------------------------------------------------
# B. LlmCenterService.__init__ 组合契约（双形态：拆分前/拆分后均通过）
# ---------------------------------------------------------------------------


class TestServiceInit:
    """B 类：LlmCenterService.__init__ 签名 + sub-service 属性双形态契约。"""

    def test_init_accepts_single_db_param(self):
        """LlmCenterService.__init__ 必须接受单参数 db: AsyncSession。"""
        sig = inspect.signature(LlmCenterService.__init__)
        params = list(sig.parameters.values())
        assert len(params) == 2, "__init__ 必须为 (self, db) 双参数"
        assert params[1].name == "db", "第二个参数名必须为 db"

    def test_init_sub_services_progressive_composition(self):
        """Sub-service 属性渐进式组合契约（R2-14 Step 3-9 期间渐进推进）。

        - 拆分前（Step 1-2）：全部 7 个 sub-service 不存在
        - 拆分中（Step 3-8）：已迁出的 sub-service 必须存在且类型正确
        - 拆分完成（Step 9）：7 个 sub-service 全部存在
        - 每个已迁出的 sub-service 必须持有 .db == service.db 引用（避免 DB 上下文漂移）
        """
        from app.services.llm_center.topology_service import FlowTopologyService
        from app.services.llm_center.node_detail_service import NodeDetailService
        from app.services.llm_center.draft_service import DraftService
        from app.services.llm_center.validate_service import ValidateService
        from app.services.llm_center.version_service import VersionService
        from app.services.llm_center.trace_service import TraceService
        from app.services.llm_center.batch_service import BatchService

        fake_db = MagicMock()
        service = LlmCenterService(db=fake_db)

        # 子服务属性 → 期望类型映射（R2-14 完整 7 个 sub-service 已组合）
        expected_types = {
            "topology": FlowTopologyService,
            "node_detail": NodeDetailService,
            "draft": DraftService,
            "validate": ValidateService,
            "version": VersionService,
            "trace": TraceService,
            "batch": BatchService,
        }
        for attr_name, expected_cls in expected_types.items():
            sub_service = getattr(service, attr_name, None)
            assert sub_service is not None, (
                f"R2-14 sub-service '{attr_name}' 已迁出但未在 __init__ 中组合"
            )
            assert isinstance(sub_service, expected_cls), (
                f"service.{attr_name} 类型必须为 {expected_cls.__name__}, "
                f"实际为 {type(sub_service).__name__}"
            )
            assert sub_service.db is fake_db, (
                f"service.{attr_name}.db 必须与主 service.db 同源（避免 DB 上下文漂移）"
            )


# ---------------------------------------------------------------------------
# C. 24 个公共方法名 frozenset byte-equal（拆分后保留 thin forwarder）
# ---------------------------------------------------------------------------


# 拆分前 LlmCenterService 24 个公共方法（按字母序）
EXPECTED_PUBLIC_METHODS: frozenset[str] = frozenset({
    "archive_old_traces",
    "batch_publish_passed",
    "batch_validate",
    "batch_validate_and_publish",
    "create_or_update_draft",
    "create_param",
    "diff_versions",
    "discard_draft",
    "get_flow_topology",
    "get_node_detail",
    "get_node_params",
    "get_node_versions",
    "impact_analysis",
    "list_flows",
    "publish",
    "query_execution_traces",
    "query_publish_history",
    "record_execution",
    "rollback",
    "set_trace_mode",
    "test_run",
    "update_node_config",
    "update_param",
    "validate_draft",
})


class TestPublicMethodSet:
    """C 类：公共方法名 frozenset byte-equal 契约。"""

    def test_public_methods_byte_equal(self):
        """24 个公共方法集合在拆分前后必须 byte-equal。"""
        actual = frozenset(
            name for name in dir(LlmCenterService)
            if not name.startswith("_") and callable(getattr(LlmCenterService, name))
        )
        missing = EXPECTED_PUBLIC_METHODS - actual
        extra = actual - EXPECTED_PUBLIC_METHODS
        assert not missing, f"R2-14 拆分丢失公共方法: {missing}"
        assert not extra, f"R2-14 拆分新增公共方法（不允许）: {extra}"
        assert actual == EXPECTED_PUBLIC_METHODS

    def test_all_public_methods_are_async(self):
        """24 个公共方法必须全部为 async（DB 操作）。"""
        for name in EXPECTED_PUBLIC_METHODS:
            method = getattr(LlmCenterService, name)
            assert inspect.iscoroutinefunction(method), (
                f"{name} 必须为 async 方法，"
                f"被 API router 通过 await service.{name}(...) 调用"
            )


# ---------------------------------------------------------------------------
# Helper: 递归追踪 thin forwarder → sub-service 源码
# ---------------------------------------------------------------------------


def _resolve_method_source(method_name: str) -> str:
    """获取主类 method 或其委托 sub-service method 的源码。

    R2-14 拆分进行中：主类 method 可能是 thin forwarder（仅 return await
    self.sub.method(...)），audit_log / 内部 helper 字面量已迁移到 sub-service
    内部。本 helper 自动追踪 sub-service 委托链，递归拼接源码用于字面量锚点
    检查（D 类 / E 类 / F 类共用）。
    """
    fake_db = MagicMock()
    service = LlmCenterService(db=fake_db)
    main_method = getattr(LlmCenterService, method_name)
    main_src = inspect.getsource(main_method)

    # 已迁出的 sub-service 列表
    sub_attrs = ["topology", "node_detail", "draft", "validate", "version", "batch", "trace"]
    extra_sources: list[str] = []
    for attr in sub_attrs:
        sub_service = getattr(service, attr, None)
        if sub_service is None:
            continue
        sub_method = getattr(type(sub_service), method_name, None)
        if sub_method is not None and callable(sub_method):
            extra_sources.append(inspect.getsource(sub_method))
    return main_src + "\n" + "\n".join(extra_sources)


# ---------------------------------------------------------------------------
# D. _broadcast_invalidate 调用链锚点（publish / rollback / update_node_config）
# ---------------------------------------------------------------------------


class TestBroadcastInvalidateCallChain:
    """D 类：_broadcast_invalidate 调用链锚点契约（跨进程热更新链路防御）。"""

    def test_publish_source_calls_broadcast_invalidate(self):
        """publish 方法源码必须含 _broadcast_invalidate(node_key) 字面量。"""
        src = _resolve_method_source("publish")
        assert "_broadcast_invalidate(node_key)" in src, (
            "publish 必须在 active 切换后调用 _broadcast_invalidate(node_key)"
            "（跨进程通知 worker reload prompt， 防御）"
        )

    def test_rollback_source_calls_broadcast_invalidate(self):
        """rollback 方法源码必须含 _broadcast_invalidate(node_key) 字面量。"""
        src = _resolve_method_source("rollback")
        assert "_broadcast_invalidate(node_key)" in src, (
            "rollback 必须在版本切换后调用 _broadcast_invalidate(node_key)"
        )

    def test_update_node_config_source_calls_broadcast_invalidate(self):
        """update_node_config 方法源码必须含 _broadcast_invalidate(node_key) 字面量。"""
        src = _resolve_method_source("update_node_config")
        assert "_broadcast_invalidate(node_key)" in src, (
            "update_node_config 必须在 temperature/max_tokens 更新后广播失效"
        )


# ---------------------------------------------------------------------------
# E. 批量编排链路 _collect_pending_nodes 锚点
# ---------------------------------------------------------------------------


class TestBatchOrchestration:
    """E 类：批量校验/发布编排链路 _collect_pending_nodes 共享 helper 锚点。

    递归追踪 thin forwarder → sub-service 源码，兼容拆分前后 ：
      - 拆分前：主类 batch_xxx 直接含 _collect_pending_nodes 字面量
      - 拆分后：主类 thin forwarder + BatchService.batch_xxx 含字面量
    """

    def test_batch_validate_uses_collect_pending_nodes(self):
        """batch_validate 必须通过 _collect_pending_nodes(...) 获取待处理节点。"""
        src = _resolve_method_source("batch_validate")
        assert "_collect_pending_nodes" in src, (
            "batch_validate 必须调用 _collect_pending_nodes 获取待处理节点列表"
        )

    def test_batch_validate_and_publish_uses_collect_pending_nodes(self):
        """batch_validate_and_publish 必须通过 _collect_pending_nodes(...) 获取待处理节点。"""
        src = _resolve_method_source("batch_validate_and_publish")
        assert "_collect_pending_nodes" in src, (
            "batch_validate_and_publish 必须调用 _collect_pending_nodes 获取待处理节点列表"
        )

    def test_batch_validate_and_publish_syncs_registry_before_collecting(self):
        """校验并发布必须先同步代码注册表，再收集待发布节点。

 防御：代码新增 NodeDef/PARAM_DEFS 时，若按钮只扫描已有
 draft/validated，新节点不会进入本次闭环，运行时仍会出现未注册 WARNING。
 """
        from app.services.llm_center.batch_service import BatchService

        src = inspect.getsource(BatchService.batch_validate_and_publish)
        sync_pos = src.find("_registry_sync_before_collecting")
        collect_pos = src.find("_collect_pending_nodes")
        assert sync_pos != -1, (
            "batch_validate_and_publish 必须先执行 registry sync helper，"
            "把代码新增节点/参数/Prompt 版本同步到 DB"
        )
        assert collect_pos != -1
        assert sync_pos < collect_pos, (
            "registry sync helper 必须发生在 _collect_pending_nodes 之前，"
            "否则代码新增 source=code 草稿不会被本轮校验发布扫描到"
        )

        helper_src = inspect.getsource(BatchService._registry_sync_before_collecting)
        assert "registry_sync" in helper_src, (
            "registry sync helper 必须调用 PromptRegistry.registry_sync"
        )
        assert "_broadcast_invalidate(None)" in helper_src, (
            "registry_sync 后必须广播全量 reload，确保 worker/API 进程缓存看到新增节点"
        )

    def test_batch_publish_passed_does_not_use_collect_pending_nodes(self):
        """batch_publish_passed 接受 node_keys 直接发布，不调 _collect_pending_nodes。"""
        src = _resolve_method_source("batch_publish_passed")
        assert "_collect_pending_nodes" not in src, (
            "batch_publish_passed 接受显式 node_keys 参数，"
            "不应调用 _collect_pending_nodes（语义边界保护）"
        )


# ---------------------------------------------------------------------------
# F. audit_log 调用契约（10 个 action 字面量 + category="llm_center" 锚点）
# ---------------------------------------------------------------------------


# 防御：写操作必须留审计痕迹
EXPECTED_AUDIT_ACTIONS_PER_METHOD: dict[str, str] = {
    "discard_draft": '"discard_draft"',
    "create_or_update_draft": '"save_draft"',
    "validate_draft": '"validate_draft"',
    "publish": '"publish_prompt"',
    "rollback": '"rollback_prompt"',
    "test_run": '"test_run"',
    "batch_validate": '"batch_validate"',
    "batch_validate_and_publish": '"batch_validate_publish"',
    "batch_publish_passed": '"batch_publish_passed"',
}


class TestAuditLogActions:
    """F 类：audit_log 调用契约（防御）。

 拆分进行中允许 audit_log 字面量在主类 thin forwarder 或 sub-service
 内部任一位置存在；helper `_resolve_method_source` 自动追踪委托链。
 """

    @pytest.mark.parametrize("method_name,expected_action", EXPECTED_AUDIT_ACTIONS_PER_METHOD.items())
    def test_method_audit_action_literal(self, method_name: str, expected_action: str):
        """每个写操作方法（主类或 sub-service）源码必须含 action=<expected_action> 字面量。"""
        src = _resolve_method_source(method_name)
        assert f"action={expected_action}" in src, (
            f"{method_name} 必须保留 audit_log action={expected_action} 字面量"
            "（防御：写操作必须留审计痕迹）"
        )

    def test_methods_use_llm_center_category(self):
        """所有写操作的 audit_log 调用必须 category=\"llm_center\"。"""
        for method_name in EXPECTED_AUDIT_ACTIONS_PER_METHOD:
            src = _resolve_method_source(method_name)
            assert 'category="llm_center"' in src, (
                f"{method_name} audit_log 调用必须 category=\"llm_center\""
            )
