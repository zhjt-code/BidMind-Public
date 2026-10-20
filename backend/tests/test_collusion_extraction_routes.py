from app.core.collusion.extraction_routes import (
    build_empty_extraction,
    merge_route_extractions,
)


def test_build_empty_extraction_contains_trace_shell() -> None:
    payload = build_empty_extraction()

    assert payload["company_info"] is None
    assert payload["personnel_list"] == []
    assert payload["metadata_info"]["extraction_trace"]["routes"] == {}


def test_merge_route_extractions_merges_fields_and_preserves_trace() -> None:
    merged = merge_route_extractions(
        route_outputs={
            "entity": {
                "company_info": {
                    "company_name": "<ORG_23>",
                    "legal_representative": '<PERSON_A>',
                },
                "personnel_list": [
                    {
                        "name": '<PERSON_B>',
                        "role": "项目经理",
                        "certificate_numbers": ["粤建安B(2020)0001"],
                        "source_page": "12",
                    }
                ],
            },
            "pricing": {
                "price_info": {
                    "total_price": "1020000元",
                }
            },
            "qualification": {
                "qualifications": ["安全生产许可证 ABC123 (第8页)"],
                "past_projects": ["某市政道路改造工程 (第15页)"],
                "subcontractors": ["某材料供应商"],
            },
        },
        route_context_meta={
            "entity": {"chunk_count": 5, "char_budget": 9000},
            "pricing": {"chunk_count": 3, "char_budget": 8000},
            "qualification": {"chunk_count": 4, "char_budget": 9000},
        },
    )

    assert merged["company_info"]["company_name"] == "<ORG_23>"
    assert merged["company_info"]["_trace"]["company_name"]["route"] == "entity"
    assert merged["price_info"]["total_price"] == "1020000元"
    assert merged["personnel_list"][0]["extraction_route"] == "entity"
    assert merged["qualifications"] == ["安全生产许可证 ABC123 (第8页)"]
    assert merged["past_projects"] == ["某市政道路改造工程 (第15页)"]
    assert merged["subcontractors"] == ["某材料供应商"]
    assert merged["metadata_info"]["extraction_trace"]["routes"]["pricing"]["chunk_count"] == 3
