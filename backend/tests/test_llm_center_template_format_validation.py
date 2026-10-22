from app.services.llm_center.validate_service import _validate_template_format


def test_template_format_validation_rejects_unescaped_json_braces() -> None:
    template = '''## 输出 JSON
{
    "metadata": {
        "score_item_name": "评分项名称"
    }
}
{content}
'''

    result = _validate_template_format(
        template,
        "user",
        {"content": "示例内容"},
    )

    assert result.passed is False
    assert result.check_type == "template_format"
    assert "格式化失败" in result.message


def test_template_format_validation_accepts_escaped_json_braces() -> None:
    template = '''## 输出 JSON
{{
    "metadata": {{
        "score_item_name": "评分项名称"
    }}
}}
{content}
'''

    result = _validate_template_format(
        template,
        "user",
        {"content": "示例内容"},
    )

    assert result.passed is True


def test_template_format_validation_skips_real_format_for_system_role() -> None:
    """ 防御: system 模板不参与运行时 .format，未转义 JSON 大括号应放行。

 线上三个 KeyError 节点(bid_structure_analysis / bid_directory_extract /
 skeleton_generate) 的 SYSTEM 文本都属于此模式：示例 JSON 用单大括号、
 供 LLM 阅读，运行时只走 resolve_system_prompt，不会 .format。
 """
    template = '''你是结构分析师。

示例输出:
```json
{
    "structure_mode": "tech_comm",
    "has_technical": true,
    "reasoning": "评分标准明确区分技术标和商务标"
}
```'''

    result = _validate_template_format(
        template,
        "system",
        {"content": "示例内容"},
    )

    assert result.passed is True
    assert result.check_type == "template_format"
    assert "system" in result.message


def test_template_format_validation_user_still_rejects_unescaped_for_real_node_templates() -> None:
    """ 防御: 修复只豁免 system，user 模板仍必须做真实 .format 校验。"""
    template = '''## 任务
请按以下格式输出:
{
    "title": "示例标题"
}
内容: {content}
'''

    result = _validate_template_format(
        template,
        "user",
        {"content": "示例内容"},
    )

    assert result.passed is False
    assert "格式化失败" in result.message
