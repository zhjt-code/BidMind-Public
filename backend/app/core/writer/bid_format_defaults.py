"""标书文档格式默认值

三层优先级:
  1. 招标文件明确要求 (WritingRenderProfile 字段非默认值)
  2. 本模块定义的行业默认值
  3. python-docx 模板原始值 (最低，不应被使用)

当 WritingRenderProfile 不存在或字段为默认值时，使用本模块的常量。
渲染完成后 _enforce_format_baseline 以这些值为基线做合规扫描。
"""

from dataclasses import dataclass, field


# ==================== 页面设置 ====================

PAGE_WIDTH_MM = 210.0       # A4
PAGE_HEIGHT_MM = 297.0      # A4
MARGIN_TOP_MM = 25.4        # 上边距 ~2.5cm
MARGIN_BOTTOM_MM = 25.4     # 下边距 ~2.5cm
MARGIN_LEFT_MM = 31.8       # 左边距 ~3cm (装订侧)
MARGIN_RIGHT_MM = 25.4      # 右边距 ~2.5cm
LINE_SPACING = 1.5          # 1.5倍行距
LINE_SPACING_MIN = 1.0      # 行距倍数下限
LINE_SPACING_MAX = 3.0      # 行距倍数上限（超出视为磅值或异常，回退默认）

# ==================== 字体定义 ====================

FONT_BODY = "仿宋"           # 正文字体
FONT_BODY_FALLBACK = "宋体"  # 正文备选
FONT_HEADING = "黑体"        # 标题字体
FONT_HEADING_FALLBACK = "SimHei"
FONT_TABLE_HEADER = "黑体"   # 表头字体
FONT_TABLE_BODY = "宋体"     # 表格正文字体
FONT_HEADER_FOOTER = "宋体"  # 页眉页脚字体

# ==================== 颜色 ====================

COLOR_BLACK = "000000"       # 全文统一黑色，严禁花哨配色


@dataclass(frozen=True)
class HeadingSpec:
    """标题层级格式规格"""
    font_name: str           # 中文字体名
    font_name_ascii: str     # 西文字体名 (与中文同名或 fallback)
    size_pt: float           # 字号 (pt)
    bold: bool = True        # 是否加粗
    italic: bool = False     # 标书标题一律不用斜体
    color: str = COLOR_BLACK
    first_line_indent_pt: float = 0.0  # 首行缩进 (标题通常不缩进)


# 标题层级映射 (depth -> Heading style name -> spec)
# depth 0 = 章标题(如"第一章"/"技术标")
# depth 1 = 一级标题(如"一、投标函")
# depth 2 = 二级标题(如"（一）报价原则")
# depth 3 = 三级标题(如"1. 测试目的")
# depth 4+ = 正文子标题

HEADING_SPECS: dict[str, HeadingSpec] = {
    "Heading 1": HeadingSpec(
        font_name="黑体", font_name_ascii="SimHei",
        size_pt=16.0, bold=True,                      # 小二 ≈ 18pt, 三号 ≈ 16pt
    ),
    "Heading 2": HeadingSpec(
        font_name="黑体", font_name_ascii="SimHei",
        size_pt=15.0, bold=True,                      # 三号 ≈ 16pt, 小三 ≈ 15pt
    ),
    "Heading 3": HeadingSpec(
        font_name="楷体", font_name_ascii="KaiTi",
        size_pt=14.0, bold=True,                      # 四号 ≈ 14pt
    ),
    "Heading 4": HeadingSpec(
        font_name="仿宋", font_name_ascii="FangSong",
        size_pt=12.0, bold=True,                      # 小四 ≈ 12pt
    ),
    "Heading 5": HeadingSpec(
        font_name="宋体", font_name_ascii="SimSun",
        size_pt=11.5, bold=True,
    ),
}

# ==================== 正文格式 ====================

BODY_SIZE_PT = 12.0           # 小四 = 12pt
BODY_FIRST_INDENT_PT = 24.0   # 首行缩进 ≈ 2字符(12pt × 2)

# ==================== 表格格式 ====================

TABLE_HEADER_SIZE_PT = 10.5   # 五号
TABLE_BODY_SIZE_PT = 10.5     # 五号
TABLE_HEADER_BOLD = True

# ==================== 页眉页脚 ====================

HEADER_FOOTER_SIZE_PT = 10.5  # 五号
PAGE_NUMBER_FORMAT = "bottom_center"


def normalize_line_spacing(value: object) -> float:
    """将 line_spacing 归一化为合理倍数值。

 LLM 提取的行距可能是磅值(如 22)或倍数(如 1.5)，
 python-docx 的 paragraph_format.line_spacing 接受浮点数时
 按倍数解释，异常大值会导致页数爆炸。

 策略：只接受 [LINE_SPACING_MIN, LINE_SPACING_MAX] 范围内的值，
 超出范围一律回退默认 LINE_SPACING，避免错误换算引入二次歧义。
 """
    if value is None:
        return LINE_SPACING
    try:
        v = float(value)
    except (TypeError, ValueError):
        return LINE_SPACING
    if v < LINE_SPACING_MIN or v > LINE_SPACING_MAX:
        return LINE_SPACING
    return v


def merge_with_profile(profile) -> dict:
    """将 WritingRenderProfile 与默认值合并

    招标文件明确要求的值优先，否则使用默认值。
    返回扁平字典供渲染器使用。
    """
    p = profile

    def _val(field_val, default):
        if field_val is None:
            return default
        return field_val

    return {
        "font_body": _val(p.font_body if p else None, FONT_BODY),
        "font_heading": _val(p.font_heading if p else None, FONT_HEADING),
        "font_size_body": _val(p.font_size_body if p else None, BODY_SIZE_PT),
        "font_size_h1": _val(p.font_size_h1 if p else None, HEADING_SPECS["Heading 1"].size_pt),
        "font_size_h2": _val(p.font_size_h2 if p else None, HEADING_SPECS["Heading 2"].size_pt),
        "font_size_h3": _val(p.font_size_h3 if p else None, HEADING_SPECS["Heading 3"].size_pt),
        "line_spacing": normalize_line_spacing(p.line_spacing if p else None),
        "margin_top": _val(p.margin_top if p else None, MARGIN_TOP_MM),
        "margin_bottom": _val(p.margin_bottom if p else None, MARGIN_BOTTOM_MM),
        "margin_left": _val(p.margin_left if p else None, MARGIN_LEFT_MM),
        "margin_right": _val(p.margin_right if p else None, MARGIN_RIGHT_MM),
        "header_text": p.header_text if p else None,
        "footer_text": p.footer_text if p else None,
    }
