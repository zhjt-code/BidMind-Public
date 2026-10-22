"""Constrained page verification retries oversized input with smaller evidence."""

import json

import httpx
import pytest
from openai import APIStatusError

from app.core.document_qa.verify.finding_verifier import _ask_page


@pytest.mark.asyncio
async def test_page_verify_reduces_evidence_only_after_input_too_long() -> None:
    request = httpx.Request("POST", "https://example.com/v1/chat/completions")
    response = httpx.Response(400, request=request)
    error = APIStatusError(
        "input length exceeded", response=response, body={"error": "input length exceeded"}
    )

    class Client:
        calls: list[str] = []

        async def chat_json(self, *, user_message: str, **kwargs):
            self.calls.append(user_message)
            if len(self.calls) == 1:
                raise error
            return {"verdicts": []}

    client = Client()
    payload = json.dumps({
        "page": 1,
        "page_char_count": 2000,
        "context": "甲" * 1800,
        "candidates": [{
            "candidate_id": "p1-0", "rule_code": "DOC-TEST-001",
            "title": "标题" * 100, "message": "证据" * 500,
            "excerpts": ["摘录" * 500], "expected": "甲", "actual": "乙",
        }],
    }, ensure_ascii=False)

    await _ask_page(client, payload)

    assert len(client.calls) == 2
    assert payload in client.calls[0]
    assert len(client.calls[1]) < len(client.calls[0])
    assert "p1-0" in client.calls[1]
    assert "DOC-TEST-001" in client.calls[1]
