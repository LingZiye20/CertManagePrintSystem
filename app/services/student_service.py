"""学生/证书业务记录 Service。

职责边界：

- 组织筛选、排序与分页参数，并调用 Repository 完成数据访问；
- 对 37 个 Excel 业务字段做基础校验，字段清单复用 ``EXCEL_FIELD_NAMES``；
- 把可预期的业务失败转换为 UI 可展示的 ``ServiceError``；
- 成功业务写入与成功审计日志处于同一事务，任何一个失败都整体回滚；
- 失败操作的审计为独立 best-effort 尝试，绝不覆盖原始业务错误。
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from math import ceil
from typing import Any

from app.data.db import EXCEL_FIELD_NAMES, Database
from app.repositories import CertificateRecordRepository, OperationLogRepository
from app.repositories.certificate_records import FILTERABLE_FIELDS, SORTABLE_FIELDS
from app.services.errors import (
    AuditWriteError,
    DataAccessError,
    DeleteBlockedError,
    DuplicateStudentNoError,
    DuplicateValueError,
    RecordNotFoundError,
    ValidationError,
)

logger = logging.getLogger(__name__)

TARGET_TYPE = "certificate_record"
# 日期字段只按文本原样保存，不做格式强制；清单仍供 Excel 导入识别原生日期单元格。
DATE_FIELDS: tuple[str, ...] = ("birth_date", "enrollment_date", "graduation_date")
DEFAULT_PAGE_SIZE = 20
ALLOWED_PAGE_SIZES: tuple[int, ...] = (20, 50, 100)


@dataclass(frozen=True, slots=True)
class StudentQuery:
    """一次学生数据查询的输入。"""

    filters: Mapping[str, str] = field(default_factory=dict)
    order_by: str = "student_no"
    descending: bool = False
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE


@dataclass(frozen=True, slots=True)
class StudentPage:
    """分页查询结果。"""

    records: list[dict[str, Any]]
    total: int
    page: int
    page_size: int
    page_count: int


class StudentService:
    """学生/证书业务记录的业务入口。"""

    def __init__(
        self,
        database: Database,
        *,
        records: CertificateRecordRepository | None = None,
        logs: OperationLogRepository | None = None,
    ) -> None:
        self.database = database
        self.records = records if records is not None else CertificateRecordRepository(database)
        self.logs = logs if logs is not None else OperationLogRepository(database)

    # ------------------------------------------------------------------ 查询

    def search(self, query: StudentQuery | None = None) -> StudentPage:
        """按筛选、排序与分页查询记录。

        页码越界时自动夹紧到最后一页，返回的 ``page`` 即实际页码。
        """
        resolved = query if query is not None else StudentQuery()
        if not isinstance(resolved, StudentQuery):
            raise ValidationError("查询参数类型不正确")

        self._validate_paging(resolved.page, resolved.page_size)
        if resolved.order_by not in SORTABLE_FIELDS:
            raise ValidationError(f"不支持的排序字段：{resolved.order_by}")
        if not isinstance(resolved.descending, bool):
            raise ValidationError("排序方向必须为布尔值")
        filters = self._normalize_filters(resolved.filters)

        try:
            total = self.records.count_records(filters=filters)
            page_count = max(1, ceil(total / resolved.page_size))
            page = min(resolved.page, page_count) if total else 1
            records = self.records.list_records(
                limit=resolved.page_size,
                offset=(page - 1) * resolved.page_size,
                order_by=resolved.order_by,
                descending=resolved.descending,
                filters=filters,
            )
        except sqlite3.Error as exc:
            logger.exception("查询学生记录失败")
            raise DataAccessError("查询学生数据失败，请稍后重试") from exc

        return StudentPage(
            records=records,
            total=total,
            page=page,
            page_size=resolved.page_size,
            page_count=page_count,
        )

    # ------------------------------------------------------------------ 写入

    def create_record(self, values: Mapping[str, Any]) -> int:
        """新增一条记录并返回主键；成功业务写入与审计在同一事务。"""
        try:
            data = self._normalize_values(values, require_student_no=True)
        except ValidationError as exc:
            self._audit_failure("student_create", None, values, str(exc))
            raise

        summary = _audit_summary("新增学生记录", data)
        try:
            with self.database.transaction() as connection:
                if (
                    self.records.get_by_student_no(data["student_no"], connection=connection)
                    is not None
                ):
                    raise DuplicateStudentNoError(f"学号 {data['student_no']} 已存在")
                record_id = self.records.create(data, connection=connection)
                self._write_success_log(
                    connection, "student_create", record_id, summary
                )
                return record_id
        except DuplicateStudentNoError as exc:
            self._audit_failure(
                "student_create", None, data, str(exc)
            )
            raise
        except sqlite3.IntegrityError as exc:
            error = DuplicateValueError("学号或证书编号与已有记录重复，请检查后重试")
            self._audit_failure("student_create", None, data, str(error))
            raise error from exc
        except AuditWriteError as exc:
            logger.exception("新增学生记录成功但审计写入失败，事务已回滚：%s", exc)
            raise
        except sqlite3.Error as exc:
            error = DataAccessError("新增学生记录失败，数据未保存")
            logger.exception("新增学生记录失败")
            self._audit_failure("student_create", None, data, str(error))
            raise error from exc

    def update_record(self, record_id: int, values: Mapping[str, Any]) -> None:
        """编辑指定记录；学号创建后不可修改。"""
        try:
            data = self._normalize_values(values, require_student_no=False)
            if not data:
                raise ValidationError("没有需要更新的字段")
        except ValidationError as exc:
            self._audit_failure("student_update", record_id, values, str(exc))
            raise

        try:
            with self.database.transaction() as connection:
                existing = self.records.get(record_id, connection=connection)
                if existing is None:
                    raise RecordNotFoundError(f"记录 {record_id} 不存在或已被删除")
                submitted_student_no = data.get("student_no")
                if (
                    submitted_student_no is not None
                    and submitted_student_no != existing["student_no"]
                ):
                    raise ValidationError(
                        f"学号 {existing['student_no']} 创建后不可修改，请保持不变"
                    )
                new_student_no = data.get("student_no", existing["student_no"])
                conflict = self.records.get_by_student_no(
                    new_student_no, connection=connection
                )
                if conflict is not None and conflict["id"] != record_id:
                    raise DuplicateStudentNoError(f"学号 {new_student_no} 已存在")
                if not self.records.update(record_id, data, connection=connection):
                    raise RecordNotFoundError(f"记录 {record_id} 不存在或已被删除")
                summary = _audit_summary("编辑学生记录", data, existing)
                self._write_success_log(
                    connection, "student_update", record_id, summary
                )
        except (ValidationError, DuplicateStudentNoError, RecordNotFoundError) as exc:
            self._audit_failure("student_update", record_id, data, str(exc))
            raise
        except sqlite3.IntegrityError as exc:
            error = DuplicateValueError("学号或证书编号与已有记录重复，请检查后重试")
            self._audit_failure("student_update", record_id, data, str(error))
            raise error from exc
        except AuditWriteError as exc:
            logger.exception("编辑学生记录成功但审计写入失败，事务已回滚：%s", exc)
            raise
        except sqlite3.Error as exc:
            error = DataAccessError("编辑学生记录失败，数据未保存")
            logger.exception("编辑学生记录失败")
            self._audit_failure("student_update", record_id, data, str(error))
            raise error from exc

    def delete_record(self, record_id: int) -> None:
        """删除指定记录；遵循当前 Schema 的真实外键关系，不绕过数据库约束。"""
        try:
            with self.database.transaction() as connection:
                existing = self.records.get(record_id, connection=connection)
                if existing is None:
                    raise RecordNotFoundError(f"记录 {record_id} 不存在或已被删除")
                if not self.records.delete(record_id, connection=connection):
                    raise RecordNotFoundError(f"记录 {record_id} 不存在或已被删除")
                summary = _audit_summary("删除学生记录", existing)
                self._write_success_log(
                    connection, "student_delete", record_id, summary
                )
        except RecordNotFoundError as exc:
            self._audit_failure("student_delete", record_id, {}, str(exc))
            raise
        except sqlite3.IntegrityError as exc:
            error = DeleteBlockedError("该记录仍被其他业务数据引用，无法删除")
            self._audit_failure("student_delete", record_id, {}, str(error))
            raise error from exc
        except AuditWriteError as exc:
            logger.exception("删除学生记录成功但审计写入失败，事务已回滚：%s", exc)
            raise
        except sqlite3.Error as exc:
            error = DataAccessError("删除学生记录失败，记录未删除")
            logger.exception("删除学生记录失败")
            self._audit_failure("student_delete", record_id, {}, str(error))
            raise error from exc

    # ------------------------------------------------------------------ 打印取数

    def list_for_print(
        self,
        filters: Mapping[str, str] | None = None,
        *,
        order_by: str = "student_no",
        descending: bool = False,
    ) -> list[dict[str, Any]]:
        """返回当前筛选与排序下的全部记录（不分页），供批量打印使用。

        Raises:
            ValidationError: 排序或筛选字段不在白名单内。
            DataAccessError: 数据库读取失败。
        """
        resolved_filters = self._normalize_filters(filters or {})
        if order_by not in SORTABLE_FIELDS:
            raise ValidationError(f"不支持的排序字段：{order_by}")
        if not isinstance(descending, bool):
            raise ValidationError("排序方向必须为布尔值")

        try:
            return self.records.list_records(
                order_by=order_by,
                descending=descending,
                filters=resolved_filters,
            )
        except sqlite3.Error as exc:
            logger.exception("读取打印数据失败")
            raise DataAccessError("读取打印数据失败，请稍后重试") from exc

    def list_ids(
        self,
        filters: Mapping[str, str] | None = None,
        *,
        order_by: str = "student_no",
        descending: bool = False,
        education_levels: Sequence[str] | None = None,
    ) -> list[int]:
        """返回当前筛选与排序下的全部记录 ID（不分页，顺序确定）。

        供批量编号使用：作用范围是当前筛选结果全集，跨分页。
        ``education_levels`` 非空时按教育层次精确过滤。

        Raises:
            ValidationError: 排序、筛选字段或层次条件不合法。
            DataAccessError: 数据库读取失败。
        """
        resolved_filters = self._normalize_filters(filters or {})
        if order_by not in SORTABLE_FIELDS:
            raise ValidationError(f"不支持的排序字段：{order_by}")
        if not isinstance(descending, bool):
            raise ValidationError("排序方向必须为布尔值")
        levels = _normalize_education_levels(education_levels)

        try:
            return self.records.list_ids(
                order_by=order_by,
                descending=descending,
                filters=resolved_filters,
                education_levels=levels,
            )
        except sqlite3.Error as exc:
            logger.exception("读取筛选记录 ID 失败")
            raise DataAccessError("读取筛选记录失败，请稍后重试") from exc

    def records_by_ids(self, record_ids: Sequence[int]) -> list[dict[str, Any]]:
        """按给定顺序返回记录快照；不存在的 ID 直接忽略。

        Raises:
            DataAccessError: 数据库读取失败。
        """
        requested = list(
            dict.fromkeys(
                record_id
                for record_id in record_ids
                if isinstance(record_id, int) and not isinstance(record_id, bool)
            )
        )
        if not requested:
            return []

        try:
            rows = self.records.list_by_ids(requested)
        except sqlite3.Error as exc:
            logger.exception("按 ID 读取打印数据失败")
            raise DataAccessError("读取打印数据失败，请稍后重试") from exc

        by_id = {row["id"]: row for row in rows}
        return [by_id[record_id] for record_id in requested if record_id in by_id]

    # ------------------------------------------------------------------ 内部

    @staticmethod
    def _validate_paging(page: int, page_size: int) -> None:
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            raise ValidationError("页码必须是大于等于 1 的整数")
        if (
            not isinstance(page_size, int)
            or isinstance(page_size, bool)
            or page_size not in ALLOWED_PAGE_SIZES
        ):
            raise ValidationError(
                f"每页条数必须是 {'、'.join(str(size) for size in ALLOWED_PAGE_SIZES)} 之一"
            )

    @staticmethod
    def _normalize_filters(filters: Mapping[str, str]) -> dict[str, str]:
        if not isinstance(filters, Mapping):
            raise ValidationError("筛选条件必须是字段映射")
        unknown = [key for key in filters if key not in FILTERABLE_FIELDS]
        if unknown:
            raise ValidationError(f"不支持的筛选字段：{'、'.join(unknown)}")

        normalized: dict[str, str] = {}
        for key, raw in filters.items():
            if raw is None:
                continue
            if not isinstance(raw, str):
                raise ValidationError(f"筛选条件 {key} 必须为文本")
            text = raw.strip()
            if text:
                normalized[key] = text
        return normalized

    @staticmethod
    def normalize_text_field(name: str, raw: Any) -> str | None:
        """清洗单个文本字段：去首尾空白，空字符串转为 ``None``。"""
        if raw is None:
            return None
        if not isinstance(raw, str):
            raise ValidationError(f"字段 {name} 必须为文本")
        return raw.strip() or None

    @staticmethod
    def _normalize_values(
        values: Mapping[str, Any],
        *,
        require_student_no: bool,
    ) -> dict[str, Any]:
        """清洗并校验业务输入，返回可直接写入 Repository 的字段映射。"""
        if not isinstance(values, Mapping):
            raise ValidationError("记录数据必须是字段映射")

        unknown = [key for key in values if key not in EXCEL_FIELD_NAMES]
        if unknown:
            raise ValidationError(f"包含不支持的字段：{'、'.join(unknown)}")

        normalized: dict[str, Any] = {}
        for name, raw in values.items():
            normalized[name] = StudentService.normalize_text_field(name, raw)

        if require_student_no:
            if not normalized.get("student_no"):
                raise ValidationError("学号不能为空")
        elif "student_no" in normalized and not normalized["student_no"]:
            raise ValidationError("学号不能为空")

        return normalized

    def _write_success_log(
        self,
        connection: sqlite3.Connection,
        operation_type: str,
        record_id: int,
        summary: str,
    ) -> None:
        """成功审计：与业务写入同一事务，失败则抛出并回滚。"""
        try:
            self.logs.create(
                operation_type,
                TARGET_TYPE,
                str(record_id),
                summary,
                "success",
                connection=connection,
            )
        except sqlite3.Error as exc:
            raise AuditWriteError("审计日志写入失败，操作已回滚") from exc

    def _audit_failure(
        self,
        operation_type: str,
        target_id: int | None,
        values: Mapping[str, Any],
        message: str,
    ) -> None:
        """失败审计：独立 best-effort，绝不覆盖原始业务错误。"""
        safe_values = values if isinstance(values, Mapping) else {}
        summary = _audit_summary("操作失败", safe_values)
        try:
            self.logs.create(
                operation_type,
                TARGET_TYPE,
                None if target_id is None else str(target_id),
                summary,
                "failure",
                error_message=message,
            )
        except Exception:  # noqa: BLE001 - 审计失败不得覆盖原始异常
            logger.exception("失败审计日志写入失败；原始错误：%s", message)


def _normalize_education_levels(
    levels: Sequence[str] | None,
) -> list[str] | None:
    """校验教育层次筛选：去空白、去重、丢弃空项；``None`` 表示不过滤。"""
    if levels is None:
        return None
    if isinstance(levels, (str, bytes)) or not isinstance(levels, Sequence):
        raise ValidationError("层次条件必须是字符串列表")

    normalized: list[str] = []
    for level in levels:
        if not isinstance(level, str):
            raise ValidationError("层次条件必须是字符串列表")
        text = level.strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized or None


def _audit_summary(
    action: str,
    values: Mapping[str, Any],
    record: Mapping[str, Any] | None = None,
) -> str:
    """生成不含身份证号等敏感信息的审计摘要。"""
    base = record if record is not None else {}
    student_no = values.get("student_no") or base.get("student_no") or "（空）"
    name = values.get("name") or base.get("name") or ""
    summary = f"{action}：学号 {student_no}"
    if name:
        summary += f"，姓名 {name}"
    return summary
