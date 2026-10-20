"""S2.1 防御测试: 散落评分条目关联收口 scoring_clause_link。

映射风险: 权威评分项与散落其它标签的等价条目需双向关联(不搬家不删除)。必须保证:
- 等价条目建立 scoring_ref / related_clause_ids 双向关联。
- 不同评分因素/无关条目不误关联(复用 从严判定)。
- 幂等: 重复运行不产生重复 related_clause_ids。
- 仅候选子类型参与, 废标红线等不被关联。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from app.core.writer.scoring_clause_link import link_scattered_scoring_clauses  # noqa: E402

pytestmark = pytest.mark.scoring


def _clause(cid, sub_type, content, *, score_weight=None, metadata=None):
    return SimpleNamespace(
        id=cid,
        sub_type=sub_type,
        content=content,
        score_weight=score_weight,
        metadata_json=metadata,
    )


def test_links_equivalent_scattered_clause_bidirectionally():
    auth = _clause(
        1, "scoring_criterion", "技术方案完整性，方案覆盖全部技术指标得10分",
        score_weight=10.0, metadata={"score_item_name": "技术方案完整性"},
    )
    scattered = _clause(2, "content_required", "技术方案完整性，方案覆盖全部技术指标得10分")
    linked = link_scattered_scoring_clauses([auth, scattered])
    assert linked == 1
    ref = scattered.metadata_json["scoring_ref"]
    assert ref["clause_id"] == 1
    assert ref["score_item_name"] == "技术方案完整性"
    assert ref["max_score"] == 10.0
    assert auth.metadata_json["related_clause_ids"] == [2]


def test_does_not_link_dissimilar_clause():
    auth = _clause(
        1, "scoring_criterion", "技术方案完整性，方案覆盖全部技术指标得10分",
        score_weight=10.0, metadata={"score_item_name": "技术方案完整性"},
    )
    unrelated = _clause(2, "material_required", "投标人须提供营业执照复印件并加盖公章")
    linked = link_scattered_scoring_clauses([auth, unrelated])
    assert linked == 0
    assert (unrelated.metadata_json or {}).get("scoring_ref") is None
    assert (auth.metadata_json or {}).get("related_clause_ids") is None


def test_link_is_idempotent():
    auth = _clause(
        1, "scoring_criterion", "施工组织设计科学合理得15分",
        score_weight=15.0, metadata={"score_item_name": "施工组织设计"},
    )
    scattered = _clause(2, "emphasis_point", "施工组织设计科学合理得15分")
    first = link_scattered_scoring_clauses([auth, scattered])
    second = link_scattered_scoring_clauses([auth, scattered])
    assert first == 1 and second == 1
    # 幂等: related_clause_ids 不重复累加
    assert auth.metadata_json["related_clause_ids"] == [2]


def test_disqualification_is_not_a_link_candidate():
    # 废标红线即便文本相似也不参与关联(保持红线纯粹)
    auth = _clause(
        1, "scoring_criterion", "未按要求提供检测报告的每项扣5分",
        score_weight=5.0, metadata={"score_item_name": "检测报告"},
    )
    disq = _clause(2, "disqualification", "未按要求提供检测报告的每项扣5分")
    linked = link_scattered_scoring_clauses([auth, disq])
    assert linked == 0
    assert (disq.metadata_json or {}).get("scoring_ref") is None


def test_no_authoritative_or_no_scattered_returns_zero():
    only_scattered = [_clause(1, "content_required", "任意内容要求")]
    only_auth = [_clause(1, "scoring_criterion", "评分项", score_weight=5.0)]
    assert link_scattered_scoring_clauses(only_scattered) == 0
    assert link_scattered_scoring_clauses(only_auth) == 0
