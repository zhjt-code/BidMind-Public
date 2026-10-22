"""后台任务取消工具契约测试。"""

from __future__ import annotations

import pytest

from app.core.arq_runtime import ProjectCancelledError


def test_rethrow_project_cancelled_raises_first_cancelled_error() -> None:
    from app.core.tasks.cancellation import rethrow_project_cancelled

    cancel = ProjectCancelledError(42, detail="unit")
    with pytest.raises(ProjectCancelledError) as exc:
        rethrow_project_cancelled([RuntimeError("other"), cancel])

    assert exc.value is cancel


def test_make_project_cancel_check_delegates_to_runtime(monkeypatch) -> None:
    from app.core.tasks.cancellation import make_project_cancel_check

    checked: list[int] = []

    def fake_raise(project_id: int) -> None:
        checked.append(project_id)
        raise ProjectCancelledError(project_id)

    monkeypatch.setattr("app.core.tasks.cancellation.raise_if_project_cancelled", fake_raise)

    check = make_project_cancel_check(77)
    with pytest.raises(ProjectCancelledError):
        check()

    assert checked == [77]
