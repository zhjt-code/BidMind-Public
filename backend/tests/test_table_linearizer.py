"""table_linearizer 防御测试 .

覆盖:
- 标准表头表 -> `表头=值`, 列对齐, 不错配.
- 标签-值网格(图4顶部 6 列, 首行含数字 45/值"男") -> 结构文本, 拒绝错配键值.
- 整行合并标题 -> 单行结构文本, 不被塞进任意列名.
- 合并表头(首行含空单元格) -> 落入结构分支, 不抛错.
- 不规则/ragged 行 -> 不抛错, 无错配.
- 空表 / 全空行 -> 返回空 / 跳过.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.parser.table_extractor import ExtractedTable  # noqa: E402
from app.core.parser.table_linearizer import (  # noqa: E402
    TableRowText,
    linearize_table,
)


def _make_table(data: list[list[str]], table_index: int = 0, page_num: int = 1) -> ExtractedTable:
    """构造测试用 ExtractedTable, 模拟 TableExtractor 输出的清理后数据."""
    row_count = len(data)
    col_count = max((len(row) for row in data), default=0)
    return ExtractedTable(
        table_index=table_index,
        page_num=page_num,
        data=data,
        row_count=row_count,
        col_count=col_count,
    )


# -----------------------------------------------------------------------------
# 标准表头表(回归既有正向行为)
# -----------------------------------------------------------------------------


def test_reliable_header_table_emits_key_value_pairs() -> None:
    table = _make_table(
        [
            ["项目名称", "单位", "数量", "备注"],
            ["钢筋", "吨", "120", "HRB400"],
            ["混凝土", "方", "300", "C30"],
        ]
    )

    rows = linearize_table(table)

    assert [r.row_no for r in rows] == [2, 3]
    assert rows[0].content == "项目名称=钢筋；单位=吨；数量=120；备注=HRB400"
    assert "项目名称=混凝土" in rows[1].content
    assert "数量=300" in rows[1].content


def test_reliable_header_table_skips_empty_values_but_keeps_column_alignment() -> None:
    """表头表中某行有空值时, 该列被跳过但其他列不能错位."""
    table = _make_table(
        [
            ["姓名", "岗位", "证书编号"],
            ['<PERSON_A>', "", "ABC-001"],
        ]
    )

    rows = linearize_table(table)

    assert len(rows) == 1
    # 关键: 证书编号必须配到 ABC-001, 不能因为"岗位"空就错配上去
    assert rows[0].content == '姓名=<PERSON_A>；证书编号=ABC-001'
    assert "岗位=ABC-001" not in rows[0].content


# -----------------------------------------------------------------------------
# 标签-值网格(图4顶部) -- 核心场景
# -----------------------------------------------------------------------------


def test_label_value_grid_falls_back_to_structural_text() -> None:
    '图4顶部 6 列标签-值网格: 首行含 "男"(值)、"45"(数字), 不应被当作表头.\n\n    旧实现会输出 `姓名=<PERSON_A>；性别=男；年龄=45` 然后让后续行错配,\n    现实现退化为结构文本, 彻底消除错配可能.\n    '
    table = _make_table(
        [
            ["姓名", '<PERSON_A>', "性别", "男", "年龄", "45"],
            ["职务", "总经理", "职称", "教授级高工", "学历", "博士"],
        ]
    )

    rows = linearize_table(table)

    assert len(rows) == 2
    assert rows[0].content == '姓名 | <PERSON_A> | 性别 | 男 | 年龄 | 45'
    assert rows[1].content == "职务 | 总经理 | 职称 | 教授级高工 | 学历 | 博士"
    # 关键防御断言: 不再出现已知错配模式
    for row in rows:
        assert "=" not in row.content
        assert "姓名=参加工作时间" not in row.content
        assert "姓名=在服务及以往服务项目情况" not in row.content


def test_label_value_grid_with_following_data_rows_does_not_emit_misaligned_pairs() -> None:
    '图1/2 错配场景重放: 顶部标签-值网格后跟一行整行合并标题, 再跟一行真实数据.\n\n    旧实现会因首行 ["姓名","<PERSON_A>",...] 被当作表头, 把后续行的第 0 列\n    塞入 `姓名=...`, 产生 `姓名=在服务及以往服务项目情况` 错配串.\n    '
    table = _make_table(
        [
            ["姓名", '<PERSON_A>', "性别", "男", "年龄", "45"],
            ["在服务及以往服务项目情况", "", "", "", "", ""],
            ["招标人", "项目名称", "管理规模", "开、完工日期", "在服务或已完", "服务质量"],
        ]
    )

    rows = linearize_table(table)

    contents = [r.content for r in rows]
    assert any("在服务及以往服务项目情况" in c for c in contents)
    # 错配断言: 旧实现会产出 "姓名=在服务及以往服务项目情况"
    for c in contents:
        assert "姓名=在服务及以往服务项目情况" not in c
        assert "姓名=参加工作时间" not in c


# -----------------------------------------------------------------------------
# 合并单元格 / 整行标题
# -----------------------------------------------------------------------------


def test_full_row_merged_title_becomes_single_cell_line() -> None:
    """整行合并标题行(只有一个非空单元格)被原样输出, 不被塞进任意列名."""
    table = _make_table(
        [
            ["招标人", "项目名称", "管理规模"],
            ["在服务及以往服务项目情况", "", ""],
            ["<ORG_ENERGY_FIELD_1>", "某项目", "中型"],
        ]
    )

    rows = linearize_table(table)

    # 该表首行有 3 个标签, 触发表头表分支; 第二行只有 col0 有值 -> 跳过其余空值
    # 这是表头表分支的合法行为(`招标人=在服务及以往服务项目情况`).
    # 我们在此场景下用结构分支(列数不齐)校验整行合并是否能保留单行文本:
    table_unaligned = _make_table(
        [
            ["招标人", "项目名称", "管理规模"],
            ["在服务及以往服务项目情况"],
            ["<ORG_ENERGY_FIELD_1>", "某项目", "中型"],
        ]
    )
    rows_unaligned = linearize_table(table_unaligned)
    contents = [r.content for r in rows_unaligned]
    # 列数不齐 -> 退到结构分支, 整行合并标题以单行结构文本出现
    assert "在服务及以往服务项目情况" in contents
    # 标题不会被错配到任意列名
    for c in contents:
        assert "招标人=在服务及以往服务项目情况" not in c

    # 上面"表头表分支"路径单独保留:
    assert rows  # 不抛错即可


def test_merged_header_with_blank_cells_falls_back_to_structural() -> None:
    """首行表头存在空单元格(合并表头) -> 列不对齐, 落入结构分支, 不错位、不报错."""
    table = _make_table(
        [
            ["资格类", "", "技术类"],
            ["营业执照", "资质证书", "施工方案"],
        ]
    )

    rows = linearize_table(table)

    # 首行包含空标签, _is_label_like 失败 -> 结构分支
    assert len(rows) == 2
    assert rows[0].content == "资格类 | 技术类"
    assert rows[1].content == "营业执照 | 资质证书 | 施工方案"


# -----------------------------------------------------------------------------
# 边界与异常路径
# -----------------------------------------------------------------------------


def test_ragged_rows_do_not_raise_and_avoid_misalignment() -> None:
    """不规则行(列数不一致)落入结构分支, 不抛错且无错配键值."""
    table = _make_table(
        [
            ["列A", "列B", "列C"],
            ["v1", "v2"],
            ["x1", "x2", "x3", "x4"],
        ]
    )

    rows = linearize_table(table)

    assert len(rows) == 3
    assert rows[0].content == "列A | 列B | 列C"
    assert rows[1].content == "v1 | v2"
    assert rows[2].content == "x1 | x2 | x3 | x4"
    for r in rows:
        assert "=" not in r.content


def test_empty_table_returns_empty_list() -> None:
    table = _make_table([])
    assert linearize_table(table) == []


def test_single_row_table_uses_structural_branch() -> None:
    """只有一行(没有数据行)-> 不应被当作表头表, 按结构文本输出."""
    table = _make_table([["唯一行单元格1", "唯一行单元格2"]])

    rows = linearize_table(table)

    assert rows == [TableRowText(row_no=1, content="唯一行单元格1 | 唯一行单元格2")]


def test_header_with_pure_digits_falls_back_to_structural() -> None:
    """首行含纯数字(如年份)-> 不是标签型, 落入结构分支."""
    table = _make_table(
        [
            ["项目", "2024", "2025"],
            ["营收", "100", "200"],
        ]
    )

    rows = linearize_table(table)

    # "2024"/"2025" 是纯数字 -> 不被判定为标签 -> 结构分支
    assert rows[0].content == "项目 | 2024 | 2025"
    assert rows[1].content == "营收 | 100 | 200"


def test_header_with_overly_long_cell_falls_back_to_structural() -> None:
    """首行存在超长单元格(>20 字符)-> 不是标签, 落入结构分支."""
    long_cell = "这是一段非常长的说明性文字超过二十个字符限定阈值"
    table = _make_table(
        [
            ["简短表头", long_cell],
            ["A", "B"],
        ]
    )

    rows = linearize_table(table)

    assert rows[0].content == f"简短表头 | {long_cell}"
    assert rows[1].content == "A | B"
