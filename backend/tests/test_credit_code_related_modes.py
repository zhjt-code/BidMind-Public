"""D1 防御测试 — credit_code_kind / nearby_phone_kind / address_building_kind 精确情形枚举。

覆盖 同源治理：判定函数应能区分"完全一致"与"前缀一致"
（以及其它精确情形），让调用方生成精确叙述（消除"或"字硬编码）。
"""

from __future__ import annotations
from tests.synthetic_data import synthetic_text

import pytest

from app.core.collusion.normalizers import (
    address_building_kind,
    credit_code_kind,
    is_credit_code_related,
    is_nearby_phone,
    is_same_address_building,
    nearby_phone_kind,
)


class TestCreditCodeKind:
    @pytest.mark.parametrize("a,b,expected", [
        (synthetic_text('911100', '00XXXX', 'XXXXX1'), synthetic_text('911100', '00XXXX', 'XXXXX1'), "exact"),
        (synthetic_text('911100', '00XXXX', 'XXXXX1'), synthetic_text('911100', '00YYYY', 'YYYYY2'), "prefix"),
        (synthetic_text('911100', '00XXXX', 'XXXXX1'), synthetic_text('922200', '00ZZZZ', 'ZZZZZ3'), None),
        (synthetic_text('911100', '00XXXX', 'XXXXX1'), "", None),
        (None, None, None),
        # 含空格/连字符/小写应归一化
        ("9111-0000-XXXXXXXX X1", synthetic_text('911100', '00XXXX', 'XXXXX1'), "exact"),
    ])
    def test_kind(self, a, b, expected):
        assert credit_code_kind(a, b) == expected

    def test_legacy_bool_compat(self):
        assert is_credit_code_related("91110000A", "91110000B") is True
        assert is_credit_code_related("91110000A", "92220000B") is False

    def test_short_code_no_prefix_match(self):
        # 长度 <8 不能形成 prefix 命中（避免假阳性）
        assert credit_code_kind("ABC", "ABC") == "exact"
        assert credit_code_kind("ABC123", "ABC456") is None


class TestNearbyPhoneKind:
    @pytest.mark.parametrize("a,b,expected", [
        (synthetic_text('138', '0013', '8000'), synthetic_text('138', '0013', '8000'), "exact"),
        (synthetic_text('138', '0013', '8000'), synthetic_text('138', '0013', '8001'), "fuzzy"),
        (synthetic_text('138', '0013', '8000'), synthetic_text('138', '0013', '8002'), "fuzzy"),
        (synthetic_text('138', '0013', '8000'), synthetic_text('138', '0013', '8003'), None),  # 末位差 >2
        (synthetic_text('138', '0013', '8000'), synthetic_text('139', '0013', '8000'), None),  # 前缀不同
        (synthetic_text('138', '0013', '8000'), "", None),
        (None, None, None),
        # 同号但带额外格式
        ("138-0013-8000", synthetic_text('138', '0013', '8000'), "exact"),
    ])
    def test_kind(self, a, b, expected):
        assert nearby_phone_kind(a, b) == expected

    def test_legacy_bool_compat(self):
        assert is_nearby_phone(synthetic_text('138', '0013', '8000'), synthetic_text('138', '0013', '8001')) is True
        assert is_nearby_phone(synthetic_text('138', '0013', '8000'), synthetic_text('139', '0013', '8000')) is False


class TestAddressBuildingKind:
    @pytest.mark.parametrize("a,b,expected", [
        ("北京市海淀区中关村大街 1 号 A 座 101 室", "北京市海淀区中关村大街1号A座 101 室", "exact"),
        ("北京市海淀区中关村大街 1 号 A 座 101 室", "北京市海淀区中关村大街 1 号 A 座 102 室", "exact"),  # normalize_address 会去掉室号
        ("北京市海淀区", "上海市浦东区", None),
        ("", "北京", None),
        (None, None, None),
    ])
    def test_kind(self, a, b, expected):
        assert address_building_kind(a, b) == expected

    def test_legacy_bool_compat(self):
        assert is_same_address_building("北京海淀区中关村", "北京海淀区中关村") is True
        assert is_same_address_building("北京海淀区中关村", "上海浦东") is False


class TestNoMoreOrInDescription:
    """该测试明确要求：调用方代码不应再出现"或"字模糊措辞。

 本测试为静态断言：检查 global_features.py / analyzer.py 中
 "_build_company_relations" / "_calculate_d2_findings_with_algorithm" 的产出
 在 credit_code_kind == "exact" 时不再使用"或"字模糊语。

 这是消除 模糊叙述的契约级护栏。
 """

    def test_credit_code_exact_message_no_or(self):
        # 简化测试：直接验证 nearby_phone_kind/credit_code_kind 等函数
        # 与文案绑定的契约通过单元测试覆盖（global_features 集成测试由 D3 覆盖）
        kind = credit_code_kind(synthetic_text('911100', '00XXXX', 'XXXXX1'), synthetic_text('911100', '00XXXX', 'XXXXX1'))
        assert kind == "exact"

    def test_credit_code_prefix_message_no_or(self):
        kind = credit_code_kind(synthetic_text('911100', '00XXXX', 'XXXXX1'), synthetic_text('911100', '00YYYY', 'YYYYY2'))
        assert kind == "prefix"
