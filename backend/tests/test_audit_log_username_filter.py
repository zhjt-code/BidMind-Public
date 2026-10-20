"""审计日志按用户名筛选的防御测试。

验证: username 走 AuditLog.username ILIKE, 与 keyword(详情) 互相独立;
空白用户名不产生条件, 避免误过滤。
"""

from app.services.audit_service import AuditService


def _filters(**overrides):
    params = {
        "category": None,
        "action": None,
        "resource_type": None,
        "user_id": None,
        "username": None,
        "keyword": None,
        "start_date": None,
        "end_date": None,
    }
    params.update(overrides)
    return AuditService._build_filters(**params)


def _compile(clause) -> str:
    return str(clause.compile(compile_kwargs={"literal_binds": True}))


def test_blank_username_does_not_add_filter():
    assert _filters() == []
    assert _filters(username="") == []
    assert _filters(username="   ") == []


def test_username_uses_ilike_on_username_column():
    clauses = _filters(username="  Alice  ")
    assert len(clauses) == 1
    sql = _compile(clauses[0])
    assert "username" in sql.lower()
    assert "detail" not in sql.lower()
    assert "%Alice%" in sql


def test_username_and_keyword_are_independent_and_filters():
    clauses = _filters(username="bob", keyword="删除")
    assert len(clauses) == 2
    compiled = [_compile(clause) for clause in clauses]
    assert any("username" in sql.lower() and "%bob%" in sql for sql in compiled)
    assert any("detail" in sql.lower() and "%删除%" in sql for sql in compiled)


def test_batch_filters_schema_accepts_username():
    from app.api.routers.audit_logs import AuditLogBatchFilters

    payload = AuditLogBatchFilters(username="carol", keyword="登录")
    dumped = payload.model_dump(exclude_none=True)
    assert dumped["username"] == "carol"
    assert dumped["keyword"] == "登录"
