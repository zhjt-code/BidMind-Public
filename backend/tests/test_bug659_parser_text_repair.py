from app.core.parser.script_char_lifter import lift_page_script_chars, text_from_chars
from app.core.parser.table_linearizer import join_cell_lines_cjk_aware


def _char(text: str, x0: float, top: float, x1: float, bottom: float, size: float) -> dict:
    return {
        "text": text,
        "x0": x0,
        "x1": x1,
        "top": top,
        "bottom": bottom,
        "doctop": top,
        "size": size,
        "upright": True,
    }


def test_lift_page_script_chars_reattaches_baseline_digits() -> None:
    chars = [
        _char("E", 10, 10, 18, 22, 12),
        _char("；", 20, 10, 24, 22, 12),
        _char("1", 18, 20, 21, 27, 7),
        _char("（", 30, 10, 33, 22, 12),
        _char("2", 34, 10, 40, 22, 12),
        _char("）", 41, 10, 44, 22, 12),
    ]

    lifted = lift_page_script_chars(chars)
    text = text_from_chars(lifted)

    assert "E1；" in text
    assert "；1（2）" not in text


def test_join_cell_lines_cjk_aware_removes_cjk_soft_break_spaces() -> None:
    assert join_cell_lines_cjk_aware("评审\n价得分") == "评审价得分"
    assert join_cell_lines_cjk_aware("E\n1") == "E 1"
