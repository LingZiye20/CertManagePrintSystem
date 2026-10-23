"""证书编号拼接纯函数测试：空片段、空白裁剪与超位数保留。"""

from __future__ import annotations

import pytest

from app.services import compose_certificate_number


def test_empty_fragments_produce_pure_numeric_number() -> None:
    assert compose_certificate_number(("", "", "", ""), 1, 4) == "0001"
    assert compose_certificate_number(("   ", "\t", "\n", "  "), 12, 4) == "0012"


def test_fragments_are_concatenated_in_order_without_separators() -> None:
    assert (
        compose_certificate_number(("GX", "", "2026", "（桂）"), 8, 4)
        == "GX2026（桂）0008"
    )


def test_fragments_trim_edges_but_keep_internal_spaces() -> None:
    assert (
        compose_certificate_number(("  GX 学 ", "  2026  ", "", ""), 1, 4)
        == "GX 学20260001"
    )


def test_sequence_is_left_padded_to_width() -> None:
    assert compose_certificate_number(("", "", "", ""), 7, 1) == "7"
    assert compose_certificate_number(("", "", "", ""), 12, 6) == "000012"


def test_sequence_width_boundaries_are_accepted() -> None:
    # 1 与 20 是允许的边界值。
    assert compose_certificate_number(("", "", "", ""), 1, 1) == "1"
    assert compose_certificate_number(("", "", "", ""), 1, 20) == "0" * 19 + "1"


def test_sequence_longer_than_width_is_not_truncated() -> None:
    # 位数 4、流水号 10000 必须完整保留。
    assert compose_certificate_number(("", "", "", ""), 10000, 4) == "10000"
    assert compose_certificate_number(("A", "", "", ""), 123456, 4) == "A123456"


@pytest.mark.parametrize("sequence_value", [0, -1, True, "1", 1.0, None])
def test_invalid_sequence_value_rejected(sequence_value: object) -> None:
    with pytest.raises(ValueError):
        compose_certificate_number(("", "", "", ""), sequence_value, 4)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "sequence_width", [0, -1, 21, 999_999_999, True, "4", 4.0, None]
)
def test_invalid_sequence_width_rejected(sequence_width: object) -> None:
    with pytest.raises(ValueError, match="1 到 20"):
        compose_certificate_number(("", "", "", ""), 1, sequence_width)  # type: ignore[arg-type]


def test_non_text_fragment_rejected() -> None:
    with pytest.raises(ValueError):
        compose_certificate_number(("A", 1, "", ""), 1, 4)  # type: ignore[arg-type]
