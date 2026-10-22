"""跨表副本行判重（换轴重构 P1，探针 v1 指标4行级判重结论产品化）。

职责: 对同一文档的全部物理表做行级归一整行全等判重——仅跨表全等判副本
 (后现者视为副本), 输出副本行归一键与页位置对照诊断。
不做: 不做相似度模糊判重(反例约束: 同表对称并列因素绝不能误判)、
 不判同表内重复(同表重复由既有身份键去重管)、不修改表格数据、
 不裁决项归属、不做整表指纹判重(探针实测已证伪: 副本跨页切分点不同)。
"""
from __future__ import annotations

# 行归一分隔符: 与探针 v1 实测口径一致("\x1f".join), 保持单元格边界可区分。
_ROW_SEPARATOR = "\x1f"
# 归一后内容(不含分隔符)少于该字符数的行不参与判重(空行/单格短行防噪声)。
_MIN_ROW_CONTENT_CHARS = 4


def _normalize_grid_row(row: list[str]) -> tuple[str, str]:
    """整行归一: 逐格去空白后以分隔符拼接; 返回(归一键, 无分隔符内容)。"""
    compact_cells = ["".join(str(cell or "").split()) for cell in row]
    return _ROW_SEPARATOR.join(compact_cells), "".join(compact_cells)


def detect_duplicate_grid_rows(
    table_grids: list[tuple[int, list[list[str]]]],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """检测跨表整行归一全等的副本行。

 table_grids: (页码, 二维单元格文本) 列表, 覆盖同一文档全部物理表,
 顺序即文档物理顺序(先现者为原本, 后现者为副本)。
 返回: (副本行归一键元组[去重保序], 诊断元组[页位置对照])。

 判定规则(保护):
 - 仅"整行归一全等"且出现在**不同物理表**时判副本, 禁止相似度模糊判重;
 - 同表内重复行不判(同表对称并列因素如"质量管理体系与措施5分"/
 "安全管理体系与措施5分"本就不同行文本, 全等口径天然免疫);
 - 归一后内容<4字符的行(空行/单格短行)不参与判重, 防止序号/小计噪声。
 """
    first_seen: dict[str, tuple[int, int, int]] = {}
    duplicate_keys: list[str] = []
    diagnostics: list[str] = []
    for table_index, (page_num, grid) in enumerate(table_grids):
        for row_index, row in enumerate(grid or []):
            key, content = _normalize_grid_row(row)
            if len(content) < _MIN_ROW_CONTENT_CHARS:
                continue
            origin = first_seen.get(key)
            if origin is None:
                first_seen[key] = (table_index, page_num, row_index)
                continue
            origin_table, origin_page, origin_row = origin
            if origin_table == table_index:
                # 同表重复不判: 由既有身份键去重管辖, 此处仅管跨表副本。
                continue
            if key not in duplicate_keys:
                duplicate_keys.append(key)
            diagnostics.append(
                f"副本行: 表{table_index}(页{page_num})行{row_index}"
                f" 与 表{origin_table}(页{origin_page})行{origin_row} 整行归一全等"
            )
    return tuple(duplicate_keys), tuple(diagnostics)


__all__ = ["detect_duplicate_grid_rows"]
