"""学生毕业照片 Service。

职责边界：

- 校验源文件为可解码的 JPEG，并把照片复制到 ``AppPaths.photos_dir``；
- 数据库只保存相对于照片目录的单层文件名，加载只依据该字段；
- 目标文件名冲突时使用安全备用名，绝不覆盖已有文件；
- 新照片落盘后，在同一数据库事务中更新 ``photo_path`` 与成功审计；
- 事务提交成功后才清理旧照片，失败时保留旧照片与旧关联；
- 清理旧照片前检查路径安全与其他记录引用，删除记录时不删除照片。
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.config.paths import AppPaths
from app.data.db import Database
from app.repositories import CertificateRecordRepository, OperationLogRepository
from app.services.errors import (
    AuditWriteError,
    DataAccessError,
    InvalidPhotoError,
    PhotoPathError,
    PhotoStorageError,
    RecordNotFoundError,
)
from app.utils.image_files import validate_jpeg_file
from app.utils.photo_paths import photo_file_path, safe_photo_stem

logger = logging.getLogger(__name__)

TARGET_TYPE = "certificate_record"
OP_PHOTO_UPDATE = "student_photo_update"
MAX_FILENAME_ATTEMPTS = 5


@dataclass(frozen=True, slots=True)
class PhotoUpdateResult:
    """一次照片新增/替换的结果。"""

    record_id: int
    photo_path: str
    previous_path: str | None
    cleanup_failed: bool = False
    cleanup_message: str | None = None


class PhotoService:
    """毕业照片的校验、存储与关联更新入口。"""

    def __init__(
        self,
        database: Database,
        paths: AppPaths,
        *,
        records: CertificateRecordRepository | None = None,
        logs: OperationLogRepository | None = None,
    ) -> None:
        self.database = database
        self.paths = paths
        self.records = records if records is not None else CertificateRecordRepository(database)
        self.logs = logs if logs is not None else OperationLogRepository(database)

    # ------------------------------------------------------------------ 读取

    def validate_source(self, source: str | Path) -> Path:
        """校验待保存的源照片，返回解析后的绝对路径。"""
        try:
            return validate_jpeg_file(Path(source)).path
        except (OSError, ValueError) as exc:
            raise InvalidPhotoError(str(exc)) from exc

    def resolve_photo(self, photo_path: str | None) -> Path | None:
        """把数据库中的相对文件名解析为照片目录内的绝对路径。"""
        if photo_path is None or not str(photo_path).strip():
            return None
        try:
            return photo_file_path(self.paths.photos_dir, photo_path)
        except ValueError as exc:
            raise PhotoPathError(str(exc)) from exc

    # ------------------------------------------------------------------ 写入

    def set_photo(
        self,
        record_id: int,
        source: str | Path,
    ) -> PhotoUpdateResult:
        """为指定记录新增或替换毕业照片。

        Args:
            record_id: 目标记录主键。
            source: 待保存的 JPEG 源文件；源文件本身不会被移动或删除。

        Raises:
            InvalidPhotoError: 源文件不是有效 JPEG。
            RecordNotFoundError: 目标记录不存在。
            PhotoStorageError: 照片目录不可用或目标文件无法写入。
            DataAccessError: 数据库更新失败，原照片关联未变更。
            AuditWriteError: 审计写入失败，照片关联已整体回滚。
        """
        validated_source = self.validate_source(source)
        record = self.records.get(record_id)
        if record is None:
            raise RecordNotFoundError(f"记录 {record_id} 不存在或已被删除")

        previous_path = record.get("photo_path") or None
        photos_dir = self._ensure_photos_dir()
        stem = safe_photo_stem(
            record.get("student_no"), fallback=f"record_{record_id}"
        )
        target = self._copy_new_file(validated_source, photos_dir, stem)
        filename = target.name
        student_no = record.get("student_no") or "（空）"

        try:
            with self.database.transaction() as connection:
                if not self.records.update(
                    record_id, {"photo_path": filename}, connection=connection
                ):
                    raise RecordNotFoundError(f"记录 {record_id} 不存在或已被删除")
                self._write_success_log(
                    connection, record_id, student_no, filename
                )
        except RecordNotFoundError:
            self._remove_new_file(target)
            raise
        except AuditWriteError:
            self._remove_new_file(target)
            raise
        except sqlite3.Error as exc:
            self._remove_new_file(target)
            logger.exception("更新照片关联失败（记录 %s）", record_id)
            raise DataAccessError("照片关联更新失败，原照片未变更") from exc
        except BaseException:
            # 任何非预期异常同样意味着事务没有成功提交，必须清理本次新文件；
            # 清理失败只记录日志，原始异常继续向上传播。
            self._remove_new_file(target)
            raise

        cleanup_failed, cleanup_message = self._cleanup_previous(
            previous_path, filename
        )
        return PhotoUpdateResult(
            record_id=record_id,
            photo_path=filename,
            previous_path=previous_path,
            cleanup_failed=cleanup_failed,
            cleanup_message=cleanup_message,
        )

    # ------------------------------------------------------------------ 文件

    def _ensure_photos_dir(self) -> Path:
        photos_dir = self.paths.photos_dir
        if photos_dir.exists():
            if not photos_dir.is_dir():
                raise PhotoStorageError("照片目录不可用：路径已被同名文件占用")
            return photos_dir
        try:
            photos_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PhotoStorageError("无法创建照片目录，请检查程序目录权限") from exc
        return photos_dir

    def _copy_new_file(self, source: Path, photos_dir: Path, stem: str) -> Path:
        """以独占方式创建目标文件并复制内容，绝不覆盖已有文件。"""
        candidates = [photos_dir / f"{stem}.jpg"]
        for _ in range(MAX_FILENAME_ATTEMPTS):
            candidates.append(photos_dir / f"{stem}_{uuid4().hex[:8]}.jpg")

        for candidate in candidates:
            try:
                handle = candidate.open("xb")
            except FileExistsError:
                continue
            except OSError as exc:
                raise PhotoStorageError(
                    "无法创建照片文件，请检查照片目录权限"
                ) from exc

            try:
                with handle, source.open("rb") as source_handle:
                    shutil.copyfileobj(source_handle, handle)
            except OSError as exc:
                self._remove_new_file(candidate)
                raise PhotoStorageError("复制照片失败，请检查文件权限") from exc
            except BaseException:
                # 复制未完整成功时，本次独占创建的目标文件必须尽量删除；
                # 清理失败只记录日志，非预期异常保持原样向上传播。
                self._remove_new_file(candidate)
                raise
            return candidate

        raise PhotoStorageError("无法分配照片文件名，请检查照片目录中的同名文件")

    @staticmethod
    def _remove_new_file(path: Path) -> None:
        """事务失败时尽力清理刚写入的新文件，不掩盖原始异常。"""
        try:
            path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001 - 补偿失败不得覆盖原始异常
            logger.exception("清理新照片失败：%s", path)

    def _cleanup_previous(
        self, previous_path: str | None, current_filename: str
    ) -> tuple[bool, str | None]:
        """事务成功后清理旧照片；任何保护条件不满足时保留文件。"""
        if not previous_path or previous_path == current_filename:
            return False, None

        try:
            old_file = photo_file_path(self.paths.photos_dir, previous_path)
        except ValueError as exc:
            logger.warning("旧照片清理失败：路径不安全（%s）：%s", previous_path, exc)
            return True, "旧照片路径不安全，未删除原文件"

        try:
            references = self.records.count_records_by_photo_path(previous_path)
        except (sqlite3.Error, ValueError) as exc:
            logger.warning("旧照片清理失败：无法确认引用（%s）：%s", previous_path, exc)
            return True, "无法确认旧照片是否被其他记录引用，未删除原文件"

        if references > 0:
            logger.info("旧照片仍被其他记录引用，保留文件：%s", previous_path)
            return False, None

        try:
            old_file.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("旧照片清理失败：%s（%s）", old_file, exc)
            return True, "旧照片文件未能删除，可稍后手动清理"
        return False, None

    # ------------------------------------------------------------------ 审计

    def _write_success_log(
        self,
        connection: sqlite3.Connection,
        record_id: int,
        student_no: str,
        filename: str,
    ) -> None:
        """成功审计与照片关联更新处于同一事务。"""
        summary = f"更新毕业照片：学号 {student_no}，文件 {filename}"
        try:
            self.logs.create(
                OP_PHOTO_UPDATE,
                TARGET_TYPE,
                str(record_id),
                summary,
                "success",
                connection=connection,
            )
        except sqlite3.Error as exc:
            raise AuditWriteError("审计日志写入失败，照片关联已回滚") from exc
