"""固定 37 列 Excel 数据导出 Service。

职责边界：

- 复用 :meth:`StudentService.list_for_print` 按当前筛选与排序读取全量记录（只读）；
- 按 :data:`app.utils.excel_template.EXCEL_COLUMN_NAMES` 写出表头，数据行字段顺序与
  :data:`app.data.db.EXCEL_FIELD_NAMES` 一一对应；
- 所有单元格一律按文本写入，保留学号、考生号、证件号码与三类证书编号的前导零和原样；
- 成功导出后在同一数据库事务内写成功审计；写出失败时 best-effort 写失败审计，
  绝不覆盖原始错误；
- 不修改任何业务数据，不生成证书编号，不改变编号规则。
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from app.data.db import EXCEL_FIELD_NAMES, Database
from app.repositories import (
    CertificateRecordRepository,
    OperationLogRepository,
)
from app.services.errors import AuditWriteError, DataAccessError, ServiceError
from app.services.student_service import StudentQuery, StudentService
from app.utils.excel_template import EXCEL_COLUMN_NAMES, EXCEL_SHEET_TITLE

logger = logging.getLogger(__name__)

EXPORT_OPERATION_TYPE = "excel_export"
EXPORT_TARGET_TYPE = "certificate_record"

# 筛选字段的中文展示名，用于审计摘要中标记字段是否填写，不输出值本身。
_FILTER_LABELS: dict[str, str] = {
    "student_no": "学号",
    "name": "姓名",
    "class_name": "班级",
    "major_name": "专业",
    "faculty_name": "学院",
}


@dataclass(frozen=True, slots=True)
class ExportResult:
    """一次数据导出的结果。"""

    file_name: str
    record_count: int
    path: Path


class ExcelExportService:
    """把当前筛选结果导出为固定 37 列 ``.xlsx`` 文件。"""

    def __init__(
        self,
        database: Database,
        *,
        records: CertificateRecordRepository | None = None,
        logs: OperationLogRepository | None = None,
        student_service: StudentService | None = None,
    ) -> None:
        self.database = database
        self.records = records if records is not None else CertificateRecordRepository(database)
        self.logs = logs if logs is not None else OperationLogRepository(database)
        self.student_service = (
            student_service if student_service is not None else StudentService(database)
        )

    # ------------------------------------------------------------------ 入口

    def export(self, query: StudentQuery, path: Path | str) -> ExportResult:
        """按 ``query`` 的筛选与排序导出全量记录到 ``path``。

        忽略 ``query`` 的分页字段。所有单元格按文本写入，``None`` 写空字符串。
        成功后写成功审计；写出失败时写失败审计（best-effort）并抛出业务错误。

        Raises:
            ServiceError: 查询或写出失败时的可读业务错误。
            AuditWriteError: 文件已写出但成功审计写入失败（文件本身有效）。
        """
        if not isinstance(query, StudentQuery):
            raise DataAccessError("导出参数类型不正确")

        target = Path(path)
        file_name = target.name or str(target)
        existed_before = target.exists()

        # 1. 读取记录（只读，复用打印取数路径）
        try:
            records = self.student_service.list_for_print(
                query.filters,
                order_by=query.order_by,
                descending=query.descending,
            )
        except ServiceError:
            raise
        except Exception:  # noqa: BLE001 - 未预期错误记录日志并转业务错误
            logger.exception("读取导出数据失败")
            self._save_failed_audit(file_name, "读取导出数据失败")
            raise DataAccessError("读取导出数据失败，请稍后重试") from None

        # 2. 写工作簿
        try:
            self._write_workbook(target, records)
        except OSError as exc:
            logger.warning("写出导出文件失败：%s（%s）", target, exc)
            self._remove_partial_file(target, existed_before)
            self._save_failed_audit(file_name, f"写出文件失败：{exc.__class__.__name__}")
            raise DataAccessError(
                "导出数据失败，无法写入文件，请检查保存位置与权限后重试"
            ) from exc
        except Exception:  # noqa: BLE001 - 未预期错误记录日志并转业务错误
            logger.exception("写出导出文件时发生未预期错误：%s", target)
            self._remove_partial_file(target, existed_before)
            self._save_failed_audit(file_name, "写出文件时发生未预期错误")
            raise DataAccessError("导出数据失败，请查看日志后重试") from None

        # 3. 成功审计（与业务同事务；文件已写出，审计失败抛 AuditWriteError）
        summary = self._success_summary(file_name, len(records), query.filters)
        try:
            with self.database.transaction() as connection:
                self.logs.create(
                    EXPORT_OPERATION_TYPE,
                    EXPORT_TARGET_TYPE,
                    None,
                    summary,
                    "success",
                    connection=connection,
                )
        except sqlite3.Error as exc:
            logger.exception("导出成功审计写入失败：%s", exc)
            raise AuditWriteError("导出成功但审计日志写入失败，请查看日志") from exc

        return ExportResult(file_name=file_name, record_count=len(records), path=target)

    # ------------------------------------------------------------------ 写出

    @staticmethod
    def _write_workbook(path: Path, records: list[dict[str, Any]]) -> None:
        """把记录按固定 37 列顺序写入工作簿；所有单元格按文本处理。"""
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = EXCEL_SHEET_TITLE
        sheet.append(list(EXCEL_COLUMN_NAMES))

        for record in records:
            row = [
                "" if record.get(name) is None else str(record[name])
                for name in EXCEL_FIELD_NAMES
            ]
            sheet.append(row)

        workbook.save(str(path))
        workbook.close()

    # ------------------------------------------------------------------ 审计

    @staticmethod
    def _success_summary(
        file_name: str,
        record_count: int,
        filters: Any,
    ) -> str:
        """生成不含敏感信息的成功审计摘要。

        只标注筛选字段是否填写，不输出筛选值；证件号码不在筛选白名单内，不会出现。
        """
        parts = [f"导出 {file_name}：{record_count} 条"]
        filter_map = filters if isinstance(filters, dict) else dict(filters or {})
        if filter_map:
            flags = []
            for field, label in _FILTER_LABELS.items():
                value = filter_map.get(field)
                flags.append(f"{label}({'已填' if value else '空'})")
            parts.append("筛选：" + "、".join(flags))
        return "；".join(parts)

    def _save_failed_audit(self, file_name: str, reason: str) -> None:
        """失败审计：独立 best-effort，绝不覆盖原始业务错误。"""
        try:
            self.logs.create(
                EXPORT_OPERATION_TYPE,
                EXPORT_TARGET_TYPE,
                None,
                f"导出 {file_name} 失败",
                "failure",
                error_message=f"导出失败：文件 {file_name}；原因：{reason}",
            )
        except Exception:  # noqa: BLE001 - 审计失败不得覆盖原始异常
            logger.exception("保存导出失败审计失败；原始原因：%s", reason)

    @staticmethod
    def _remove_partial_file(path: Path, existed_before: bool) -> None:
        """写出失败时尽力清理本次新建的半截文件；不删除用户原有文件。"""
        if existed_before:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("清理未完成的导出文件失败：%s", path)
