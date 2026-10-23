"""操作审计只读查询 Service。

界面只通过本 Service 读取 ``operation_logs``，不直接执行 SQL；
查询接口不提供任何修改或删除能力。
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from app.data.db import Database
from app.repositories import OperationLogRepository
from app.services.errors import DataAccessError, ValidationError

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 200
MAX_LIMIT = 1000


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """一条操作审计记录。"""

    id: int
    created_at: str
    operation_type: str
    target_type: str
    target_id: str | None
    summary: str
    result: str
    error_message: str | None


class AuditService:
    """操作审计的只读查询入口。"""

    def __init__(
        self,
        database: Database,
        *,
        logs: OperationLogRepository | None = None,
    ) -> None:
        self.database = database
        self.logs = logs if logs is not None else OperationLogRepository(database)

    def list_recent(
        self,
        *,
        operation_type: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> tuple[AuditEntry, ...]:
        """按时间倒序返回审计记录；``limit`` 限制在 1–1000。"""
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= MAX_LIMIT
        ):
            raise ValidationError(f"查询条数必须是 1 到 {MAX_LIMIT} 之间的整数")

        try:
            rows = self.logs.list_logs(operation_type=operation_type, limit=limit)
        except sqlite3.Error as exc:
            logger.exception("查询操作审计失败")
            raise DataAccessError("查询操作审计失败，请稍后重试") from exc

        return tuple(
            AuditEntry(
                id=int(row["id"]),
                created_at=row["created_at"],
                operation_type=row["operation_type"],
                target_type=row["target_type"],
                target_id=row["target_id"],
                summary=row["summary"],
                result=row["result"],
                error_message=row["error_message"],
            )
            for row in rows
        )

    def operation_types(self) -> tuple[str, ...]:
        """返回库中已出现的操作类型，供筛选下拉使用。"""
        try:
            return self.logs.distinct_operation_types()
        except sqlite3.Error as exc:
            logger.exception("读取操作类型失败")
            raise DataAccessError("读取操作类型失败，请稍后重试") from exc
