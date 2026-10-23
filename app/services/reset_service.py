"""系统重置 Service。

职责边界：

- 数据库清理与默认数据恢复在单个 SQLite 事务内完成；任何一步失败都整体回滚；
- 照片文件在事务提交前先移入受管理的暂存目录，提交失败时移回原处；
- 只有数据库提交成功后才删除暂存文件；暂存清理失败会如实报告并保留路径；
- 操作历史在事务内清空后写入一条重置成功审计，保证该记录不会被同一次重置清除；
- 不使用删除数据库文件的方式重建数据库，``schema_meta`` 始终保留；
- 不触碰 QSettings 界面偏好、程序资源和未受管理的文件。
"""

from __future__ import annotations

import logging
import shutil
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from app.config.paths import AppPaths
from app.data.db import Database
from app.data.db.seed import seed_defaults
from app.repositories import (
    AppSettingsRepository,
    CertificateRecordRepository,
    CertificateTemplateRepository,
    ImportBatchRepository,
    NumberingRuleRepository,
    NumberSequenceRepository,
    OperationLogRepository,
    PrintRecordRepository,
)
from app.services.errors import ResetError
from app.services.template_service import TemplateService
from app.utils.logging_reset import truncate_log_file

logger = logging.getLogger(__name__)

RESET_OPERATION_TYPE = "system_reset"
RESET_TARGET_TYPE = "system"
STAGING_PREFIX = ".reset_staging_"


@dataclass(frozen=True, slots=True)
class ResetScope:
    """重置范围说明，供确认界面展示。"""

    clears: tuple[str, ...]
    keeps: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResetResult:
    """一次系统重置的真实结果。"""

    success: bool
    message: str
    warnings: tuple[str, ...]
    photos_removed: int
    counts: Mapping[str, int]
    staging_path: Path | None
    audit_log_id: int | None
    audit_saved: bool


class ResetService:
    """系统重置编排入口；UI 只调用本 Service，不直接删表或删文件。"""

    def __init__(
        self,
        database: Database,
        paths: AppPaths | None = None,
        *,
        template_service: TemplateService | None = None,
        records: CertificateRecordRepository | None = None,
        prints: PrintRecordRepository | None = None,
        batches: ImportBatchRepository | None = None,
        templates: CertificateTemplateRepository | None = None,
        rules: NumberingRuleRepository | None = None,
        sequences: NumberSequenceRepository | None = None,
        settings: AppSettingsRepository | None = None,
        logs: OperationLogRepository | None = None,
    ) -> None:
        self.database = database
        self.paths = paths
        self.template_service = (
            template_service
            if template_service is not None
            else TemplateService(database, paths)
        )
        self.records = records if records is not None else CertificateRecordRepository(database)
        self.prints = prints if prints is not None else PrintRecordRepository(database)
        self.batches = batches if batches is not None else ImportBatchRepository(database)
        self.templates = (
            templates if templates is not None else CertificateTemplateRepository(database)
        )
        self.rules = rules if rules is not None else NumberingRuleRepository(database)
        self.sequences = (
            sequences if sequences is not None else NumberSequenceRepository(database)
        )
        self.settings = (
            settings if settings is not None else AppSettingsRepository(database)
        )
        self.logs = logs if logs is not None else OperationLogRepository(database)

    # ------------------------------------------------------------------ 范围

    @staticmethod
    def describe_scope() -> ResetScope:
        """返回重置的清理与保留清单。"""
        return ResetScope(
            clears=(
                "全部学生记录及其证书编号",
                "全部证书打印记录与打印审计",
                "全部 Excel 导入批次（含失败批次）",
                "全部操作日志（操作审计历史）",
                "全部自定义模板（恢复三份内置模板）",
                "全部自定义业务设置（恢复默认值）",
                "三类编号规则与流水（流水恢复为 1）",
                "受管理的学生照片文件（photos 目录内的文件）",
                "应用运行日志内容（logs/app.log）",
            ),
            keeps=(
                "数据库文件与数据库结构（表结构、schema 版本等初始化元数据）",
                "内置模板资源与程序文件（resources/、可执行文件）",
                "界面偏好（QSettings 中的窗口大小与位置）",
                "photos 目录中的子目录与未受管理文件",
            ),
        )

    # ------------------------------------------------------------------ 执行

    def reset_system(self) -> ResetResult:
        """执行系统重置并返回真实结果，不抛业务异常掩盖部分状态。"""
        if self.paths is None:
            raise ResetError("未配置程序路径，无法执行系统重置")

        staging = self.paths.db_dir / f"{STAGING_PREFIX}{uuid.uuid4().hex}"
        warnings: list[str] = []
        try:
            staging.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            raise ResetError(f"无法创建照片暂存目录，系统未重置：{exc}") from exc

        staged: list[str] = []
        try:
            self._stage_photos(staging, staged, warnings)
        except OSError as exc:
            logger.exception("系统重置：照片暂存失败")
            warnings.extend(self._restore_photos(staging, staged))
            self._remove_staging_quiet(staging, warnings)
            return self._failure_result(
                f"系统重置失败：整理照片文件时出错，系统未重置（{exc}）", warnings
            )

        try:
            counts, audit_id = self._reset_database(staged_count=len(staged))
        except Exception as exc:  # noqa: BLE001 - 需要报告真实失败状态
            logger.exception("系统重置：数据库清理失败，事务已回滚")
            warnings.extend(self._restore_photos(staging, staged))
            self._remove_staging_quiet(staging, warnings)
            return self._failure_result(
                f"系统重置失败：{exc}；数据库已回滚，业务数据未改变", warnings
            )

        staging_path: Path | None = None
        try:
            _remove_tree(staging)
        except OSError as exc:
            logger.warning("系统重置：照片暂存目录清理失败：%s", exc)
            staging_path = staging
            warnings.append(
                f"照片暂存目录未能删除，请手动清理：{staging}"
            )

        log_warning = truncate_log_file(self.paths.log_file)
        if log_warning:
            warnings.append(log_warning)

        summary = _success_message(counts, len(staged))
        return ResetResult(
            success=True,
            message=summary,
            warnings=tuple(warnings),
            photos_removed=len(staged),
            counts=counts,
            staging_path=staging_path,
            audit_log_id=audit_id,
            audit_saved=audit_id is not None,
        )

    # ------------------------------------------------------------------ 数据库

    def _reset_database(self, *, staged_count: int) -> tuple[dict[str, int], int]:
        with self.database.transaction() as connection:
            counts = {
                "print_records": self.prints.delete_all(connection=connection),
                "certificate_records": self.records.delete_all(connection=connection),
                "import_batches": self.batches.delete_all(connection=connection),
                "certificate_templates": self.templates.delete_all(connection=connection),
                "numbering_rules": self.rules.delete_all(connection=connection),
                "number_sequences": self.sequences.delete_all(connection=connection),
                "app_settings": self.settings.delete_all(connection=connection),
                "operation_logs": self.logs.delete_all(connection=connection),
            }

            seed_defaults(connection)
            self.template_service.seed_builtin_templates(connection)

            counts["photos"] = staged_count
            audit_id = self.logs.create(
                RESET_OPERATION_TYPE,
                RESET_TARGET_TYPE,
                None,
                _success_message(counts, staged_count),
                "success",
                connection=connection,
            )
            return counts, audit_id

    # ------------------------------------------------------------------ 文件

    def _stage_photos(
        self, staging: Path, staged: list[str], warnings: list[str]
    ) -> None:
        assert self.paths is not None
        photos_dir = self.paths.photos_dir
        photos_dir.mkdir(parents=True, exist_ok=True)
        for entry in sorted(photos_dir.iterdir(), key=lambda item: item.name):
            if entry.is_symlink():
                warnings.append(f"照片目录中存在符号链接，已保留：{entry.name}")
                continue
            if not entry.is_file():
                warnings.append(f"照片目录中存在非文件项，已保留：{entry.name}")
                continue
            _move_file(entry, staging / entry.name)
            staged.append(entry.name)

    def _restore_photos(self, staging: Path, staged: list[str]) -> list[str]:
        assert self.paths is not None
        warnings: list[str] = []
        remaining = 0
        for name in staged:
            source = staging / name
            if not source.exists():
                continue
            try:
                _move_file(source, self.paths.photos_dir / name)
            except OSError:
                remaining += 1
                logger.exception("恢复照片失败：%s", name)
        if remaining:
            warnings.append(
                f"{remaining} 个照片文件未能恢复，仍位于暂存目录：{staging}"
            )
        return warnings

    def _remove_staging_quiet(self, staging: Path, warnings: list[str]) -> None:
        try:
            _remove_tree(staging)
        except OSError:
            warnings.append(f"照片暂存目录未能清理：{staging}")

    # ------------------------------------------------------------------ 审计

    def _failure_result(self, message: str, warnings: list[str]) -> ResetResult:
        audit_id: int | None = None
        audit_saved = False
        try:
            audit_id = self.logs.create(
                RESET_OPERATION_TYPE,
                RESET_TARGET_TYPE,
                None,
                "系统重置失败",
                "failure",
                error_message=message,
            )
            audit_saved = True
        except Exception:  # noqa: BLE001 - 审计失败不得掩盖重置结果
            logger.exception("系统重置失败审计写入失败")
            warnings.append("失败审计记录未能保存，请查看运行日志")

        return ResetResult(
            success=False,
            message=message,
            warnings=tuple(warnings),
            photos_removed=0,
            counts={},
            staging_path=None,
            audit_log_id=audit_id,
            audit_saved=audit_saved,
        )


def _move_file(source: Path, target: Path) -> None:
    """移动单个文件；失败时抛出 ``OSError``。"""
    source.replace(target)


def _remove_tree(path: Path) -> None:
    """删除暂存目录树；目录不存在视为成功。"""
    if path.exists():
        shutil.rmtree(path)


def _success_message(counts: Mapping[str, int], photos: int) -> str:
    return (
        "系统重置完成："
        f"清除学生记录 {counts.get('certificate_records', 0)} 条、"
        f"打印记录 {counts.get('print_records', 0)} 条、"
        f"导入批次 {counts.get('import_batches', 0)} 条、"
        f"操作日志 {counts.get('operation_logs', 0)} 条、"
        f"自定义模板 {counts.get('certificate_templates', 0)} 份、"
        f"照片 {photos} 个；已恢复内置模板、默认编号规则与流水起始值"
    )
