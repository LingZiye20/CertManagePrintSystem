"""图片文件内容校验工具。

使用 PySide6 ``QImageReader`` 读取实际内容，不引入额外图像依赖；只负责判断
文件是否为可解码的 JPEG，不生成缩略图、不修改原文件。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QImageReader

JPEG_FORMATS = frozenset({"jpg", "jpeg"})
JPEG_EXTENSIONS = (".jpg", ".jpeg")


@dataclass(frozen=True, slots=True)
class JpegInfo:
    """已通过校验的 JPEG 文件信息。"""

    path: Path
    width: int
    height: int


def validate_jpeg_file(path: Path) -> JpegInfo:
    """校验文件存在、扩展名为 JPG/JPEG 且内容可解码为 JPEG。

    Raises:
        ValueError: 文件不存在、不是普通文件、扩展名不符、内容损坏或实际格式
            不是 JPEG。
    """
    candidate = Path(path)
    if not candidate.exists():
        raise ValueError("照片文件不存在")
    if not candidate.is_file():
        raise ValueError("照片路径不是普通文件")
    if candidate.suffix.lower() not in JPEG_EXTENSIONS:
        raise ValueError("照片仅支持 JPG/JPEG 格式")

    reader = QImageReader(str(candidate))
    if not reader.canRead():
        raise ValueError("照片文件无法读取或不是有效图片")

    image_format = bytes(reader.format()).decode("ascii", "ignore").lower()
    if image_format not in JPEG_FORMATS:
        raise ValueError("照片内容不是有效的 JPEG 图片")

    size = reader.size()
    if not size.isValid() or size.width() <= 0 or size.height() <= 0:
        raise ValueError("照片尺寸无效")

    # ``canRead``/``format``/``size`` 只读取文件头；必须实际解码，截断或损坏的
    # 图像数据会在这一步返回空图像。
    image = reader.read()
    if image.isNull() or image.width() <= 0 or image.height() <= 0:
        raise ValueError("照片文件已损坏或图像数据不完整")

    # libjpeg 对部分截断文件会补灰后返回完整尺寸图像，因此还需确认 JPEG 结束
    # 标记（EOI，FFD9）存在于文件末尾，避免不完整文件进入正式保存流程。
    if not _has_jpeg_end_marker(candidate):
        raise ValueError("照片文件已损坏或图像数据不完整")
    return JpegInfo(path=candidate.resolve(), width=image.width(), height=image.height())


def _has_jpeg_end_marker(path: Path) -> bool:
    """检查文件尾部是否以 JPEG EOI 标记结束（允许常见尾部空白填充）。"""
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            if size < 4:
                return False
            handle.seek(max(0, size - 64))
            tail = handle.read().rstrip(b"\x00\t\r\n ")
    except OSError as exc:
        raise ValueError("照片文件无法读取") from exc
    return tail.endswith(b"\xff\xd9")
