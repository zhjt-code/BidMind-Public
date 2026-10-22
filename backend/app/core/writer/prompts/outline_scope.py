"""实质内容纲要标题抽取 Prompt。"""

from app.core.ai.prompt_registry import call_with_fallback

SCOPE_TITLE_SYSTEM = """你是资深投标方案策划师。
你的任务是从与当前投标章节相关的招标原文中，提取该章节目录应响应的纲要级标题。

## 事实边界（不可违反）
- 只输出招标原文已经列出、且与当前投标章节主题相关的一级标题。
- 不得把技术参数、验收指标、分值、实施步骤、质量保证措施、审查程序等写作要素拆成标题。
- 不得输出投标文件格式章、评分标准中的条目。
- 不要输出证书复印件字段、人员名单或表格列名。
- 与当前章节无关的招标内容不要提取。
- 不确定的内容不要编造。

## 粒度
- 只要一级标题，保持目录的纲要性。
- 建议 2 到 8 条；标题宜短，一般不超过 20 个汉字。
- 不要输出二级细则、表格字段或「包括但不限于」后的参数清单。

## 输出格式
输出 JSON 数组：
```json
[
    {"title": "智慧园区平台建设", "source_hint": "建设内容"}
]
```
仅输出 JSON 数组，不要添加任何说明文字。无可用条目时输出 []。"""

SCOPE_TITLE_USER = """## 当前投标章节
{chapter_title}

## 与本章相关的招标原文
{scope_text}

## 任务
只提取与「{chapter_title}」相关的纲要级标题，用于丰富该章节目录。
- 必须能在原文中找到依据
- 不要参数、指标、评分项、投标文件目录条目、审查程序
- 标题去除原有编号
- 若原文与本章无关，输出 []

仅输出 JSON 数组。"""


def get_scope_title_prompt(scope_text: str, chapter_title: str = "") -> tuple[str, str]:
    """获取实质内容纲要标题抽取 Prompt。"""

    return call_with_fallback(
        "writing.outline_scope_titles",
        _code_scope_title_prompt,
        scope_text=scope_text,
        chapter_title=chapter_title,
    )


def _code_scope_title_prompt(scope_text: str, chapter_title: str = "") -> tuple[str, str]:
    return (
        SCOPE_TITLE_SYSTEM,
        SCOPE_TITLE_USER.format(
            scope_text=scope_text or "（无相关招标原文）",
            chapter_title=chapter_title or "（未指定章节）",
        ),
    )
