""": 招标分块提取输出预算与有损修复信号策略测试。"""

import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.contracts.degradation_signal import DegradationSignal, push_signal

chunk_budget_policy = importlib.import_module(
    "app.core.extractor.tender_extractor_parts.chunk_budget_policy"
)
CHUNK_EXTRACT_TOKEN_CEILING = chunk_budget_policy.CHUNK_EXTRACT_TOKEN_CEILING
ChunkJsonErrorKind = chunk_budget_policy.ChunkJsonErrorKind
classify_chunk_extract_json_error = (
    chunk_budget_policy.classify_chunk_extract_json_error
)
escalate_chunk_max_tokens = chunk_budget_policy.escalate_chunk_max_tokens
initial_chunk_max_tokens = chunk_budget_policy.initial_chunk_max_tokens
pop_lossy_repair_signals = chunk_budget_policy.pop_lossy_repair_signals


def test_initial_chunk_max_tokens_uses_higher_budget_for_validation_rescue():
    assert initial_chunk_max_tokens(None) == 2000
    assert initial_chunk_max_tokens("validation_rescue") == 4000


def test_escalate_chunk_max_tokens_doubles_until_ceiling():
    assert escalate_chunk_max_tokens(2000) == 4000
    assert escalate_chunk_max_tokens(5000) == CHUNK_EXTRACT_TOKEN_CEILING
    assert escalate_chunk_max_tokens(CHUNK_EXTRACT_TOKEN_CEILING) == CHUNK_EXTRACT_TOKEN_CEILING


def test_classify_unterminated_string_as_truncation():
    err = json.JSONDecodeError(
        "Unterminated string starting at",
        '[{"description": "abc',
        18,
    )

    assert classify_chunk_extract_json_error(err) is ChunkJsonErrorKind.TRUNCATION


def test_classify_char_zero_expecting_value_as_empty_response_not_truncation():
    err = json.JSONDecodeError("Expecting value", "", 0)

    assert classify_chunk_extract_json_error(err) is ChunkJsonErrorKind.EMPTY_RESPONSE


def test_classify_non_empty_expecting_value_as_malformed_not_truncation():
    err = json.JSONDecodeError("Expecting value", '{"key": }', 8)

    assert classify_chunk_extract_json_error(err) is ChunkJsonErrorKind.MALFORMED


def test_pop_lossy_repair_signals_filters_non_lossy_repairs_and_clears_buffer():
    push_signal(DegradationSignal(
        source="json_repair_truncated",
        flow="all",
        repair_type="truncated_array",
        original_length=2000,
        recovered_elements=3,
    ))
    push_signal(DegradationSignal(
        source="json_repair_trailing_comma",
        flow="all",
        repair_type="trailing_comma",
        original_length=200,
        recovered_elements=1,
    ))

    signals = pop_lossy_repair_signals()

    assert [signal.repair_type for signal in signals] == ["truncated_array"]
    assert pop_lossy_repair_signals() == []
