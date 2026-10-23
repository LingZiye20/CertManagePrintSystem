"""JPEG 完整性校验测试：有效文件、损坏文件与伪装扩展名。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from app.utils.image_files import validate_jpeg_file


def _save_image(path: Path, image_format: str, *, width: int = 40, height: int = 20) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.red)
    assert image.save(str(path), image_format) is True
    return path


def test_valid_jpeg_reports_actual_decoded_size(tmp_path: Path) -> None:
    path = _save_image(tmp_path / "valid.jpg", "JPEG", width=40, height=20)

    info = validate_jpeg_file(path)

    assert info.path == path.resolve()
    assert (info.width, info.height) == (40, 20)


def test_missing_file_and_non_jpg_extension_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        validate_jpeg_file(tmp_path / "missing.jpg")

    png = _save_image(tmp_path / "photo.png", "PNG")
    with pytest.raises(ValueError):
        validate_jpeg_file(png)


def test_obviously_invalid_content_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "broken.jpg"
    path.write_bytes(b"not a jpeg")

    with pytest.raises(ValueError):
        validate_jpeg_file(path)


def test_truncated_jpeg_with_valid_header_is_rejected(tmp_path: Path) -> None:
    full = _save_image(tmp_path / "full.jpg", "JPEG", width=200, height=200)
    data = full.read_bytes()
    truncated = tmp_path / "truncated.jpg"
    truncated.write_bytes(data[: len(data) // 2])

    assert truncated.read_bytes().startswith(b"\xff\xd8")
    with pytest.raises(ValueError):
        validate_jpeg_file(truncated)


def test_png_content_disguised_as_jpg_is_rejected(tmp_path: Path) -> None:
    path = _save_image(tmp_path / "fake.jpg", "PNG")

    with pytest.raises(ValueError):
        validate_jpeg_file(path)
