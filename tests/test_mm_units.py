"""毫米/点与像素换算测试：不依赖 Qt，验证统一单位换算。"""

from __future__ import annotations

import math

import pytest

from app.utils.mm_units import (
    MAX_DPI,
    MIN_DPI,
    MM_PER_INCH,
    POINTS_PER_INCH,
    mm_to_px,
    pt_to_px,
    validate_dpi,
)


def test_mm_per_inch_constant() -> None:
    assert MM_PER_INCH == 25.4
    assert POINTS_PER_INCH == 72.0


def test_mm_to_px_at_known_dpi() -> None:
    assert mm_to_px(25.4, 96) == 96
    assert mm_to_px(25.4, 300) == 300
    assert mm_to_px(210.0, 96) == 794  # 210 / 25.4 * 96 = 793.70 → 794
    assert mm_to_px(297.0, 300) == 3508  # 3507.87 → 3508


def test_pt_to_px_at_known_dpi() -> None:
    assert pt_to_px(72.0, 72) == 72
    assert pt_to_px(12.0, 96) == 16
    assert pt_to_px(10.5, 300) == 44  # 43.75 → 44


def test_conversion_rounds_half_up() -> None:
    # 0.5 个像素向上取整，避免尺寸在临界点被系统性低估。
    assert mm_to_px(25.4 / 2, 1) == 1
    assert pt_to_px(0.5, 72) == 1


def test_zero_length_converts_to_zero() -> None:
    assert mm_to_px(0.0, 300) == 0
    assert pt_to_px(0.0, 300) == 0


def test_dpi_validation_accepts_range_and_rejects_invalid() -> None:
    assert validate_dpi(MIN_DPI) == MIN_DPI
    assert validate_dpi(MAX_DPI) == MAX_DPI
    assert validate_dpi(300) == 300

    for invalid in (0, -1, MAX_DPI + 1, 300.5, "300", None, True):
        with pytest.raises(ValueError):
            validate_dpi(invalid)


def test_conversion_rejects_negative_or_non_finite_length() -> None:
    for invalid in (-1.0, math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError):
            mm_to_px(invalid, 300)
        with pytest.raises(ValueError):
            pt_to_px(invalid, 300)

    with pytest.raises(ValueError):
        mm_to_px("10", 300)


def test_conversion_rejects_invalid_dpi() -> None:
    with pytest.raises(ValueError):
        mm_to_px(10.0, 0)
    with pytest.raises(ValueError):
        pt_to_px(10.0, -96)
