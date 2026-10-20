""": A4 页芯高度适配。"""

from app.core.writer.visuals.page_fit import (
    MAX_DIAGRAM_HEIGHT_MM,
    PAGE_CORE_HEIGHT_MM,
    fit_diagram_size_mm,
)


def test_page_core_matches_a4_minus_vertical_margins() -> None:
    assert PAGE_CORE_HEIGHT_MM == 297.0 - 25.4 - 25.4
    assert MAX_DIAGRAM_HEIGHT_MM == PAGE_CORE_HEIGHT_MM - 20.0


def test_landscape_ratio_keeps_nominal_width() -> None:
    width_mm, height_mm = fit_diagram_size_mm(130, 400, 300)
    assert width_mm == 130
    assert abs(height_mm - 97.5) < 1e-9


def test_oversized_height_shrinks_width_and_preserves_ratio() -> None:
    width_mm, height_mm = fit_diagram_size_mm(130, 400, 2000)
    assert height_mm == MAX_DIAGRAM_HEIGHT_MM
    assert width_mm < 130
    assert abs(width_mm / height_mm - 400 / 2000) < 1e-9
