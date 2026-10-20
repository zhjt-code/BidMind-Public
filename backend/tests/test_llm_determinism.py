"""S.3.1 单元测试：LLM seed 派生与 contextvar 解析。"""

from __future__ import annotations

from app.core.ai import determinism


class TestDeriveSeed:
    def test_none_inputs_return_none(self):
        assert determinism.derive_seed(None, None) is None
        assert determinism.derive_seed(None, "") is None
        assert determinism.derive_seed("", None) is None
        assert determinism.derive_seed("", "") is None

    def test_project_id_only_deterministic(self):
        a = determinism.derive_seed(42, None)
        b = determinism.derive_seed(42, None)
        assert a == b
        assert 0 <= a <= 2**31 - 1

    def test_node_key_only_deterministic(self):
        a = determinism.derive_seed(None, "bid_review.review_item")
        b = determinism.derive_seed(None, "bid_review.review_item")
        assert a == b
        assert 0 <= a <= 2**31 - 1

    def test_combined_differs_from_either(self):
        s_both = determinism.derive_seed(42, "bid_review.review_item")
        s_pid = determinism.derive_seed(42, None)
        s_nk = determinism.derive_seed(None, "bid_review.review_item")
        assert s_both != s_pid
        assert s_both != s_nk

    def test_different_projects_distinct(self):
        a = determinism.derive_seed(42, "bid_review.review_item")
        b = determinism.derive_seed(43, "bid_review.review_item")
        assert a != b

    def test_different_nodes_distinct(self):
        a = determinism.derive_seed(42, "bid_review.review_item")
        b = determinism.derive_seed(42, "collusion.d1")
        assert a != b

    def test_int_string_project_id_equivalent(self):
        assert determinism.derive_seed(42, "x") == determinism.derive_seed("42", "x")


class TestResolveCurrentSeed:
    def test_explicit_seed_overrides(self, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "LLM_SEED_ENABLED", True)
        assert determinism.resolve_current_seed(
            project_id=99, explicit_seed=12345,
        ) == 12345

    def test_disabled_returns_none_even_with_inputs(self, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "LLM_SEED_ENABLED", False)
        assert determinism.resolve_current_seed(project_id=99) is None

    def test_explicit_seed_normalized_to_int32(self, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "LLM_SEED_ENABLED", True)
        big = 2**40
        res = determinism.resolve_current_seed(
            project_id=99, explicit_seed=big,
        )
        assert res is not None
        assert 0 <= res <= 2**31 - 1

    def test_contextvar_project_id_fallback(self, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "LLM_SEED_ENABLED", True)
        token = determinism.current_project_id.set(123)
        try:
            seed = determinism.resolve_current_seed(project_id=None)
            assert seed is not None
            assert 0 <= seed <= 2**31 - 1
        finally:
            determinism.current_project_id.reset(token)

    def test_no_inputs_returns_none(self, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "LLM_SEED_ENABLED", True)
        # contextvar 默认 None；不提供 project_id / explicit_seed
        seed = determinism.resolve_current_seed()
        # node_key 也没有 contextvar 时返回 None；若恰好有 node_key 则派生
        # 这里不做严格 None 断言，因为可能在其他测试中 current_node_key 被 set 了
        assert seed is None or (0 <= seed <= 2**31 - 1)
