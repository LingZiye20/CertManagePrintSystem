"""照片文件名与相对路径安全工具测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.utils.photo_paths import (
    photo_file_path,
    safe_photo_stem,
    validate_photo_filename,
)


def test_safe_stem_keeps_normal_student_number() -> None:
    assert safe_photo_stem("00123") == "00123"
    assert safe_photo_stem("  S-2026-001  ") == "S-2026-001"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../00123", "_00123"),
        ("00123/abc", "00123_abc"),
        ("00123\\abc", "00123_abc"),
        ("C:00123", "C_00123"),
        ('a<b>c?d*e|f"g', "a_b_c_d_e_f_g"),
    ],
)
def test_safe_stem_removes_path_and_windows_invalid_characters(
    raw: str, expected: str
) -> None:
    stem = safe_photo_stem(raw)
    assert stem == expected
    assert "/" not in stem and "\\" not in stem and ":" not in stem


@pytest.mark.parametrize("raw", ["CON", "con", "PRN", "NUL", "COM1", "LPT9"])
def test_safe_stem_avoids_windows_reserved_names(raw: str) -> None:
    stem = safe_photo_stem(raw)
    assert stem.upper() not in {"CON", "PRN", "NUL", "COM1", "LPT9"}
    assert stem


@pytest.mark.parametrize("raw", ["", "   ", ".", "..", None, 123])
def test_safe_stem_falls_back_for_invalid_input(raw: object) -> None:
    assert safe_photo_stem(raw, fallback="record_1") == "record_1"


def test_safe_stem_truncates_long_names() -> None:
    stem = safe_photo_stem("A" * 400)
    assert len(stem) <= 120


@pytest.mark.parametrize(
    "value",
    ["00123.jpg", "00123.JPEG", "00123_a1b2c3d4.jpg"],
)
def test_validate_photo_filename_accepts_single_jpg_name(value: str) -> None:
    assert validate_photo_filename(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "00123.png",
        "../00123.jpg",
        "sub/00123.jpg",
        "sub\\00123.jpg",
        "C:\\temp\\00123.jpg",
        "/tmp/00123.jpg",
        ".jpg",
        "CON.jpg",
        "00123.jpg ",
        None,
        123,
    ],
)
def test_validate_photo_filename_rejects_unsafe_or_non_jpg(value: object) -> None:
    with pytest.raises(ValueError):
        validate_photo_filename(value)  # type: ignore[arg-type]


def test_photo_file_path_stays_inside_photos_dir(tmp_path: Path) -> None:
    photos_dir = tmp_path / "photos"
    photos_dir.mkdir()

    assert photo_file_path(photos_dir, "00123.jpg") == (photos_dir / "00123.jpg").resolve()

    with pytest.raises(ValueError):
        photo_file_path(photos_dir, "../escape.jpg")
    with pytest.raises(ValueError):
        photo_file_path(photos_dir, str((tmp_path / "outside.jpg").resolve()))
