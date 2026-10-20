"""Stage C 契约测试: material_pair_judge 节点在 seed/registry 的登记。

覆盖:
- 节点纳入 writing 流程节点清单与确定性稳定性等级(温度 0)
- seed NodeDef 使用 chat_with_validation + MaterialPairJudgeBatch 输出 schema
- seed PARAM_DEFS 登记 pairs_json 输入与 decisions 输出
- 代码默认 Prompt 占位符可格式化
"""

from app.core.ai.prompt_registry import FLOW_NODE_KEYS, _derive_stability_class
from app.core.writer.prompts.material_pair_judge import (
    MATERIAL_PAIR_JUDGE_USER,
    get_material_pair_judge_prompt,
)
from scripts.seed_llm_registry import ALL_NODES, PARAM_DEFS

_NODE_KEY = "writing.material_pair_judge"


def test_node_registered_in_writing_flow() -> None:
    assert _NODE_KEY in FLOW_NODE_KEYS["writing"]


def test_node_is_deterministic() -> None:
    assert _derive_stability_class(_NODE_KEY) == "deterministic"


def test_seed_node_uses_validation_and_schema() -> None:
    node = next((nd for nd in ALL_NODES if nd.node_key == _NODE_KEY), None)
    assert node is not None, "material_pair_judge 未在 seed ALL_NODES 中登记"
    assert node.call_method == "chat_with_validation"
    assert node.output_schema_name == "MaterialPairJudgeBatch"
    assert node.stage_key == "material_match"


def test_seed_params_declared() -> None:
    params = {(pd.node_key, pd.param_key, pd.direction) for pd in PARAM_DEFS}
    assert (_NODE_KEY, "pairs_json", "input") in params
    assert (_NODE_KEY, "decisions", "output") in params


def test_prompt_placeholder_formats() -> None:
    assert "{pairs_json}" in MATERIAL_PAIR_JUDGE_USER
    system_msg, user_msg = get_material_pair_judge_prompt('[{"pair_id": "m1_n2"}]')
    assert system_msg
    assert "m1_n2" in user_msg
