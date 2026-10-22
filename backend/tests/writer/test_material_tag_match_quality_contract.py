"""素材打标与素材匹配质量提升契约测试。"""

from __future__ import annotations

from types import SimpleNamespace

from app.core.writer.material_matcher import MaterialMatcher
from app.core.writer.material_tagger import MaterialTagger
from app.core.writer.outline_generator import OutlineGenerator
from app.core.writer.prompts.chapter_profile import get_chapter_profile_prompt
from app.core.writer.prompts.material_tag import get_material_tag_prompt
from scripts.seed_llm_registry import _WRITING_NODES, PARAM_DEFS


def _node_defaults(node_key: str) -> tuple[float, int]:
    for node in _WRITING_NODES:
        if node.node_key == node_key:
            return node.default_temperature, node.default_max_tokens
    raise AssertionError(f"node not found: {node_key}")


def test_seed_runtime_params_match_material_and_match_nodes() -> None:
    assert _node_defaults("writing.material_tag") == (0.1, 1000)
    assert _node_defaults("writing.image_classify") == (0.1, 1500)
    assert _node_defaults("writing.chapter_judge") == (0.1, 4000)
    assert _node_defaults("writing.chapter_profile") == (0.1, 8000)


def test_material_tag_prompt_handles_missing_outline_without_fabricating_sections() -> None:
    _system, user = get_material_tag_prompt(
        title="营业执照扫描件",
        material_type="text",
        content_preview="营业执照，统一社会信用代码 123。",
        outline_tree="",
        resolved_label="营业执照",
    )

    assert "目录尚未生成" in user
    assert "禁止自创章节名称" in user
    assert "applicable_sections" in user
    assert "tag_basis" in user


def test_material_tagger_normalizes_quality_signals() -> None:
    tagger = object.__new__(MaterialTagger)
    result = tagger._normalize_tags({
        "domain": "企业资质",
        "applicable_sections": ["资格证明材料"],
        "confidence": 1.4,
        "evidence_source": "llm",
        "tag_basis": "标题与内容均指向营业执照",
    })

    assert result["confidence"] == 1.0
    assert result["evidence_source"] == "llm"
    assert "营业执照" in result["tag_basis"]


def test_material_tagger_fallback_has_quality_signals() -> None:
    tagger = object.__new__(MaterialTagger)
    material = SimpleNamespace(title="项目业绩材料", display_title=None, resolved_label=None)

    result = tagger._fallback_tags(material)

    assert result["confidence"] < 0.5
    assert result["evidence_source"] == "fallback_keyword"
    assert "关键词" in result["tag_basis"]


def test_chapter_profile_prompt_accepts_clause_examples_summary() -> None:
    _system, user = get_chapter_profile_prompt(
        tree_text="- [free_writing] 技术方案",
        leaf_list="- ID=1, 标题=技术方案",
        industry_profile_excerpt="物业服务",
        clause_domain_summary="scoring_criterion=2",
        clause_examples_summary="- scoring_criterion: 服务方案完整性 20分",
    )

    assert "具体约束示例" in user
    assert "服务方案完整性" in user
    assert "顶层必须是 JSON 数组" in user
    assert "禁止输出" in user


def test_chapter_profile_response_unwraps_common_array_fields() -> None:
    raw = {
        "profiles": [
            {
                "node_id": 1,
                "semantic_role": "技术方案",
                "expects": ["评分标准"],
                "rejects": ["报价", "资质"],
            }
        ]
    }

    normalized = OutlineGenerator._normalize_chapter_profiles_response(
        raw,
        volume_key="technical",
    )

    assert normalized == raw["profiles"]


def test_chapter_profile_response_rejects_unusable_object() -> None:
    normalized = OutlineGenerator._normalize_chapter_profiles_response(
        {"profile": {"node_id": 1}},
        volume_key="technical",
    )

    assert normalized == []


def test_chapter_profile_response_wraps_single_profile_object() -> None:
    """: LLM 返回单条画像对象（未包数组）时应包装为单元素数组。

 复现项目 1388 技术卷 missing=11 的生产场景: LLM 返回
 keys=['node_id','semantic_role','expects','rejects']。
 """
    raw = {
        "node_id": 7,
        "semantic_role": "技术方案章节",
        "expects": ["评分标准", "技术要求"],
        "rejects": ["报价", "投标函", "资质"],
    }

    normalized = OutlineGenerator._normalize_chapter_profiles_response(
        raw,
        volume_key="technical",
    )

    assert normalized == [raw]


def test_chapter_profile_response_wraps_single_object_when_node_id_missing() -> None:
    """LLM 偶发漏写 node_id 但画像字段齐全时仍应识别为单条画像。"""
    raw = {
        "semantic_role": "服务承诺章节",
        "expects": ["服务承诺", "售后保障"],
        "rejects": ["报价明细", "技术方案"],
    }

    normalized = OutlineGenerator._normalize_chapter_profiles_response(
        raw,
        volume_key="commercial",
    )

    assert normalized == [raw]


def test_chapter_profile_response_does_not_wrap_dict_with_one_match_only() -> None:
    """仅命中一个画像字段不足以判定为单条画像，避免误伤无意义对象。"""
    normalized = OutlineGenerator._normalize_chapter_profiles_response(
        {"node_id": 1, "unrelated_field": "noise"},
        volume_key="technical",
    )

    assert normalized == []


def test_chapter_profile_prompt_forbids_bare_single_object_output() -> None:
    """: prompt 必须显式禁止单对象输出。"""
    _system, user = get_chapter_profile_prompt(
        tree_text="- [free_writing] 技术方案",
        leaf_list="- ID=1, 标题=技术方案",
    )

    assert "即使本卷只有一个叶子章节" in user
    assert "禁止直接输出单个对象" in user


def test_chapter_profile_prompt_enforces_full_coverage_contract() -> None:
    """: prompt 必须显式要求输出数组长度等于输入叶子数。"""
    _system, user = get_chapter_profile_prompt(
        tree_text="- [free_writing] 技术方案\n- [free_writing] 服务承诺",
        leaf_list="- ID=1, 标题=技术方案\n- ID=2, 标题=服务承诺",
    )

    assert "覆盖完整性" in user
    assert "输出数组长度必须等于" in user
    assert "禁止省略任何一个章节" in user


def test_accept_profile_items_filters_invalid_and_low_quality() -> None:
    """质量门控: node_id 不在集合 / degraded 应被丢弃；matchable 保留。"""
    profiles = [
        {"node_id": 1, "semantic_role": "技术方案", "expects": ["评分"], "rejects": ["报价", "资质"]},
        {"node_id": 99, "semantic_role": "外卷节点", "expects": ["x"], "rejects": ["a", "b"]},
        {"node_id": 2, "semantic_role": "", "expects": ["x"], "rejects": ["a", "b"]},
        {"node_id": 3, "semantic_role": "服务承诺", "expects": [], "rejects": ["a", "b"]},
        {"node_id": 4, "semantic_role": "人员配置", "expects": ["简历"], "rejects": ["报价"]},
        "not-a-dict",
    ]

    results = OutlineGenerator._accept_profile_items(profiles, node_id_set={1, 2, 3, 4})

    assert [nid for nid, _ in results] == [1, 4]
    assert results[0][1]["semantic_role"] == "技术方案"


async def test_supplement_missing_profiles_skips_when_coverage_above_threshold(
    monkeypatch,
) -> None:
    """兼容回退: enforce=False 且 coverage>=50% 时不触发补救。"""
    from app.core.config import settings
    from app.core.writer.outline_generator import OutlineGenerator as _OG

    monkeypatch.setattr(settings, "WRITING_CHAPTER_PROFILE_ENFORCE_COVERAGE", False)
    og = object.__new__(_OG)
    call_counter = {"n": 0}

    async def _fake_call_llm(payload, op_label):
        call_counter["n"] += 1
        return []

    missing = [SimpleNamespace(id=5)]
    result = await og._supplement_missing_profiles(
        volume_key="technical",
        volume_label="技术",
        expected=6,
        missing_nodes=missing,
        base_payload={"leaf_list": "..."},
        build_leaf_list=lambda nodes: "stub",
        call_llm=_fake_call_llm,
    )

    assert result == []
    assert call_counter["n"] == 0, "enforce=False 且 coverage>=50% 时不应触发补救调用"


async def test_supplement_missing_profiles_triggers_at_75_percent_when_enforce(
    monkeypatch,
) -> None:
    """: enforce=True 时覆盖率 75% 仍触发补救。"""
    from app.core.config import settings
    from app.core.writer.outline_generator import OutlineGenerator as _OG

    monkeypatch.setattr(settings, "WRITING_CHAPTER_PROFILE_ENFORCE_COVERAGE", True)
    og = object.__new__(_OG)
    call_counter = {"n": 0}

    async def _fake_call_llm(payload, op_label):
        call_counter["n"] += 1
        return [
            {
                "node_id": 5,
                "semantic_role": "补齐",
                "expects": ["资料"],
                "rejects": ["报价", "资质"],
            }
        ]

    result = await og._supplement_missing_profiles(
        volume_key="technical",
        volume_label="技术",
        expected=4,
        missing_nodes=[SimpleNamespace(id=5)],
        base_payload={"leaf_list": "..."},
        build_leaf_list=lambda nodes: "stub",
        call_llm=_fake_call_llm,
    )
    assert call_counter["n"] == 1
    assert [nid for nid, _ in result] == [5]


async def test_supplement_missing_profiles_triggers_when_severe_shortage() -> None:
    """: 覆盖率 < 50% 时触发补救调用并补齐缺漏画像。"""
    from app.core.writer.outline_generator import OutlineGenerator as _OG

    og = object.__new__(_OG)
    missing = [SimpleNamespace(id=2), SimpleNamespace(id=3), SimpleNamespace(id=4)]

    async def _fake_call_llm(payload, op_label):
        assert "补救" in op_label
        return [
            {"node_id": 2, "semantic_role": "服务承诺", "expects": ["售后"], "rejects": ["报价", "资质"]},
            {"node_id": 3, "semantic_role": "项目实施", "expects": ["进度"], "rejects": ["报价", "资质"]},
        ]

    result = await og._supplement_missing_profiles(
        volume_key="technical",
        volume_label="技术",
        expected=4,
        missing_nodes=missing,
        base_payload={"leaf_list": "原始列表"},
        build_leaf_list=lambda nodes: f"缩窄列表 {len(nodes)} 项",
        call_llm=_fake_call_llm,
    )

    accepted_ids = sorted(nid for nid, _ in result)
    assert accepted_ids == [2, 3], "应补齐补救返回的两个画像"


async def test_supplement_missing_profiles_swallows_llm_failure() -> None:
    """: 补救 LLM 异常时安全降级返回空，不阻断主流程。"""
    from app.core.writer.outline_generator import OutlineGenerator as _OG

    og = object.__new__(_OG)

    async def _fail_llm(payload, op_label):
        raise RuntimeError("LLM upstream down")

    result = await og._supplement_missing_profiles(
        volume_key="technical",
        volume_label="技术",
        expected=10,
        missing_nodes=[SimpleNamespace(id=i) for i in range(8)],
        base_payload={"leaf_list": "..."},
        build_leaf_list=lambda nodes: "stub",
        call_llm=_fail_llm,
    )

    assert result == []


def test_log_volume_profile_coverage_escalates_severe_shortage(caplog) -> None:
    """: 补救后仍缺漏 >50% 必须升级 ERROR 日志便于运维感知。"""
    import logging

    from loguru import logger as _loguru_logger

    handler_id = _loguru_logger.add(
        lambda msg: logging.getLogger("loguru.bridge").handle(
            logging.LogRecord(
                name="loguru.bridge",
                level=getattr(logging, msg.record["level"].name, logging.INFO),
                pathname=msg.record["file"].path,
                lineno=msg.record["line"],
                msg=msg.record["message"],
                args=(),
                exc_info=None,
            )
        ),
        level="INFO",
    )
    try:
        with caplog.at_level(logging.INFO, logger="loguru.bridge"):
            OutlineGenerator._log_volume_profile_coverage(
                volume_key="technical", expected=10, accepted=2,
            )
            OutlineGenerator._log_volume_profile_coverage(
                volume_key="commercial", expected=10, accepted=7,
            )
            OutlineGenerator._log_volume_profile_coverage(
                volume_key="qualification", expected=5, accepted=5,
            )
    finally:
        _loguru_logger.remove(handler_id)

    levels = {(rec.message[:30], rec.levelname) for rec in caplog.records}
    assert any(
        "单卷章节画像完成" in msg and lvl == "ERROR" for msg, lvl in levels
    ), "严重缺漏应升级到 ERROR"
    assert any(
        "单卷章节画像完成" in msg and lvl == "WARNING" for msg, lvl in levels
    ), "少量缺漏应记 WARNING"
    assert any(
        "单卷章节画像完成" in msg and lvl == "INFO" for msg, lvl in levels
    ), "全覆盖应记 INFO"


def test_material_matcher_detects_missing_or_weak_chapter_profiles() -> None:
    nodes = [
        SimpleNamespace(id=1, title="技术方案", parent_id=None, chapter_profile=None),
        SimpleNamespace(
            id=2,
            title="人员配置",
            parent_id=None,
            chapter_profile='{"semantic_role":"","expects":[],"rejects":[]}',
        ),
        SimpleNamespace(
            id=3,
            title="服务承诺",
            parent_id=None,
            chapter_profile='{"semantic_role":"服务承诺","expects":["服务"],"rejects":["报价","资质"]}',
        ),
    ]

    stats = MaterialMatcher._assess_chapter_profile_quality(nodes)

    assert stats["total"] == 3
    assert stats["missing"] == 1
    assert stats["weak"] == 1
    assert stats["ok"] == 1


_FORBIDDEN_FULL_CANDIDATE_ECHO = (
    "对每个候选约束都必须给出判断",
    "should_bind true 或 false",
    "对每个候选都必须",
)


def test_chapter_judge_prompt_emits_bind_true_only() -> None:
    from app.core.writer.prompts.material_match import get_chapter_judge_prompt

    _system, user = get_chapter_judge_prompt(
        chapter_title="投标函",
        chapter_type="fixed_template",
        chapter_path="商务标/投标函",
        semantic_role="投标函固定格式",
        expects="固定模板",
        rejects="技术方案",
        candidates_text="- ID=12, 类型=fixed_template, 内容=投标函格式",
    )

    assert "只输出 should_bind=true" in user
    assert "不超过 40 字" in user
    for phrase in _FORBIDDEN_FULL_CANDIDATE_ECHO:
        assert phrase not in user


def test_compact_verdict_contract_rejects_full_echo_on_classify_entity() -> None:
    """同形态第二例: 条款分类同样不得要求全量逐条回显判断。"""
    from app.core.writer.prompts.clause_classify import CLAUSE_CLASSIFY_USER

    for phrase in _FORBIDDEN_FULL_CANDIDATE_ECHO:
        assert phrase not in CLAUSE_CLASSIFY_USER


def test_material_seed_declares_new_prompt_params() -> None:
    params = {
        param.param_key
        for param in PARAM_DEFS
        if param.node_key in {
            "writing.material_tag",
            "writing.image_classify",
            "writing.chapter_profile",
            "writing.chapter_judge",
        }
    }

    assert "outline_tree" in params
    assert "resolved_label" in params
    assert "confidence" in params
    assert "tag_basis" in params
    assert "clause_examples_summary" in params
