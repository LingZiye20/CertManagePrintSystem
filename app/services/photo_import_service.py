"""照片批量导入 Service。

职责边界：

- 只扫描用户选定的文件夹，按文件名与指定字段精确匹配，不写库、不动照片；
- 只收集 ``.jpg`` / ``.jpeg`` 文件，其余文件计入"已忽略"；
- 匹配键与字段值都做 ``strip()``，区分大小写，不做补零或格式转换；
- 字段值为空的记录不参与匹配；字段值歧义与同一学生多文件分别报告；
- 导入时逐条调用 :meth:`PhotoService.set_photo`，沿用其事务、审计与旧文件清理语义；
- 单条失败不中断整批，结果按成功、失败、跳过、忽略文件分别报告。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from app.repositories import CertificateRecordRepository
from app.services.errors import PhotoImportError, ServiceError
from app.services.photo_service import PhotoService

logger = logging.getLogger(__name__)

JPEG_SUFFIXES: frozenset[str] = frozenset({".jpg", ".jpeg"})

# 关联字段白名单：只在这三种业务字段上做精确匹配。
PHOTO_KEY_FIELDS: dict[str, str] = {
    "student_no": "学号",
    "id_number": "证件号码",
    "candidate_no": "考生号",
}


class PhotoImportMode(str, Enum):
    """已有照片学生的处理方式。"""

    OVERWRITE = "overwrite"
    SKIP_EXISTING = "skip_existing"


@dataclass(frozen=True, slots=True)
class PhotoImportCandidate:
    """一次扫描中确定可导入的文件与学生。"""

    record_id: int
    student_no: str
    student_name: str
    file_name: str
    has_existing_photo: bool


@dataclass(frozen=True, slots=True)
class PhotoImportIssue:
    """一条跳过/失败/冲突记录，学生相关信息只使用学号与姓名。"""

    file_name: str
    reason: str
    student_no: str | None = None
    student_name: str | None = None

    def describe(self) -> str:
        """返回适合界面展示的单行文本。"""
        prefix = ""
        if self.student_no:
            prefix = f"学号 {self.student_no}"
            if self.student_name:
                prefix += f"（{self.student_name}）"
            prefix = f"{prefix} "
        return f"{self.file_name}：{prefix}{self.reason}"


@dataclass(frozen=True, slots=True)
class PhotoScanResult:
    """只读扫描结果；不写数据库、不创建照片文件。"""

    folder: Path
    field: str
    candidates: tuple[PhotoImportCandidate, ...] = ()
    unmatched_files: tuple[PhotoImportIssue, ...] = ()
    ambiguous_files: tuple[PhotoImportIssue, ...] = ()
    invalid_files: tuple[PhotoImportIssue, ...] = ()
    duplicate_files: tuple[PhotoImportIssue, ...] = ()
    ignored_files: tuple[str, ...] = ()

    @property
    def field_label(self) -> str:
        return PHOTO_KEY_FIELDS.get(self.field, self.field)

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)

    @property
    def existing_photo_count(self) -> int:
        return sum(1 for candidate in self.candidates if candidate.has_existing_photo)

    @property
    def has_candidates(self) -> bool:
        return bool(self.candidates)


@dataclass(frozen=True, slots=True)
class PhotoImportSuccess:
    """一条成功导入的记录。"""

    student_no: str
    student_name: str
    file_name: str
    photo_path: str
    cleanup_failed: bool = False
    cleanup_message: str | None = None


@dataclass(frozen=True, slots=True)
class PhotoImportReport:
    """一次批量导入的完整结果。"""

    scan: PhotoScanResult
    mode: PhotoImportMode
    imported: tuple[PhotoImportSuccess, ...] = ()
    failed: tuple[PhotoImportIssue, ...] = ()
    skipped: tuple[PhotoImportIssue, ...] = ()

    @property
    def imported_count(self) -> int:
        return len(self.imported)

    @property
    def failed_count(self) -> int:
        return len(self.failed)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)

    @property
    def ignored_count(self) -> int:
        return len(self.scan.ignored_files)

    @property
    def cleanup_warnings(self) -> tuple[str, ...]:
        warnings = []
        for item in self.imported:
            if item.cleanup_failed and item.cleanup_message:
                warnings.append(
                    f"学号 {item.student_no}（{item.student_name}）："
                    f"{item.cleanup_message}"
                )
        return tuple(warnings)

    @property
    def message(self) -> str:
        return (
            f"照片导入完成：成功 {self.imported_count}，失败 {self.failed_count}，"
            f"跳过 {self.skipped_count}，忽略文件 {self.ignored_count}"
        )


class PhotoImportService:
    """按文件名批量匹配学生照片，并逐条复用 :class:`PhotoService` 写入。"""

    def __init__(
        self,
        photos: PhotoService,
        *,
        records: CertificateRecordRepository | None = None,
    ) -> None:
        self.photos = photos
        self.records = records if records is not None else photos.records

    # ------------------------------------------------------------------ 扫描

    def scan(self, folder: str | Path, field: str) -> PhotoScanResult:
        """扫描文件夹并按指定字段匹配学生，不产生任何写入。"""
        if field not in PHOTO_KEY_FIELDS:
            raise PhotoImportError(f"不支持的关联字段：{field}")
        directory = Path(folder)
        if not directory.is_dir():
            raise PhotoImportError("照片文件夹不存在或不可访问，请重新选择")

        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise PhotoImportError("无法读取照片文件夹，请检查访问权限") from exc

        jpeg_files: list[Path] = []
        ignored: list[str] = []
        for entry in entries:
            try:
                is_file = entry.is_file()
            except OSError:
                continue
            if not is_file:
                continue
            if entry.suffix.lower() in JPEG_SUFFIXES:
                jpeg_files.append(entry)
            else:
                ignored.append(entry.name)

        groups: dict[str, list[Path]] = {}
        for path in jpeg_files:
            groups.setdefault(path.stem.strip(), []).append(path)

        index = self._build_field_index(field)

        candidates: list[PhotoImportCandidate] = []
        unmatched: list[PhotoImportIssue] = []
        ambiguous: list[PhotoImportIssue] = []
        invalid: list[PhotoImportIssue] = []
        duplicates: list[PhotoImportIssue] = []

        for key in sorted(groups):
            files = groups[key]
            students = index.get(key) if key else None
            if not students:
                for path in files:
                    unmatched.append(
                        PhotoImportIssue(
                            path.name,
                            f"没有学生的{PHOTO_KEY_FIELDS[field]}与该文件名匹配",
                        )
                    )
                continue
            if len(students) > 1:
                for path in files:
                    ambiguous.append(
                        PhotoImportIssue(
                            path.name,
                            f"字段值不唯一，匹配到 {len(students)} 条学生记录，已跳过",
                        )
                    )
                continue

            record = students[0]
            student_no, student_name = _student_label(record)
            first = files[0]
            for extra in files[1:]:
                duplicates.append(
                    PhotoImportIssue(
                        extra.name,
                        f"该学生已由其他文件匹配（{first.name}）",
                        student_no,
                        student_name,
                    )
                )

            try:
                self.photos.validate_source(first)
            except ServiceError as exc:
                invalid.append(
                    PhotoImportIssue(first.name, str(exc), student_no, student_name)
                )
                continue

            candidates.append(
                PhotoImportCandidate(
                    record_id=int(record["id"]),
                    student_no=student_no,
                    student_name=student_name,
                    file_name=first.name,
                    has_existing_photo=bool(
                        str(record.get("photo_path") or "").strip()
                    ),
                )
            )

        return PhotoScanResult(
            folder=directory,
            field=field,
            candidates=tuple(candidates),
            unmatched_files=tuple(unmatched),
            ambiguous_files=tuple(ambiguous),
            invalid_files=tuple(invalid),
            duplicate_files=tuple(duplicates),
            ignored_files=tuple(ignored),
        )

    def _build_field_index(self, field: str) -> dict[str, list[dict[str, Any]]]:
        """按 ``strip()`` 后的字段值建立索引，空值不参与匹配。"""
        index: dict[str, list[dict[str, Any]]] = {}
        for record in self.records.list_for_photo_matching(field):
            raw = record.get(field)
            if not isinstance(raw, str):
                continue
            key = raw.strip()
            if not key:
                continue
            index.setdefault(key, []).append(record)
        return index

    # ------------------------------------------------------------------ 导入

    def import_photos(
        self,
        scan: PhotoScanResult,
        *,
        mode: PhotoImportMode | str = PhotoImportMode.OVERWRITE,
    ) -> PhotoImportReport:
        """逐条调用 :meth:`PhotoService.set_photo`，单条失败不影响其他文件。"""
        try:
            resolved_mode = (
                mode if isinstance(mode, PhotoImportMode) else PhotoImportMode(mode)
            )
        except ValueError as exc:
            raise PhotoImportError(f"不支持的照片导入模式：{mode}") from exc

        imported: list[PhotoImportSuccess] = []
        failed: list[PhotoImportIssue] = []
        skipped: list[PhotoImportIssue] = []

        for candidate in scan.candidates:
            if (
                resolved_mode is PhotoImportMode.SKIP_EXISTING
                and candidate.has_existing_photo
            ):
                skipped.append(
                    PhotoImportIssue(
                        candidate.file_name,
                        "已有照片，按选择跳过",
                        candidate.student_no,
                        candidate.student_name,
                    )
                )
                continue

            try:
                result = self.photos.set_photo(
                    candidate.record_id, scan.folder / candidate.file_name
                )
            except ServiceError as exc:
                failed.append(
                    PhotoImportIssue(
                        candidate.file_name,
                        str(exc),
                        candidate.student_no,
                        candidate.student_name,
                    )
                )
                continue
            except Exception:  # noqa: BLE001 - 单条未预期错误不得中断整批
                logger.exception(
                    "批量导入照片时发生未预期错误（学号 %s）", candidate.student_no
                )
                failed.append(
                    PhotoImportIssue(
                        candidate.file_name,
                        "发生未预期错误，请查看日志后重试",
                        candidate.student_no,
                        candidate.student_name,
                    )
                )
                continue

            imported.append(
                PhotoImportSuccess(
                    student_no=candidate.student_no,
                    student_name=candidate.student_name,
                    file_name=candidate.file_name,
                    photo_path=result.photo_path,
                    cleanup_failed=result.cleanup_failed,
                    cleanup_message=result.cleanup_message,
                )
            )

        return PhotoImportReport(
            scan=scan,
            mode=resolved_mode,
            imported=tuple(imported),
            failed=tuple(failed),
            skipped=tuple(skipped),
        )


def _student_label(record: dict[str, Any]) -> tuple[str, str]:
    """学生一律以学号与姓名标识，避免在报告中出现证件号码。"""
    student_no = str(record.get("student_no") or "（空学号）")
    student_name = str(record.get("name") or "（未填姓名）")
    return student_no, student_name
