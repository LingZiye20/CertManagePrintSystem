"""照片文件名与相对路径安全工具。

数据库 ``certificate_records.photo_path`` 只保存相对于 ``AppPaths.photos_dir``
的单层文件名（如 ``00123.jpg``），本模块负责生成安全文件名，并在解析时把
结果严格限制在照片目录内，避免目录穿越与绝对路径。
"""

from __future__ import annotations

from pathlib import Path

MAX_PHOTO_STEM_LENGTH = 120
PHOTO_EXTENSIONS: tuple[str, ...] = (".jpg", ".jpeg")

_WINDOWS_INVALID_CHARS = frozenset('<>:"/\\|?*')
# Windows 保留设备名，不能作为文件名主干。
_RESERVED_STEMS = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
)


def safe_photo_stem(value: object, *, fallback: str = "photo") -> str:
    """把任意文本转换为安全的单层文件名主干。

    路径分隔符与 Windows 非法字符替换为 ``_``；去除首尾空格与点；
    保留名加前缀；过长时截断。无法得到有效主干时返回 ``fallback``。
    """
    text = value.strip() if isinstance(value, str) else ""
    if not text or not text.strip("."):
        return fallback

    cleaned = "".join(
        "_" if char in _WINDOWS_INVALID_CHARS or ord(char) < 32 else char
        for char in text
    )
    cleaned = cleaned.strip(" .")
    if not cleaned or not cleaned.strip("."):
        return fallback

    if cleaned.upper() in _RESERVED_STEMS:
        cleaned = f"_{cleaned}"
    if len(cleaned) > MAX_PHOTO_STEM_LENGTH:
        cleaned = cleaned[:MAX_PHOTO_STEM_LENGTH].strip(" .")
    return cleaned or fallback


def validate_photo_filename(value: object) -> str:
    """校验数据库中的照片相对路径。

    只接受单层 ``.jpg`` / ``.jpeg`` 文件名，拒绝空值、首尾空白、路径分隔符、
    绝对路径、目录穿越、Windows 保留名与不安全字符；通过时原样返回。
    """
    if not isinstance(value, str) or not value:
        raise ValueError("照片文件名必须是非空文本")
    if value != value.strip():
        raise ValueError("照片文件名不能包含首尾空白")
    if any(char in _WINDOWS_INVALID_CHARS for char in value):
        raise ValueError("照片文件名包含非法字符")

    path = Path(value)
    if path.is_absolute() or path.name != value:
        raise ValueError("照片路径必须位于照片目录内")
    if path.suffix.lower() not in PHOTO_EXTENSIONS:
        raise ValueError("照片仅支持 JPG/JPEG 格式")

    stem = path.stem
    if not stem or safe_photo_stem(stem, fallback="") != stem:
        raise ValueError("照片文件名包含不安全字符或保留名")
    return value


def photo_file_path(photos_dir: Path, value: object) -> Path:
    """把数据库中的相对文件名解析为照片目录内的绝对路径。

    Raises:
        ValueError: 文件名不安全或解析结果不在 ``photos_dir`` 内。
    """
    filename = validate_photo_filename(value)
    root = Path(photos_dir).resolve()
    target = (root / filename).resolve()
    if target.parent != root:
        raise ValueError("照片路径必须位于照片目录内")
    return target
