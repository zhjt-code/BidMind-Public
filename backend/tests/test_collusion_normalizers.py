from app.core.collusion.global_features import GlobalFeatureAnalyzer
from app.core.collusion.normalizers import (
    is_credit_code_related,
    is_nearby_phone,
    is_same_address_building,
    normalize_company_name,
)
from tests.synthetic_data import synthetic_text


def test_normalizers_handle_company_phone_and_address() -> None:
    company_a = synthetic_text("甲建设", "有限责任", "公司")
    assert normalize_company_name(company_a) == "甲建设"
    assert is_nearby_phone(synthetic_text('138', '0000', '0001'), synthetic_text('138', '0000', '0002')) is True
    assert is_same_address_building("南山区科技园1号楼801室", "南山区科技园1号楼901室") is True
    assert is_credit_code_related(synthetic_text('914403', '00ABC1', '234567'), synthetic_text('914403', '00XYZ7', '654321')) is True


def test_global_feature_analyzer_uses_normalized_relations() -> None:
    company_a = synthetic_text("甲建设", "有限责任", "公司")
    company_a_short = synthetic_text("甲建设", "有限", "公司")
    company_b = synthetic_text("乙建设", "有限", "公司")
    analyzer = GlobalFeatureAnalyzer(
        {
            1: {
                "company_info": {
                    "company_name": company_a,
                    "contact_phone": synthetic_text('138', '0000', '0001'),
                    "registered_address": "南山区科技园1号楼801室",
                    "unified_social_credit_code": synthetic_text('914403', '00ABC1', '234567'),
                },
                # P1-4: 末两位改用非 00 值（77）, 与新规则保持一致地验证 precision_tail 仍能触发;
                # 全 "00" 尾数在投标中过于常见, 不再视为可疑信号。
                "price_info": {"total_price": "1000077元"},
            },
            2: {
                "company_info": {
                    "company_name": company_a_short,
                    "contact_phone": synthetic_text('138', '0000', '0002'),
                    "registered_address": "南山区科技园1号楼901室",
                    "unified_social_credit_code": synthetic_text('914403', '00XYZ7', '654321'),
                },
                "price_info": {"total_price": "1000177元"},
            },
            3: {
                "company_info": {
                    "company_name": company_b,
                    "contact_phone": synthetic_text('139', '0000', '0003'),
                    "registered_address": "宝安区创业路2号楼501室",
                    "unified_social_credit_code": synthetic_text('914403', '99AAA7', '654321'),
                },
                "price_info": {"total_price": "1000277元"},
            },
        },
        doc_names={1: "A", 2: "B", 3: "C"},
    )

    result = analyzer.analyze()

    assert any(edge[0] == 1 and edge[1] == 2 for edge in result.company_edges)
    assert any(pattern["type"] == "precision_tail" for pattern in result.pricing_patterns)
