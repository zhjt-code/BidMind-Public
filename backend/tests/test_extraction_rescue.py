"""防御测试: 招标要求提取补救机制 

覆盖:
1. Schema 对非法 category 的拒绝行为
2. _normalize_raw_item 归一化逻辑
3. _parse_items_with_salvage 逐条 salvage
4. _infer_category_from_content 关键词推断
5. _max_consecutive_run 连续页簇检测
"""

import os
import sys

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pydantic import ValidationError

from app.schemas.requirement import ExtractedRequirement


class TestSchemaValidation:
    def test_valid_categories_accepted(self):
        for cat in ("QUAL", "TECH", "COMM", "PERF", "STAFF", "FORMAT"):
            r = ExtractedRequirement(
                category=cat,
                description=f"valid {cat} requirement test",
                is_mandatory=True,
                keywords=["test"],
            )
            assert r.category == cat

    def test_generic_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            ExtractedRequirement(
                category="GENERIC",
                description="should be rejected",
                is_mandatory=True,
            )
        assert "literal_error" in str(exc_info.value)

    def test_other_invalid_categories_rejected(self):
        for bad_cat in ("OTHER", "GENERAL", "综合", "unknown"):
            with pytest.raises(ValidationError):
                ExtractedRequirement(
                    category=bad_cat,
                    description="should be rejected too",
                    is_mandatory=True,
                )

    def test_optional_fields_default_none(self):
        r = ExtractedRequirement(
            category="TECH",
            description="no topic or subject_scope",
            is_mandatory=False,
            keywords=["test"],
        )
        assert r.topic is None
        assert r.subject_scope is None
        assert r.canonical_key is None


class TestNormalizeRawItem:
    @pytest.fixture
    def extractor(self):
        from unittest.mock import AsyncMock

        from app.core.extractor.tender_extractor import TenderExtractor
        fake_db = AsyncMock()
        return TenderExtractor(db=fake_db)

    def test_generic_mapped_to_tech(self, extractor):
        item = {"category": "GENERIC", "description": "test", "is_mandatory": True}
        result = extractor._normalize_raw_item(item)
        assert result["category"] == "TECH"

    def test_valid_category_unchanged(self, extractor):
        item = {"category": "QUAL", "description": "test", "is_mandatory": True}
        result = extractor._normalize_raw_item(item)
        assert result["category"] == "QUAL"

    def test_case_insensitive_mapping(self, extractor):
        for alias, expected in [
            ("generic", "TECH"),
            ("General", "TECH"),
            ("QUALIFICATION", "QUAL"),
            ("COMMERCIAL", "COMM"),
        ]:
            item = {"category": alias, "description": "test desc", "is_mandatory": True}
            result = extractor._normalize_raw_item(item)
            assert result["category"] == expected, f"{alias} should map to {expected}"

    def test_keyword_inference_fallback(self, extractor):
        item = {
            "category": "UNKNOWN_CAT",
            "description": "投标人须提供营业执照复印件",
            "is_mandatory": True,
            "keywords": ["营业执照"],
        }
        result = extractor._normalize_raw_item(item)
        assert result["category"] == "QUAL"

    @pytest.mark.parametrize(
        "raw_category, description, keywords, expected_category, expected_relevance",
        [
            (
                "PROCEDURAL",
                "投标文件须按规定密封并加盖公章",
                ["密封", "盖章"],
                "FORMAT",
                "procedural",
            ),
            (
                "actionable",
                "投标人须提供营业执照复印件",
                ["营业执照"],
                "QUAL",
                "actionable",
            ),
            (
                "Informational",
                "投标报价不得超过最高限价",
                ["报价", "最高限价"],
                "COMM",
                "informational",
            ),
        ],
    )
    def test_review_relevance_spill_in_category_is_migrated(
        self,
        extractor,
        raw_category,
        description,
        keywords,
        expected_category,
        expected_relevance,
    ):
        item = {
            "category": raw_category,
            "description": description,
            "is_mandatory": False,
            "keywords": keywords,
        }
        result = extractor._normalize_raw_item(item)
        assert result["category"] == expected_category
        assert result["review_relevance"] == expected_relevance

    def test_review_relevance_spill_preserves_existing_valid_relevance(self, extractor):
        item = {
            "category": "PROCEDURAL",
            "review_relevance": "informational",
            "description": "投标人须提供营业执照复印件",
            "is_mandatory": False,
            "keywords": ["营业执照"],
        }
        result = extractor._normalize_raw_item(item)
        assert result["category"] == "QUAL"
        assert result["review_relevance"] == "informational"

    def test_review_relevance_spill_normalizes_uppercase_relevance(self, extractor):
        item = {
            "category": "PROCEDURAL",
            "review_relevance": "PROCEDURAL",
            "description": "投标文件须按规定密封并加盖公章",
            "is_mandatory": False,
            "keywords": ["密封", "盖章"],
        }
        result = extractor._normalize_raw_item(item)
        assert result["category"] == "FORMAT"
        assert result["review_relevance"] == "procedural"


class TestParseItemsWithSalvage:
    @pytest.fixture
    def extractor(self):
        from unittest.mock import AsyncMock

        from app.core.extractor.tender_extractor import TenderExtractor
        fake_db = AsyncMock()
        return TenderExtractor(db=fake_db)

    def test_all_valid(self, extractor):
        items = [
            {"category": "QUAL", "description": "valid item one", "is_mandatory": True, "keywords": ["a"]},
            {"category": "TECH", "description": "valid item two", "is_mandatory": False, "keywords": ["b"]},
        ]
        valid, failed = extractor._parse_items_with_salvage(items, chunk_id=1)
        assert len(valid) == 2
        # 接口演进: failed 为失败条目摘要列表（供 明细展示），非计数
        assert failed == []

    def test_partial_salvage(self, extractor):
        items = [
            {"category": "QUAL", "description": "good item here", "is_mandatory": True, "keywords": ["a"]},
            {"category": "TOTALLY_INVALID_UNMAPPABLE", "description": "", "is_mandatory": True},
        ]
        valid, failed = extractor._parse_items_with_salvage(items, chunk_id=2)
        assert len(valid) == 1
        assert valid[0].category == "QUAL"
        assert len(failed) == 1
        # 失败摘要必须可定位: 含条目下标与原因（供人工核对）
        assert failed[0]["item_idx"] == 1
        assert failed[0].get("reason")

    def test_generic_auto_salvaged(self, extractor):
        items = [
            {"category": "GENERIC", "description": "this is a technical test requirement", "is_mandatory": True, "keywords": ["test"]},
        ]
        valid, failed = extractor._parse_items_with_salvage(items, chunk_id=3)
        assert len(valid) == 1
        assert valid[0].category == "TECH"
        assert failed == []

    def test_review_relevance_spill_without_category_hint_still_fails(self, extractor):
        items = [
            {
                "category": "PROCEDURAL",
                "description": "投标人须具备相关服务能力",
                "is_mandatory": False,
                "keywords": ["服务能力"],
            },
        ]
        valid, failed = extractor._parse_items_with_salvage(items, chunk_id=4)
        assert valid == []
        assert len(failed) == 1
        assert failed[0]["item_idx"] == 0
        assert failed[0]["summary"] == "投标人须具备相关服务能力"
        assert failed[0]["reason"] == "要求分类无法识别"


class TestEmptyResultSemantics:
    """: 合法空结果 [] 不应被视为失败分块"""

    @pytest.fixture
    def extractor(self):
        from unittest.mock import AsyncMock

        from app.core.extractor.tender_extractor import TenderExtractor
        fake_db = AsyncMock()
        return TenderExtractor(db=fake_db)

    def test_extract_from_chunk_empty_response_returns_empty_list(self, extractor, monkeypatch):
        """LLM 返回空数组时应返回 []（合法空页），不是 None（真实失败）"""
        import asyncio
        from unittest.mock import AsyncMock

        from app.core.config import settings

        # 接口演进适配: 关闭缓存/章节上下文旁路，聚焦"空数组 vs None"语义本身
        monkeypatch.setattr(settings, "TENDER_CHUNK_EXTRACT_CACHE_ENABLED", False)
        monkeypatch.setattr(settings, "TENDER_EXTRACT_SECTION_CONTEXT_ENABLED", False)

        extractor.llm_client = AsyncMock()
        extractor.llm_client.chat_json = AsyncMock(return_value=[])

        from types import SimpleNamespace
        # section_path/document_id/chunk_fingerprint: 章节上下文与缓存键读取的必备属性
        fake_chunk = SimpleNamespace(
            id=1, page_num=5, content="目录页内容", chunk_index=0,
            section_path=None, document_id=1, chunk_fingerprint="fp1",
        )

        # get_event_loop 依赖会话遗留循环, 与 pytest-asyncio 同跑时抛
        # "There is no current event loop"; asyncio.run 每次新建循环, 顺序无关
        result = asyncio.run(extractor._extract_from_chunk(fake_chunk))
        assert result is not None, "空数组应返回 []（合法空页），不是 None"
        assert result == []

    def test_extract_from_chunk_failure_returns_none(self, extractor, monkeypatch):
        """LLM 持续 JSON 解析错误时应返回 None（真实失败）"""
        import asyncio
        import json
        from unittest.mock import AsyncMock

        from app.core.config import settings

        monkeypatch.setattr(settings, "TENDER_CHUNK_EXTRACT_CACHE_ENABLED", False)
        monkeypatch.setattr(settings, "TENDER_EXTRACT_SECTION_CONTEXT_ENABLED", False)

        extractor.llm_client = AsyncMock()
        extractor.llm_client.chat_json = AsyncMock(
            side_effect=json.JSONDecodeError("test", "doc", 0)
        )
        extractor.max_retries = 1

        from types import SimpleNamespace
        fake_chunk = SimpleNamespace(
            id=2, page_num=10, content="some content", chunk_index=1,
            section_path=None, document_id=1, chunk_fingerprint="fp2",
        )

        result = asyncio.run(extractor._extract_from_chunk(fake_chunk))
        assert result is None, "真实失败应返回 None，不是 []"

        has_failure = any(
            sf["stage"] == "chunk_extraction" and sf.get("chunk_id") == 2
            for sf in extractor._stage_failures
        )
        assert has_failure, "真实失败应记录 chunk_extraction stage_failure"

    def test_empty_result_not_in_failed_chunks(self):
        """[] 不应进入 failed_chunks，None 才应该"""
        results_with_empty = [
            [],
            [{"category": "QUAL", "description": "test"}],
            None,
            [],
        ]

        failed_indices = []
        empty_count = 0
        success_count = 0
        for idx, result in enumerate(results_with_empty):
            if result is None:
                failed_indices.append(idx)
            elif result:
                success_count += 1
            else:
                empty_count += 1

        assert failed_indices == [2], "Only None (index 2) should be a failure"
        assert empty_count == 2, "Two empty pages should be counted as empty, not failed"
        assert success_count == 1, "One result with data should be success"


class TestExtractionQualityReader:
    """验证 _read_extraction_quality 从 heartbeat 正确读取结构化质量指标"""

    def test_none_heartbeat(self):
        from app.services.requirement_service import _read_extraction_quality
        assert _read_extraction_quality(None) is None

    def test_missing_key(self):
        from app.services.requirement_service import _read_extraction_quality
        assert _read_extraction_quality({"stage": "completed"}) is None

    def test_dict_value(self):
        from app.services.requirement_service import _read_extraction_quality
        quality = {"failed_chunk_count": 2, "failure_rate": 0.05}
        result = _read_extraction_quality({"extraction_quality": quality})
        assert result == quality

    def test_json_string_value(self):
        import json

        from app.services.requirement_service import _read_extraction_quality
        quality = {"failed_chunk_count": 1, "needs_quality_review": False}
        result = _read_extraction_quality({"extraction_quality": json.dumps(quality)})
        assert result == quality

    def test_invalid_string(self):
        from app.services.requirement_service import _read_extraction_quality
        assert _read_extraction_quality({"extraction_quality": "not json"}) is None


class TestMaxConsecutiveRun:
    def test_empty(self):
        from app.core.tasks.extract_task import _max_consecutive_run
        assert _max_consecutive_run([]) == 0

    def test_single(self):
        from app.core.tasks.extract_task import _max_consecutive_run
        assert _max_consecutive_run([5]) == 1

    def test_consecutive(self):
        from app.core.tasks.extract_task import _max_consecutive_run
        assert _max_consecutive_run([3, 4, 5, 6, 7]) == 5

    def test_mixed(self):
        from app.core.tasks.extract_task import _max_consecutive_run
        assert _max_consecutive_run([1, 2, 5, 6, 7, 10]) == 3

    def test_no_consecutive(self):
        from app.core.tasks.extract_task import _max_consecutive_run
        assert _max_consecutive_run([1, 3, 5, 7]) == 1


class TestProceduralCategoryInference:
    """ 追加: 程序性/递交类词汇的分类兜底推断（第 2 层）。

 第 1 层业务实体命中时结果必须逐字不变；第 2 层仅补齐此前返回 None 的程序性文本；
 项目未归类的招标程序/纪律词汇仍返回 None，保持"推不出即失败"。
 """

    @pytest.mark.parametrize(
        "description, keywords, expected",
        [
            ("投标人须提供营业执照复印件", ["营业执照"], "QUAL"),
            ("近三年类似项目业绩不少于2项", ["业绩"], "PERF"),
            ("项目经理须具备高级职称", ["职称"], "STAFF"),
            ("投标报价不得超过最高限价", ["报价"], "COMM"),
            ("技术参数须满足招标文件要求", ["参数"], "TECH"),
            ("投标文件须装订成册并加盖公章", ["装订"], "FORMAT"),
        ],
    )
    def test_business_entity_layer_results_unchanged(self, description, keywords, expected):
        from app.core.extractor.tender_extractor_parts.category_inference import (
            infer_requirement_category,
        )
        assert infer_requirement_category(description, keywords) == expected

    @pytest.mark.parametrize(
        "description, expected",
        [
            # noise_rules.yaml by_category 已归类的程序性词汇
            ("投标文件正文应使用宋体小四号字", "FORMAT"),
            ("电子版投标文件须为PDF格式且无病毒", "FORMAT"),
            ("资格审查由评标委员会在开标后进行", "QUAL"),
            ("中标后须落实到岗考勤管理", "STAFF"),
            # 递交方式补充词（Prompt 既有约定: 物理形态与递交方式归 FORMAT）
            ("投标文件递交地点为市公共资源交易中心", "FORMAT"),
        ],
    )
    def test_procedural_vocab_layer_infers_category(self, description, expected):
        from app.core.extractor.tender_extractor_parts.category_inference import (
            infer_requirement_category,
        )
        assert infer_requirement_category(description, None) == expected

    def test_business_entity_layer_wins_over_procedural_vocab(self):
        """同时含实体词与版面词时，必须由第 1 层裁定，避免版面词抢占实体分类。"""
        from app.core.extractor.tender_extractor_parts.category_inference import (
            infer_requirement_category,
        )
        # "正本" 属 noise_rules FORMAT 词表，"项目经理/职称" 属第 1 层 STAFF
        assert infer_requirement_category(
            "项目经理职称证明材料须提交正本", ["职称"]
        ) == "STAFF"

    @pytest.mark.parametrize(
        "description",
        [
            "投标人不得串通投标、围标或者陪标",
            "对评标结果有异议可通过投诉渠道提出",
            "本项目不组织踏勘现场",
        ],
    )
    def test_uncategorized_procedural_vocab_still_returns_none(self, description):
        """global 段的招标程序/纪律词汇项目未赋类别，此处也不得臆造分类。"""
        from app.core.extractor.tender_extractor_parts.category_inference import (
            infer_requirement_category,
        )
        assert infer_requirement_category(description, None) is None

    def test_empty_text_returns_none(self):
        from app.core.extractor.tender_extractor_parts.category_inference import (
            infer_requirement_category,
        )
        assert infer_requirement_category(None, None) is None
        assert infer_requirement_category("   ", []) is None

    def test_missing_noise_rules_degrades_to_business_layer_only(self, monkeypatch):
        """词表配置缺失时第 2 层静默失效，第 1 层仍须可用（配置问题不阻断提取）。"""
        from app.core.extractor.tender_extractor_parts import category_inference

        monkeypatch.setattr(category_inference, "load_noise_rules", lambda: {})
        assert category_inference.infer_requirement_category(
            "投标人须提供营业执照", ["营业执照"]
        ) == "QUAL"
        assert category_inference.infer_requirement_category(
            "投标文件正文应使用宋体小四号字", None
        ) is None

    def test_spill_item_with_procedural_text_now_salvaged(self):
        """生产形态闭环: PROCEDURAL 串入 category 且正文为版面要求时不再被丢弃。"""
        from unittest.mock import AsyncMock

        from app.core.extractor.tender_extractor import TenderExtractor

        extractor = TenderExtractor(db=AsyncMock())
        items = [
            {
                "category": "PROCEDURAL",
                "description": "投标文件正文应使用宋体小四号字，行距为固定值20磅",
                "is_mandatory": False,
                "keywords": ["宋体", "行距"],
            },
        ]
        valid, failed = extractor._parse_items_with_salvage(items, chunk_id=5)
        assert failed == []
        assert len(valid) == 1
        assert valid[0].category == "FORMAT"
        assert valid[0].review_relevance == "procedural"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
