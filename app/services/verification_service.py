"""证书核验服务（只读）。

职责边界：

- 进入核验时固定记录 ID 与顺序，以及筛选与排序快照；
- 会话期间只读：不写 `certificate_records`、不写审计、不修改编号规则；
- 每次切换记录都重新读取数据库当前值，不冻结字段内容；
- 状态只表达程序能确认的事实：可核对、缺少证书编号、记录已删除、读取失败；
- 不实现输入编号反查，也不声称证书真实或有效。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.data.db import Database
from app.repositories import CertificateRecordRepository
from app.services.errors import ValidationError
from app.services.numbering_service import CERTIFICATE_TYPES, CertificateType
from app.services.student_service import StudentService

logger = logging.getLogger(__name__)

# 核验状态：只描述程序可确认的事实，避免「证书有效/真实」等结论性措辞。
VERIFICATION_READY = "ready"
VERIFICATION_MISSING_NUMBER = "missing_number"
VERIFICATION_MISSING_RECORD = "missing_record"
VERIFICATION_READ_FAILED = "read_failed"

STATUS_LABELS: dict[str, str] = {
    VERIFICATION_READY: "可核对",
    VERIFICATION_MISSING_NUMBER: "缺少证书编号",
    VERIFICATION_MISSING_RECORD: "记录已删除",
    VERIFICATION_READ_FAILED: "读取失败",
}

DISPLAY_FIELDS: tuple[str, ...] = (
    "student_no",
    "name",
    "faculty_name",
    "major_name",
    "class_name",
)


@dataclass(frozen=True, slots=True)
class VerificationSession:
    """一次核验会话的固定集合与查询快照。"""

    certificate_type: str
    certificate_type_label: str
    number_field: str
    filters: Mapping[str, str]
    order_by: str
    descending: bool
    record_ids: tuple[int, ...]

    @property
    def total(self) -> int:
        return len(self.record_ids)


@dataclass(frozen=True, slots=True)
class VerificationEntry:
    """核验页面上的单条记录视图；``error`` 仅用于读取失败说明。"""

    index: int
    total: int
    record_id: int
    certificate_type: str
    status: str
    certificate_no: str | None = None
    student_no: str | None = None
    name: str | None = None
    faculty_name: str | None = None
    major_name: str | None = None
    class_name: str | None = None
    error: str | None = None

    @property
    def position(self) -> int:
        """从 1 开始的当前位置，便于界面显示「第 i / N 条」。"""
        return self.index + 1

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]


class VerificationService:
    """按固定筛选快照逐条核对证书编号的业务入口。"""

    def __init__(
        self,
        database: Database,
        *,
        students: StudentService | None = None,
        records: CertificateRecordRepository | None = None,
    ) -> None:
        self.database = database
        self.students = students if students is not None else StudentService(database)
        self.records = (
            records if records is not None else CertificateRecordRepository(database)
        )

    # ------------------------------------------------------------------ 会话

    def build_session(
        self,
        certificate_type: str,
        filters: Mapping[str, str] | None = None,
        *,
        order_by: str = "student_no",
        descending: bool = False,
    ) -> VerificationSession:
        """按当前筛选与排序固定记录 ID 顺序。

        复用 ``StudentService.list_for_print()`` 完成筛选白名单、排序白名单与
        参数校验，本方法不复制另一套规则。

        Raises:
            ValidationError: 证书类型、筛选字段或排序字段不合法。
            DataAccessError: 数据库读取失败。
        """
        type_info = self._type_info(certificate_type)
        rows = self.students.list_for_print(
            filters or {},
            order_by=order_by,
            descending=descending,
        )
        return self._session_from_rows(
            type_info,
            filters or {},
            order_by,
            descending,
            rows,
        )

    def reload(self, session: VerificationSession) -> VerificationSession:
        """按会话保存的筛选与排序重新获取集合，保留证书类型。"""
        return self.build_session(
            session.certificate_type,
            dict(session.filters),
            order_by=session.order_by,
            descending=session.descending,
        )

    # ------------------------------------------------------------------ 单条

    def load_entry(self, session: VerificationSession, index: int) -> VerificationEntry:
        """读取集合中指定位置的记录当前值，并给出核验状态。

        Raises:
            ValidationError: 索引非法或集合为空。
        """
        self._validate_index(session, index)
        record_id = session.record_ids[index]
        try:
            row = self.records.get(record_id)
        except Exception as exc:  # noqa: BLE001 - 读取异常不得误判为记录已删除
            logger.exception("核验读取记录失败（id=%s）", record_id)
            return self._failed_entry(session, index, record_id, f"数据库读取失败：{exc}")

        if row is None:
            return VerificationEntry(
                index=index,
                total=session.total,
                record_id=record_id,
                certificate_type=session.certificate_type,
                status=VERIFICATION_MISSING_RECORD,
            )

        number = self._number_value(row, session.number_field)
        return VerificationEntry(
            index=index,
            total=session.total,
            record_id=record_id,
            certificate_type=session.certificate_type,
            status=VERIFICATION_READY if number else VERIFICATION_MISSING_NUMBER,
            certificate_no=number or None,
            student_no=self._text_value(row, "student_no"),
            name=self._text_value(row, "name"),
            faculty_name=self._text_value(row, "faculty_name"),
            major_name=self._text_value(row, "major_name"),
            class_name=self._text_value(row, "class_name"),
        )

    # ------------------------------------------------------------------ 导航

    def previous_index(self, session: VerificationSession, index: int) -> int:
        self._validate_index(session, index)
        return max(index - 1, 0)

    def next_index(self, session: VerificationSession, index: int) -> int:
        self._validate_index(session, index)
        return min(index + 1, session.total - 1)

    def can_previous(self, session: VerificationSession, index: int) -> bool:
        self._validate_index(session, index)
        return index > 0

    def can_next(self, session: VerificationSession, index: int) -> bool:
        self._validate_index(session, index)
        return index < session.total - 1

    # ------------------------------------------------------------------ 内部

    def _session_from_rows(
        self,
        type_info: CertificateType,
        filters: Mapping[str, str],
        order_by: str,
        descending: bool,
        rows: list[dict[str, Any]],
    ) -> VerificationSession:
        return VerificationSession(
            certificate_type=type_info.key,
            certificate_type_label=type_info.label,
            number_field=type_info.number_field,
            filters=dict(filters),
            order_by=order_by,
            descending=descending,
            record_ids=tuple(int(row["id"]) for row in rows),
        )

    @staticmethod
    def _type_info(certificate_type: str) -> CertificateType:
        for type_info in CERTIFICATE_TYPES:
            if type_info.key == certificate_type:
                return type_info
        raise ValidationError(f"不支持的证书类型：{certificate_type}")

    @staticmethod
    def _validate_index(session: VerificationSession, index: int) -> None:
        if not session.record_ids:
            raise ValidationError("当前核验集合没有记录，请调整筛选后重新载入")
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or not 0 <= index < session.total
        ):
            raise ValidationError(f"核验位置必须在 0 到 {session.total - 1} 之间")

    @staticmethod
    def _failed_entry(
        session: VerificationSession,
        index: int,
        record_id: int,
        message: str,
    ) -> VerificationEntry:
        return VerificationEntry(
            index=index,
            total=session.total,
            record_id=record_id,
            certificate_type=session.certificate_type,
            status=VERIFICATION_READ_FAILED,
            error=message,
        )

    @staticmethod
    def _number_value(row: Mapping[str, Any], number_field: str) -> str:
        raw = row.get(number_field)
        return "" if raw is None else str(raw).strip()

    @staticmethod
    def _text_value(row: Mapping[str, Any], field: str) -> str | None:
        raw = row.get(field)
        if raw is None:
            return None
        text = str(raw)
        return text if text else None
