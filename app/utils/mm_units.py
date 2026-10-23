"""毫米/点与像素的统一换算。

模板的持久化坐标与尺寸一律使用毫米；渲染时按 DPI 换算为像素。
本模块只做纯数学换算，不依赖 Qt，便于独立测试与复用。
"""

from __future__ import annotations

import math

MM_PER_INCH = 25.4
POINTS_PER_INCH = 72.0

MIN_DPI = 1
MAX_DPI = 600


def validate_dpi(dpi: object) -> int:
    """校验 DPI 为 ``MIN_DPI`` 到 ``MAX_DPI`` 之间的整数。

    Raises:
        ValueError: 非整数、超出范围或为布尔值。
    """
    if not isinstance(dpi, int) or isinstance(dpi, bool):
        raise ValueError("DPI 必须是整数")
    if not MIN_DPI <= dpi <= MAX_DPI:
        raise ValueError(f"DPI 必须在 {MIN_DPI} 到 {MAX_DPI} 之间")
    return dpi


def _validate_length(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("长度必须是数值")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("长度必须是有限数值")
    if number < 0:
        raise ValueError("长度不能为负数")
    return number


def _round_half_up(value: float) -> int:
    """非负数四舍五入（0.5 向上），避免 Python 银行家舍入导致尺寸抖动。"""
    return math.floor(value + 0.5)


def mm_to_px(value: float, dpi: int) -> int:
    """把毫米换算为像素，四舍五入到整数像素。"""
    return _round_half_up(_validate_length(value) / MM_PER_INCH * validate_dpi(dpi))


def pt_to_px(value: float, dpi: int) -> int:
    """把字号点换算为像素，四舍五入到整数像素。"""
    return _round_half_up(_validate_length(value) / POINTS_PER_INCH * validate_dpi(dpi))
