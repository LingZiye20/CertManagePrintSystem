"""固定 37 列 Excel 模板导入 Service。

职责边界：

- 读取并校验整个工作簿后才开始写入业务数据；
- 严格匹配 :data:`app.utils.excel_template.EXCEL_COLUMN_NAMES` 的表头与顺序；
- 收集全部可定位的行级错误，任意错误都整批失败；
- 全部学生记录、成功批次和成功审计处于同一数据库事务；
- 失败批次与失败审计在业务事务回滚后通过独立事务 best-effort 保存；
- 不生成证书编号，不修改编号规则或流水。
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

import openpyxl

from app.data.db import EXCEL_FIELD_NAMES, Database
from app.repositories import (
    CertificateRecordRepository,
    ImportBatchRepository,
    OperationLogRepository,
)
from app.services.errors import AuditWriteError, ValidationError
from app.services.student_service import DATE_FIELDS, StudentService
from app.utils.excel_template import EXCEL_COLUMN_NAMES, EXCEL_COLUMN_COUNT

logger = logging.getLogger(__name__)

CERTIFICATE_NUMBER_FIELDS: tuple[str, ...] = (
    "certificate_no",
    "degree_certificate_no",
    "other_certificate_no",
)
IMPORT_OPERATION_TYPE = "excel_import"
IMPORT_TARGET_TYPE = "import_batch"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ImportIssue:
    """一条可定位的导入问题。"""

    row: int | None
    column: str | None
    field: str | None
    kind: str
    reason: str


@dataclass(frozen=True, slots=True)
class ImportResult:
    """一次导入尝试的完整结果。"""

    file_name: str
    status: str
    total_rows: int
    imported_count: int
    failed_count: int
    issues: tuple[ImportIssue, ...]
    batch_id: int | None
    batch_saved: bool
    warnings: tuple[str, ...]
    message: str


@dataclass(slots=True)
class _ParsedRow:
    row_number: int
    values: dict[str, Any]
    issues: list[ImportIssue] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _ScanResult:
    total_rows: int
    failed_count: int
    issues: tuple[ImportIssue, ...]
    rows: tuple[_ParsedRow, ...]
    fatal: bool


class _ConcurrentStudentNoError(Exception):
    """事务内复查发现学号已被其他操作占用。"""

    def __init__(self, student_nos: set[str]) -> None:
        self.student_nos = student_nos
        super().__init__("学号已被其他操作占用")


class ExcelImportService:
    """固定 37 列 Excel 模板的学生资料导入入口。"""

    def __init__(
        self,
        database: Database,
        *,
        records: CertificateRecordRepository | None = None,
        batches: ImportBatchRepository | None = None,
        logs: OperationLogRepository | None = None,
    ) -> None:
        self.database = database
        self.records = records if records is not None else CertificateRecordRepository(database)
        self.batches = batches if batches is not None else ImportBatchRepository(database)
        self.logs = logs if logs is not None else OperationLogRepository(database)

    # ------------------------------------------------------------------ 入口

    def import_file(self, path: Path) -> ImportResult:
        """读取并导入一个固定 37 列 ``.xlsx`` 文件。

        预期业务失败通过 :class:`ImportResult` 返回，不抛异常；未预期错误会
        记录日志并以失败结果返回，原始业务原因不会被失败批次/审计错误覆盖。
        """
        source = Path(path)
        started_at = _utc_now()
        file_name = source.name or str(source)

        if not source.is_file():
            return self._fail(
                file_name,
                0,
                [
                    ImportIssue(
                        row=None,
                        column=None,
                        field=None,
                        kind="file",
                        reason="文件不存在或不是普通文件",
                    )
                ],
                started_at,
                failed_count=0,
            )

        if source.suffix.lower() != ".xlsx":
            return self._fail(
                file_name,
                0,
                [
                    ImportIssue(
                        row=None,
                        column=None,
                        field=None,
                        kind="file",
                        reason="仅支持 .xlsx 格式的 Excel 文件",
                    )
                ],
                started_at,
                failed_count=0,
            )

        try:
            workbook = openpyxl.load_workbook(
                filename=source, read_only=True, data_only=True
            )
        except Exception:
            logger.exception("读取 Excel 文件失败：%s", source)
            return self._fail(
                file_name,
                0,
                [
                    ImportIssue(
                        row=None,
                        column=None,
                        field=None,
                        kind="file",
                        reason="无法读取 Excel 文件，请确认文件未损坏且为 .xlsx 格式",
                    )
                ],
                started_at,
                failed_count=0,
            )

        try:
            scan = self._scan_workbook(workbook)
        except Exception:
            logger.exception("解析 Excel 工作表失败：%s", source)
            return self._fail(
                file_name,
                0,
                [
                    ImportIssue(
                        row=None,
                        column=None,
                        field=None,
                        kind="file",
                        reason="读取工作表内容失败，请确认文件结构完整",
                    )
                ],
                started_at,
                failed_count=0,
            )
        finally:
            try:
                workbook.close()
            except Exception:  # noqa: BLE001 - 关闭失败不应掩盖导入结果
                logger.exception("关闭 Excel 工作簿失败：%s", source)

        if scan.fatal:
            return self._fail(
                file_name,
                scan.total_rows,
                scan.issues,
                started_at,
                failed_count=scan.failed_count,
            )

        candidates = [
            row
            for row in scan.rows
            if row.values.get("student_no")
            and not any(issue.field == "student_no" for issue in row.issues)
        ]
        try:
            existing = self.records.existing_student_nos(
                [row.values["student_no"] for row in candidates]
            )
        except Exception:
            logger.exception("查询数据库已有学号失败")
            return self._fail(
                file_name,
                scan.total_rows,
                [
                    ImportIssue(
                        row=None,
                        column=None,
                        field=None,
                        kind="database",
                        reason="查询数据库已有学号失败，本次导入未写入任何学生记录",
                    )
                ],
                started_at,
                failed_count=scan.total_rows,
            )

        for row in candidates:
            student_no = row.values["student_no"]
            if student_no in existing:
                row.issues.append(
                    ImportIssue(
                        row=row.row_number,
                        column="学号",
                        field="student_no",
                        kind="duplicate_in_db",
                        reason=f"学号 {student_no} 已存在于数据库",
                    )
                )

        issues = tuple(issue for row in scan.rows for issue in row.issues)
        failed_count = sum(1 for row in scan.rows if row.issues)
        if issues:
            return self._fail(
                file_name,
                scan.total_rows,
                issues,
                started_at,
                failed_count=failed_count,
            )

        return self._insert_rows(
            file_name,
            scan.rows,
            started_at,
            total_rows=scan.total_rows,
        )

    # ------------------------------------------------------------------ 写入

    def _insert_rows(
        self,
        file_name: str,
        rows: tuple[_ParsedRow, ...],
        started_at: str,
        *,
        total_rows: int,
    ) -> ImportResult:
        try:
            with self.database.transaction() as connection:
                conflicts = self.records.existing_student_nos(
                    [row.values["student_no"] for row in rows],
                    connection=connection,
                )
                if conflicts:
                    raise _ConcurrentStudentNoError(conflicts)

                for row in rows:
                    self.records.create(row.values, connection=connection)

                try:
                    batch_id = self.batches.create(
                        file_name,
                        STATUS_SUCCESS,
                        total_rows=total_rows,
                        started_at=started_at,
                        finished_at=_utc_now(),
                        connection=connection,
                    )
                except sqlite3.Error as exc:
                    raise AuditWriteError(
                        "导入批次记录写入失败，本次导入已全部回滚"
                    ) from exc

                try:
                    self.logs.create(
                        IMPORT_OPERATION_TYPE,
                        IMPORT_TARGET_TYPE,
                        str(batch_id),
                        f"导入 {file_name}：成功 {len(rows)} 条",
                        "success",
                        connection=connection,
                    )
                except sqlite3.Error as exc:
                    raise AuditWriteError(
                        "导入审计记录写入失败，本次导入已全部回滚"
                    ) from exc
        except _ConcurrentStudentNoError as exc:
            issues = [
                ImportIssue(
                    row=row.row_number,
                    column="学号",
                    field="student_no",
                    kind="duplicate_in_db",
                    reason=f"学号 {row.values['student_no']} 已存在于数据库",
                )
                for row in rows
                if row.values.get("student_no") in exc.student_nos
            ]
            if not issues:
                issues = [
                    ImportIssue(
                        row=None,
                        column=None,
                        field=None,
                        kind="database",
                        reason="学号与数据库已有记录冲突，本次导入已全部回滚",
                    )
                ]
            return self._fail(
                file_name,
                total_rows,
                issues,
                started_at,
                failed_count=len(rows),
            )
        except AuditWriteError as exc:
            logger.exception("导入成功审计或批次写入失败，业务事务已回滚")
            return self._fail(
                file_name,
                total_rows,
                [
                    ImportIssue(
                        row=None,
                        column=None,
                        field=None,
                        kind="audit",
                        reason=str(exc),
                    )
                ],
                started_at,
                failed_count=len(rows),
            )
        except sqlite3.IntegrityError:
            logger.exception("导入学生记录触发数据库唯一约束，业务事务已回滚")
            return self._fail(
                file_name,
                total_rows,
                [
                    ImportIssue(
                        row=None,
                        column=None,
                        field=None,
                        kind="database",
                        reason="学号与已有记录冲突或违反唯一约束，本次导入已全部回滚",
                    )
                ],
                started_at,
                failed_count=len(rows),
            )
        except sqlite3.Error:
            logger.exception("导入学生记录时数据库写入失败，业务事务已回滚")
            return self._fail(
                file_name,
                total_rows,
                [
                    ImportIssue(
                        row=None,
                        column=None,
                        field=None,
                        kind="database",
                        reason="数据库写入失败，本次导入已全部回滚",
                    )
                ],
                started_at,
                failed_count=len(rows),
            )
        except Exception:
            logger.exception("导入学生记录时发生未预期错误，业务事务已回滚")
            return self._fail(
                file_name,
                total_rows,
                [
                    ImportIssue(
                        row=None,
                        column=None,
                        field=None,
                        kind="internal",
                        reason="导入过程中发生未预期错误，本次导入已全部回滚",
                    )
                ],
                started_at,
                failed_count=len(rows),
            )

        return ImportResult(
            file_name=file_name,
            status=STATUS_SUCCESS,
            total_rows=total_rows,
            imported_count=len(rows),
            failed_count=0,
            issues=(),
            batch_id=batch_id,
            batch_saved=True,
            warnings=(),
            message=f"导入成功：{len(rows)} 条",
        )

    # ------------------------------------------------------------------ 解析

    def _scan_workbook(self, workbook: Any) -> _ScanResult:
        worksheets = list(workbook.worksheets)
        if len(worksheets) != 1:
            reason = (
                "工作簿中没有工作表"
                if not worksheets
                else f"工作簿必须只包含一个工作表，当前为 {len(worksheets)} 个"
            )
            return _ScanResult(
                total_rows=0,
                failed_count=0,
                issues=(
                    ImportIssue(
                        row=None,
                        column=None,
                        field=None,
                        kind="template",
                        reason=reason,
                    ),
                ),
                rows=(),
                fatal=True,
            )

        sheet = worksheets[0]
        all_rows = list(sheet.iter_rows(min_row=1, values_only=True))
        header = all_rows[0] if all_rows else ()
        data_rows = all_rows[1:]
        total_rows = sum(1 for row in data_rows if _row_has_content(row))

        header_issues = _validate_header(header)
        if header_issues:
            return _ScanResult(
                total_rows=total_rows,
                failed_count=total_rows,
                issues=tuple(header_issues),
                rows=(),
                fatal=True,
            )

        if total_rows == 0:
            return _ScanResult(
                total_rows=0,
                failed_count=0,
                issues=(
                    ImportIssue(
                        row=None,
                        column=None,
                        field=None,
                        kind="empty_data",
                        reason="工作表没有可导入的数据行",
                    ),
                ),
                rows=(),
                fatal=True,
            )

        parsed: list[_ParsedRow] = []
        for row_number, raw_row in enumerate(data_rows, start=2):
            if not _row_has_content(raw_row):
                continue
            parsed.append(self._parse_row(raw_row, row_number))

        _add_duplicate_issues(parsed)
        failed_count = sum(1 for row in parsed if row.issues)
        issues = tuple(issue for row in parsed for issue in row.issues)
        return _ScanResult(
            total_rows=total_rows,
            failed_count=failed_count,
            issues=issues,
            rows=tuple(parsed),
            fatal=False,
        )

    def _parse_row(self, raw_row: tuple[Any, ...], row_number: int) -> _ParsedRow:
        values: dict[str, Any] = {name: None for name in EXCEL_FIELD_NAMES}
        issues: list[ImportIssue] = []

        extra_index = next(
            (
                index
                for index in range(EXCEL_COLUMN_COUNT, len(raw_row))
                if _cell_has_content(raw_row[index])
            ),
            None,
        )
        if extra_index is not None:
            issues.append(
                ImportIssue(
                    row=row_number,
                    column=f"第 {extra_index + 1} 列",
                    field=None,
                    kind="extra_column",
                    reason="超出固定 37 列模板，请删除多余列",
                )
            )

        for index, name in enumerate(EXCEL_FIELD_NAMES):
            column = EXCEL_COLUMN_NAMES[index]
            raw = raw_row[index] if index < len(raw_row) else None
            normalized, issue = self._parse_cell(name, column, raw, row_number)
            values[name] = normalized
            if issue is not None:
                issues.append(issue)

        if not values.get("student_no") and not any(
            issue.field == "student_no" for issue in issues
        ):
            issues.append(
                ImportIssue(
                    row=row_number,
                    column="学号",
                    field="student_no",
                    kind="required",
                    reason="学号不能为空",
                )
            )

        for name in CERTIFICATE_NUMBER_FIELDS:
            if values.get(name) is None:
                continue
            index = EXCEL_FIELD_NAMES.index(name)
            issues.append(
                ImportIssue(
                    row=row_number,
                    column=EXCEL_COLUMN_NAMES[index],
                    field=name,
                    kind="certificate_no_not_empty",
                    reason="导入文件中的证书编号字段必须为空；不会自动清空或生成编号",
                )
            )

        return _ParsedRow(row_number=row_number, values=values, issues=issues)

    @staticmethod
    def _parse_cell(
        name: str,
        column: str,
        raw: Any,
        row_number: int,
    ) -> tuple[Any, ImportIssue | None]:
        if raw is None:
            return None, None

        if isinstance(raw, str):
            try:
                text = StudentService.normalize_text_field(name, raw)
            except ValidationError as exc:
                return None, ImportIssue(
                    row=row_number,
                    column=column,
                    field=name,
                    kind="type",
                    reason=f"{column}：{exc}",
                )
            return text, None

        if name in DATE_FIELDS and isinstance(raw, datetime):
            if raw.time() != time(0, 0):
                return None, ImportIssue(
                    row=row_number,
                    column=column,
                    field=name,
                    kind="date",
                    reason=f"{column} 含时间部分，请使用纯日期（YYYY-MM-DD）",
                )
            return raw.date().isoformat(), None

        if name in DATE_FIELDS and isinstance(raw, date):
            return raw.isoformat(), None

        if name == "student_no":
            reason = (
                f"{column} 在 Excel 中不是文本类型，可能已丢失前导零；"
                "请将学号列设为文本后重新保存"
            )
        else:
            reason = (
                f"{column} 不是文本单元格；为避免 Excel 自动转换造成数据改写，"
                "请将该列设为文本后重新保存"
            )
        return None, ImportIssue(
            row=row_number,
            column=column,
            field=name,
            kind="type",
            reason=reason,
        )

    # ------------------------------------------------------------------ 失败

    def _fail(
        self,
        file_name: str,
        total_rows: int,
        issues: Any,
        started_at: str,
        *,
        failed_count: int,
    ) -> ImportResult:
        issue_tuple = tuple(issues)
        message = (
            f"导入失败：未写入任何学生记录（{failed_count} 行未导入）"
            if failed_count
            else (
                f"导入失败：{issue_tuple[0].reason}"
                if issue_tuple
                else "导入失败：未写入任何学生记录"
            )
        )

        summary = _format_error_summary(issue_tuple, failed_count)
        batch_id, batch_saved, batch_warning = self._save_failed_batch(
            file_name,
            total_rows,
            summary,
            started_at,
        )
        audit_warning = self._save_failed_audit(
            file_name,
            batch_id,
            total_rows=total_rows,
            failed_count=failed_count,
            error_count=len(issue_tuple),
        )
        warnings = tuple(
            warning
            for warning in (batch_warning, audit_warning)
            if warning is not None
        )
        if not batch_saved:
            message = f"{message}；失败批次记录未能保存，请查看日志"

        return ImportResult(
            file_name=file_name,
            status=STATUS_FAILED,
            total_rows=total_rows,
            imported_count=0,
            failed_count=failed_count,
            issues=issue_tuple,
            batch_id=batch_id,
            batch_saved=batch_saved,
            warnings=warnings,
            message=message,
        )

    def _save_failed_batch(
        self,
        file_name: str,
        total_rows: int,
        error_summary: str,
        started_at: str,
    ) -> tuple[int | None, bool, str | None]:
        try:
            batch_id = self.batches.create(
                file_name,
                STATUS_FAILED,
                total_rows=total_rows,
                started_at=started_at,
                finished_at=_utc_now(),
                error_summary=error_summary,
            )
            return batch_id, True, None
        except Exception:  # noqa: BLE001 - 失败记录不能覆盖原始导入错误
            logger.exception("保存导入失败批次失败")
            return None, False, "失败批次记录未能保存，请查看日志"

    def _save_failed_audit(
        self,
        file_name: str,
        batch_id: int | None,
        *,
        total_rows: int,
        failed_count: int,
        error_count: int,
    ) -> str | None:
        """保存失败审计；详细字段错误由批次 ``error_summary`` 承担。

        审计自身写入失败只返回 warning，不修改调用方已生成的导入错误信息。
        """
        error_message = (
            f"导入失败：文件 {file_name}；总行数：{total_rows}；"
            f"失败行数：{failed_count}；错误数：{error_count}"
        )
        try:
            self.logs.create(
                IMPORT_OPERATION_TYPE,
                IMPORT_TARGET_TYPE,
                None if batch_id is None else str(batch_id),
                f"导入 {file_name} 失败",
                "failure",
                error_message=error_message,
            )
            return None
        except Exception:  # noqa: BLE001 - 失败记录不能覆盖原始导入错误
            logger.exception("保存导入失败审计失败")
            return "失败审计记录未能保存，请查看日志"


def _utc_now() -> str:
    """统一 UTC ISO-8601 毫秒时间戳，与 Schema 默认值格式一致。"""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _cell_has_content(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _row_has_content(row: Any) -> bool:
    if row is None:
        return False
    return any(_cell_has_content(value) for value in row)


def _display_header(value: Any) -> str:
    if value is None:
        return "（空）"
    text = str(value)
    if not text.strip():
        return "（空）"
    return text if len(text) <= 40 else f"{text[:37]}..."


def _validate_header(raw_header: Any) -> list[ImportIssue]:
    values = list(raw_header or ())
    while values and not _cell_has_content(values[-1]):
        values.pop()

    if not values:
        return [
            ImportIssue(
                row=1,
                column=None,
                field=None,
                kind="template",
                reason="第 1 行为空，缺少固定 37 列表头",
            )
        ]

    issues: list[ImportIssue] = []
    for index in range(min(len(values), EXCEL_COLUMN_COUNT)):
        expected = EXCEL_COLUMN_NAMES[index]
        actual = values[index]
        if not isinstance(actual, str) or actual != expected:
            issues.append(
                ImportIssue(
                    row=1,
                    column=expected,
                    field=EXCEL_FIELD_NAMES[index],
                    kind="template",
                    reason=(
                        f"第 {index + 1} 列应为「{expected}」，"
                        f"实际为「{_display_header(actual)}」"
                    ),
                )
            )

    if len(values) < EXCEL_COLUMN_COUNT:
        for index in range(len(values), EXCEL_COLUMN_COUNT):
            issues.append(
                ImportIssue(
                    row=1,
                    column=EXCEL_COLUMN_NAMES[index],
                    field=EXCEL_FIELD_NAMES[index],
                    kind="template",
                    reason=f"缺少第 {index + 1} 列「{EXCEL_COLUMN_NAMES[index]}」",
                )
            )
    elif len(values) > EXCEL_COLUMN_COUNT:
        for index in range(EXCEL_COLUMN_COUNT, len(values)):
            issues.append(
                ImportIssue(
                    row=1,
                    column=f"第 {index + 1} 列",
                    field=None,
                    kind="template",
                    reason=(
                        f"存在模板外列「{_display_header(values[index])}」，"
                        "固定模板只有 37 列"
                    ),
                )
            )

    return issues


def _add_duplicate_issues(rows: list[_ParsedRow]) -> None:
    seen: dict[str, int] = {}
    for row in rows:
        student_no = row.values.get("student_no")
        if not student_no:
            continue
        if any(issue.field == "student_no" for issue in row.issues):
            continue
        first_row = seen.get(student_no)
        if first_row is None:
            seen[student_no] = row.row_number
            continue
        row.issues.append(
            ImportIssue(
                row=row.row_number,
                column="学号",
                field="student_no",
                kind="duplicate_in_file",
                reason=f"学号 {student_no} 与第 {first_row} 行重复",
            )
        )


def _format_error_summary(
    issues: tuple[ImportIssue, ...],
    failed_count: int,
) -> str:
    lines: list[str] = []
    for issue in issues:
        location = f"第 {issue.row} 行" if issue.row is not None else "文件"
        column = issue.column or issue.field or "-"
        lines.append(f"{location} · {column}：{issue.reason}")
    lines.append(
        f"共 {failed_count} 行失败，{len(issues)} 个错误；本次导入未写入任何学生记录。"
    )
    return "\n".join(lines)
