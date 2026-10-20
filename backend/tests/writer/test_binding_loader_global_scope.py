""" 防御测试: 项目级 scope=global 无节点绑定进入生成消费。

风险映射: matcher 把全局约束以 outline_node_id=NULL, scope='global' 落库并标注
"内容生成阶段需消费", 但 load_bound_clauses 此前只查直接绑定 + 祖先节点绑定, 无任何
查询命中无节点绑定, 导致全局约束(含废标条款)从未进入生成上下文。
锁定:
- include_inherited=True: 加载项目级 global 无节点绑定并纳入 clause 消费, 按
 project_id 查询(db.get 命中), global 查询带 scope 过滤(orphan 不混入);
- include_inherited=False: 不查 project_id、不执行 global 查询, 仅返回直接绑定。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.writer.composer import binding_loader
from app.models.writing_binding import BindingStatus, BindingType


def _binding(clause_id, scope, *, node_id=None, status=BindingStatus.suggested):
    return SimpleNamespace(
        clause_id=clause_id, scope=scope, outline_node_id=node_id,
        status=status, binding_type=BindingType.clause_to_node, confidence=1.0,
    )


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _Lock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeDB:
    """按 SQL 特征分派返回值, 不依赖 execute 调用顺序。"""

    def __init__(self, *, direct, global_, node, captured):
        self._direct = direct
        self._global = global_
        self._node = node
        self._captured = captured
        self.get_calls = 0
        self.executed_sql: list[str] = []

    async def execute(self, statement, *a, **k):
        sql = str(statement).lower()
        self.executed_sql.append(sql)
        if "from writing_clauses" in sql:
            return _Result(
                [SimpleNamespace(id=i) for i in self._captured.get("ids", [])]
            )
        if "outline_node_id is null" in sql:
            return _Result(self._global)
        if "outline_node_id in" in sql:
            return _Result([])  # 本测试无祖先
        return _Result(self._direct)

    async def get(self, model, ident):
        self.get_calls += 1
        return self._node


def _make_composer(db, captured):
    composer = SimpleNamespace()
    composer.db = db
    composer._db_lock = _Lock()

    async def _no_ancestors(node_id):
        return []

    composer._load_ancestor_node_ids = _no_ancestors

    def _spy_collect(groups, attr):
        ids = binding_loader.collect_binding_targets(groups, attr)
        captured["ids"] = ids
        captured["groups"] = groups
        return ids

    composer._collect_binding_targets = _spy_collect
    return composer


@pytest.mark.asyncio
async def test_include_inherited_loads_project_global_no_node_bindings() -> None:
    captured: dict = {}
    db = _FakeDB(
        direct=[_binding(1, "exclusive", node_id=5)],
        global_=[_binding(10, "global", node_id=None)],
        node=SimpleNamespace(id=5, project_id=99),
        captured=captured,
    )
    composer = _make_composer(db, captured)

    clauses = await binding_loader.load_bound_clauses(
        composer, 5, include_inherited=True,
    )

    # 全局无节点约束进入消费
    assert 10 in captured["ids"]
    assert 1 in captured["ids"]
    assert {c.id for c in clauses} == {1, 10}
    # 按项目查询 project_id
    assert db.get_calls == 1
    # global 查询带 scope 过滤(scope=='global' 排除 orphan)
    global_sql = [s for s in db.executed_sql if "outline_node_id is null" in s]
    assert len(global_sql) == 1
    assert "scope" in global_sql[0]


@pytest.mark.asyncio
async def test_exclude_inherited_skips_global_query() -> None:
    captured: dict = {}
    db = _FakeDB(
        direct=[_binding(1, "exclusive", node_id=5)],
        global_=[_binding(10, "global", node_id=None)],
        node=SimpleNamespace(id=5, project_id=99),
        captured=captured,
    )
    composer = _make_composer(db, captured)

    clauses = await binding_loader.load_bound_clauses(
        composer, 5, include_inherited=False,
    )

    # 仅直接绑定, 不含全局约束
    assert captured["ids"] == [1]
    assert {c.id for c in clauses} == {1}
    assert len(captured["groups"]) == 1
    # 未查 project_id、未执行 global 查询
    assert db.get_calls == 0
    assert not any("outline_node_id is null" in s for s in db.executed_sql)
