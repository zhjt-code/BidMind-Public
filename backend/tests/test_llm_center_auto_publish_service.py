import pytest


class _FakeBatchService:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls: list[dict] = []

    async def batch_validate_and_publish(
        self,
        scope: str,
        flow_key: str | None,
        include_technical: bool,
        username: str,
        change_note: str | None = None,
    ) -> dict:
        self.calls.append({
            "scope": scope,
            "flow_key": flow_key,
            "include_technical": include_technical,
            "username": username,
            "change_note": change_note,
        })
        return self.result


@pytest.mark.asyncio
async def test_auto_publish_uses_global_pending_scope_and_technical_nodes() -> None:
    from app.services.llm_center.auto_publish_service import auto_publish_code_registry

    fake = _FakeBatchService({
        "summary": {
            "total": 2,
            "validated_passed": 2,
            "validated_failed": 0,
            "published": 2,
        },
        "items": [
            {"node_key": "writing.hard_clause", "status": "published"},
            {"node_key": "judge.writer", "status": "published"},
        ],
    })

    report = await auto_publish_code_registry(
        db=object(),
        username="deploy:auto",
        service_factory=lambda _db: fake,
    )

    assert fake.calls == [{
        "scope": "all_pending",
        "flow_key": None,
        "include_technical": True,
        "username": "deploy:auto",
        "change_note": "部署后自动同步校验并发布代码注册源",
    }]
    assert report["ok"] is True
    assert report["summary"]["published"] == 2


@pytest.mark.asyncio
async def test_auto_publish_reports_validation_failure_without_success() -> None:
    from app.services.llm_center.auto_publish_service import auto_publish_code_registry

    fake = _FakeBatchService({
        "summary": {
            "total": 2,
            "validated_passed": 1,
            "validated_failed": 1,
            "published": 0,
        },
        "items": [
            {"node_key": "writing.hard_clause", "status": "validated"},
            {
                "node_key": "writing.structure_clause",
                "status": "validation_failed",
                "error_message": "新增未定义占位符",
            },
        ],
    })

    report = await auto_publish_code_registry(
        db=object(),
        username="deploy:auto",
        service_factory=lambda _db: fake,
    )

    assert report["ok"] is False
    assert report["failed_items"] == [{
        "node_key": "writing.structure_clause",
        "status": "validation_failed",
        "error_message": "新增未定义占位符",
    }]


def test_assert_auto_publish_success_raises_for_failed_report() -> None:
    from app.services.llm_center.auto_publish_service import (
        AutoPublishCodeRegistryError,
        assert_auto_publish_success,
    )

    with pytest.raises(AutoPublishCodeRegistryError) as exc:
        assert_auto_publish_success({
            "ok": False,
            "summary": {"total": 1, "validated_failed": 1, "published": 0},
            "failed_items": [
                {
                    "node_key": "writing.structure_clause",
                    "status": "validation_failed",
                    "error_message": "新增未定义占位符",
                },
            ],
        })

    assert "writing.structure_clause" in str(exc.value)
