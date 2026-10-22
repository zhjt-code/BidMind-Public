"""防御测试: chat_json 返回值形态契约守卫

覆盖:
1. require_dict_response 对 dict / list / None / str 的行为
2. bid_extractor._call_llm_extract 在 chat_json 偶发返回 list 时
 应触发现有 JSONDecodeError 重试链, 而非被外层兜底为"未找到响应"

背景: chat_json 接口契约声明返回 dict, 但实现路径(_try_repair_json,
_extract_first_json_payload 数组保护, 原始 json.loads 解析 [...]) 实际
可能返回 list, 导致按 dict 假设直接 .get 的调用方抛 AttributeError
并被外层兜底, 把可恢复的格式问题伪装成业务缺失(与 同型)。
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.ai.json_response import require_dict_response
from app.core.config import settings
from app.core.extractor.bid_extractor import BidExtractor, ExtractionFailedSignal
from app.core.retriever.semantic_retriever import SearchResult

# ===== Part 1: require_dict_response 单元测试 =====

class TestRequireDictResponse:
    def test_passes_dict_through(self):
        original = {"found": True, "content": "ok"}
        result = require_dict_response(original, operation_name="test")
        assert result is original

    def test_passes_empty_dict_through(self):
        result = require_dict_response({}, operation_name="test")
        assert result == {}

    def test_raises_on_list(self):
        with pytest.raises(json.JSONDecodeError) as exc_info:
            require_dict_response(
                [{"found": True, "content": "x"}],
                operation_name="要求 21887 提取",
            )
        msg = str(exc_info.value)
        assert "list" in msg
        assert "要求 21887 提取" in msg

    def test_raises_on_empty_list(self):
        with pytest.raises(json.JSONDecodeError):
            require_dict_response([], operation_name="test")

    def test_raises_on_none(self):
        with pytest.raises(json.JSONDecodeError) as exc_info:
            require_dict_response(None, operation_name="test_none")
        assert "NoneType" in str(exc_info.value)

    def test_raises_on_str(self):
        with pytest.raises(json.JSONDecodeError) as exc_info:
            require_dict_response(
                "raw string output", operation_name="test_str"
            )
        assert "str" in str(exc_info.value)

    def test_raises_on_int(self):
        with pytest.raises(json.JSONDecodeError) as exc_info:
            require_dict_response(42, operation_name="test_int")
        assert "int" in str(exc_info.value)


# ===== Part 2: bid_extractor 集成回归测试 =====
# 复用 test_bid_extractor_input_guard.py 中的 FakeLLMClient 模式

class FakeLLMClient:
    """脚本化 LLM 客户端: 按顺序返回 scripted_results 中的元素。

    元素若为 Exception 则 raise, 否则作为 chat_json 的返回值。
    """

    def __init__(self, scripted_results):
        self.scripted_results = list(scripted_results)
        self.calls = []

    async def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        if not self.scripted_results:
            raise RuntimeError("missing scripted result")
        current = self.scripted_results.pop(0)
        if isinstance(current, Exception):
            raise current
        return current


def make_extractor(
    llm_client, *, top_k: int = 2, max_retries: int = 2
) -> BidExtractor:
    extractor = object.__new__(BidExtractor)
    extractor.llm_client = llm_client
    extractor.max_retries = max_retries
    extractor.top_k = top_k
    extractor.item_timeout = 1.0
    return extractor


def build_search_result(chunk_id: int, content: str) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        document_id=1,
        content=content,
        page_num=1,
        similarity=0.9,
    )


def build_success_response(requirement_id: int = 1) -> dict:
    return {
        "requirement_id": requirement_id,
        "content": "已找到有效响应内容",
        "evidence_type": "other",
        "source_pages": [1],
        "found": True,
        "completeness": "partial",
        "missing_elements": [],
    }


@pytest.mark.asyncio
async def test_call_llm_extract_retries_when_chat_json_returns_list(monkeypatch):
    """核心回归: chat_json 偶发返回 [{...}] 时应触发重试并最终成功,
 禁止被外层兜底为"未找到响应"(与 同型防御)。
 """
    monkeypatch.setattr(settings, "BID_EXTRACT_CONTEXT_CHAR_BUDGET", 5000)
    monkeypatch.setattr(settings, "BID_EXTRACT_CHUNK_CHAR_LIMIT", 500)
    monkeypatch.setattr(settings, "BID_EXTRACT_RETRY_REDUCTION_RATIO", 0.5)

    fake_llm = FakeLLMClient([
        [{"found": True, "content": "误包数组的响应"}],
        build_success_response(21887),
    ])
    extractor = make_extractor(fake_llm, top_k=2, max_retries=3)
    search_results = [build_search_result(1, "简短证据内容")]

    result = await extractor._call_llm_extract(
        req_id=21887,
        category=None,
        description="测试要求",
        is_mandatory=True,
        search_results=search_results,
        image_chunks=[],
    )

    assert result is not None, "list 返回应触发重试, 不应返回 None"
    assert result.requirement_id == 21887
    assert len(fake_llm.calls) == 2, "应调用 chat_json 2 次(首次 list + 重试 dict)"


@pytest.mark.asyncio
async def test_call_llm_extract_exhausts_retries_when_always_list(monkeypatch):
    """若 chat_json 持续返回 list, 应耗尽重试后返回基础设施失败信号,
    日志体现"超过最大重试次数"而非被静默兜底为业务级"未找到响应"。
    """
    monkeypatch.setattr(settings, "BID_EXTRACT_CONTEXT_CHAR_BUDGET", 5000)
    monkeypatch.setattr(settings, "BID_EXTRACT_CHUNK_CHAR_LIMIT", 500)

    fake_llm = FakeLLMClient([
        [{"x": 1}],
        [{"x": 2}],
        [{"x": 3}],
    ])
    extractor = make_extractor(fake_llm, top_k=2, max_retries=3)
    search_results = [build_search_result(1, "证据")]

    result = await extractor._call_llm_extract(
        req_id=99,
        category=None,
        description="始终返回数组",
        is_mandatory=True,
        search_results=search_results,
        image_chunks=[],
    )

    assert isinstance(result, ExtractionFailedSignal)
    assert result.reason == "retry_exhausted"
    assert len(fake_llm.calls) == 3, "应耗尽 max_retries 次重试"


@pytest.mark.asyncio
async def test_call_llm_extract_normal_dict_path_unchanged(monkeypatch):
    """守卫不应影响正常 dict 返回路径: 首次成功不重试。"""
    monkeypatch.setattr(settings, "BID_EXTRACT_CONTEXT_CHAR_BUDGET", 5000)
    monkeypatch.setattr(settings, "BID_EXTRACT_CHUNK_CHAR_LIMIT", 500)

    fake_llm = FakeLLMClient([build_success_response(33)])
    extractor = make_extractor(fake_llm, top_k=2, max_retries=2)
    search_results = [build_search_result(1, "证据")]

    result = await extractor._call_llm_extract(
        req_id=33,
        category=None,
        description="正常路径",
        is_mandatory=False,
        search_results=search_results,
        image_chunks=[],
    )

    assert result is not None
    assert result.requirement_id == 33
    assert len(fake_llm.calls) == 1
