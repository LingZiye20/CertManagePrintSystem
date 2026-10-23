"""证书编号规则与流水编排 Service。

职责边界：

- 提供唯一的编号拼接逻辑，预览与正式生成共用；
- 预览只读，不写数据库、不递增流水、不占用编号；
- 正式生成在单个事务内完成规则读取、流水读取、编号计算、唯一性检查、
  编号写入、流水递增与成功审计，任一步骤失败都整体回滚；
- 编号冲突时立即回滚，不跳号、不消耗流水、不覆盖已有编号；
- 规则更新与成功审计处于同一事务，审计失败则规则更新回滚。
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.data.db import Database
from app.repositories import (
    CertificateRecordRepository,
    NumberSequenceRepository,
    NumberingRuleRepository,
    OperationLogRepository,
)
from app.services.errors import (
    AuditWriteError,
    DataAccessError,
    NumberConflictError,
    RecordNotFoundError,
    ServiceError,
    ValidationError,
)

logger = logging.getLogger(__name__)

TARGET_RECORD = "certificate_record"
TARGET_RULE = "numbering_rule"
OP_GENERATE = "certificate_number_generate"
OP_RULE_UPDATE = "numbering_rule_update"

# 序号位数统一范围：拼接、规则校验与界面输入共用。
MIN_SEQUENCE_WIDTH = 1
MAX_SEQUENCE_WIDTH = 20

FIXED_TEXT_FIELDS: tuple[str, ...] = (
    "fixed_text_1",
    "fixed_text_2",
    "fixed_text_3",
    "fixed_text_4",
)
RULE_WRITABLE_FIELDS: tuple[str, ...] = (
    "rule_name",
    *FIXED_TEXT_FIELDS,
    "sequence_width",
    "enabled",
)


@dataclass(frozen=True, slots=True)
class CertificateType:
    """一类证书的编号配置标识。"""

    key: str
    label: str
    number_field: str


CERTIFICATE_TYPES: tuple[CertificateType, ...] = (
    CertificateType("graduation", "毕结业证书", "certificate_no"),
    CertificateType("degree", "学位证书", "degree_certificate_no"),
    CertificateType("other", "其他证书", "other_certificate_no"),
)
CERTIFICATE_TYPE_MAP: dict[str, CertificateType] = {
    type_info.key: type_info for type_info in CERTIFICATE_TYPES
}


@dataclass(frozen=True, slots=True)
class NumberingRuleView:
    """一次规则读取的快照，``next_value`` 仅供展示。"""

    rule_key: str
    rule_name: str
    fixed_texts: tuple[str, str, str, str]
    sequence_width: int
    enabled: bool
    next_value: int


@dataclass(frozen=True, slots=True)
class NumberPreview:
    """编号预览结果；预览不写入数据库。"""

    rule_key: str
    label: str
    number: str
    sequence_value: int
    sequence_width: int


@dataclass(frozen=True, slots=True)
class GeneratedNumber:
    """正式生成结果。"""

    record_id: int
    rule_key: str
    label: str
    number: str
    sequence_value: int
    next_value: int


def compose_certificate_number(
    fixed_texts: Sequence[str],
    sequence_value: int,
    sequence_width: int,
) -> str:
    """按固定文本顺序拼接编号；空片段被忽略，不添加任何分隔符。

    固定文本只去除首尾空白，保留内部空格；序号左侧补零，超过配置位数时
    完整保留，不截断。序号位数必须在 1–20 之间。
    """
    if (
        not isinstance(sequence_value, int)
        or isinstance(sequence_value, bool)
        or sequence_value < 1
    ):
        raise ValueError("流水号必须是大于等于 1 的整数")
    if (
        not isinstance(sequence_width, int)
        or isinstance(sequence_width, bool)
        or not MIN_SEQUENCE_WIDTH <= sequence_width <= MAX_SEQUENCE_WIDTH
    ):
        raise ValueError(
            f"序号位数必须是 {MIN_SEQUENCE_WIDTH} 到 {MAX_SEQUENCE_WIDTH} 之间的整数"
        )

    parts: list[str] = []
    for text in fixed_texts:
        if not isinstance(text, str):
            raise ValueError("固定文本片段必须为文本")
        trimmed = text.strip()
        if trimmed:
            parts.append(trimmed)
    parts.append(str(sequence_value).zfill(sequence_width))
    return "".join(parts)


class NumberingService:
    """证书编号规则、预览与正式生成的业务入口。"""

    def __init__(
        self,
        database: Database,
        *,
        rules: NumberingRuleRepository | None = None,
        sequences: NumberSequenceRepository | None = None,
        records: CertificateRecordRepository | None = None,
        logs: OperationLogRepository | None = None,
    ) -> None:
        self.database = database
        self.rules = rules if rules is not None else NumberingRuleRepository(database)
        self.sequences = (
            sequences if sequences is not None else NumberSequenceRepository(database)
        )
        self.records = (
            records if records is not None else CertificateRecordRepository(database)
        )
        self.logs = logs if logs is not None else OperationLogRepository(database)

    # ------------------------------------------------------------------ 只读

    @staticmethod
    def available_types() -> tuple[CertificateType, ...]:
        """返回三类证书类型，顺序固定。"""
        return CERTIFICATE_TYPES

    def get_rule(self, rule_key: str) -> NumberingRuleView:
        """读取指定类型的规则与当前流水值；不存在或非文本键时报业务错误。"""
        type_info = _require_type(rule_key)
        try:
            with self.database.connect() as connection:
                return self._load_rule(type_info, connection)
        except sqlite3.Error as exc:
            logger.exception("读取编号规则失败")
            raise DataAccessError("读取编号规则失败，请稍后重试") from exc

    def preview(self, rule_key: str) -> NumberPreview:
        """按当前规则与当前流水值计算预览编号，不修改数据库。"""
        type_info = _require_type(rule_key)
        try:
            with self.database.connect() as connection:
                view = self._load_rule(type_info, connection)
        except sqlite3.Error as exc:
            logger.exception("预览证书编号失败")
            raise DataAccessError("预览证书编号失败，请稍后重试") from exc

        _ensure_enabled(view)
        _ensure_width(view)
        number = compose_certificate_number(
            view.fixed_texts, view.next_value, view.sequence_width
        )
        return NumberPreview(
            rule_key=type_info.key,
            label=type_info.label,
            number=number,
            sequence_value=view.next_value,
            sequence_width=view.sequence_width,
        )

    # ------------------------------------------------------------------ 规则

    def update_rule(self, rule_key: str, changes: Mapping[str, Any]) -> None:
        """更新规则并写入成功审计；两者处于同一事务。"""
        type_info = _require_type(rule_key)
        try:
            normalized = _normalize_rule_changes(changes)
        except ValidationError as exc:
            self._audit_failure(
                OP_RULE_UPDATE,
                type_info.key,
                f"更新编号规则失败：{type_info.label}",
                str(exc),
            )
            raise

        try:
            with self.database.transaction() as connection:
                if self.rules.get(type_info.key, connection=connection) is None:
                    raise ValidationError(f"编号规则不存在：{type_info.label}")
                if not self.rules.update(
                    type_info.key, normalized, connection=connection
                ):
                    raise ValidationError(f"编号规则不存在：{type_info.label}")
                self._write_success_log(
                    connection,
                    OP_RULE_UPDATE,
                    TARGET_RULE,
                    type_info.key,
                    f"更新编号规则：{type_info.label}",
                )
        except AuditWriteError as exc:
            logger.exception("更新编号规则成功但审计写入失败，事务已回滚：%s", exc)
            raise
        except ServiceError as exc:
            self._audit_failure(
                OP_RULE_UPDATE,
                type_info.key,
                f"更新编号规则失败：{type_info.label}",
                str(exc),
            )
            raise
        except sqlite3.IntegrityError as exc:
            error = ValidationError("编号规则取值不符合要求，更新已回滚")
            self._audit_failure(
                OP_RULE_UPDATE,
                type_info.key,
                f"更新编号规则失败：{type_info.label}",
                str(error),
            )
            raise error from exc
        except sqlite3.Error as exc:
            error = DataAccessError("更新编号规则失败，规则未保存")
            logger.exception("更新编号规则失败")
            self._audit_failure(
                OP_RULE_UPDATE,
                type_info.key,
                f"更新编号规则失败：{type_info.label}",
                str(error),
            )
            raise error from exc

    # ------------------------------------------------------------------ 生成

    def generate_number(self, record_id: int, rule_key: str) -> GeneratedNumber:
        """为指定记录正式生成编号；成功返回生成结果。

        冲突、记录缺失或数据库错误都会整体回滚，流水不会因此递增。
        """
        type_info = _require_type(rule_key)
        if not isinstance(record_id, int) or isinstance(record_id, bool) or record_id < 1:
            error = ValidationError("记录 ID 必须是正整数")
            self._audit_failure(
                OP_GENERATE,
                str(record_id),
                f"生成{type_info.label}编号失败",
                str(error),
            )
            raise error

        try:
            with self.database.transaction() as connection:
                record = self.records.get(record_id, connection=connection)
                if record is None:
                    raise RecordNotFoundError(f"记录 {record_id} 不存在或已被删除")

                view = self._load_rule(type_info, connection)
                _ensure_enabled(view)
                _ensure_width(view)

                existing = record.get(type_info.number_field)
                if existing:
                    raise NumberConflictError(
                        f"该记录已存在{type_info.label}编号 {existing}，不允许覆盖"
                    )

                number = compose_certificate_number(
                    view.fixed_texts, view.next_value, view.sequence_width
                )
                if self.records.exists_by_number(
                    type_info.number_field, number, connection=connection
                ):
                    raise NumberConflictError(
                        f"{type_info.label}编号 {number} 已存在，本次生成已回滚，"
                        "未消耗流水号"
                    )

                if not self.records.update(
                    record_id,
                    {type_info.number_field: number},
                    connection=connection,
                ):
                    raise RecordNotFoundError(f"记录 {record_id} 不存在或已被删除")

                if not self.sequences.update_next_value(
                    type_info.key, view.next_value + 1, connection=connection
                ):
                    raise DataAccessError("编号流水更新失败，本次生成已回滚")

                self._write_success_log(
                    connection,
                    OP_GENERATE,
                    TARGET_RECORD,
                    str(record_id),
                    f"生成{type_info.label}编号：学号 {record.get('student_no') or '（空）'}"
                    f"，编号 {number}",
                )
                return GeneratedNumber(
                    record_id=record_id,
                    rule_key=type_info.key,
                    label=type_info.label,
                    number=number,
                    sequence_value=view.next_value,
                    next_value=view.next_value + 1,
                )
        except AuditWriteError as exc:
            logger.exception("生成证书编号成功但审计写入失败，事务已回滚：%s", exc)
            raise
        except ServiceError as exc:
            self._audit_failure(
                OP_GENERATE,
                str(record_id),
                f"生成{type_info.label}编号失败",
                str(exc),
            )
            raise
        except sqlite3.IntegrityError as exc:
            error = NumberConflictError(
                f"{type_info.label}编号与已有编号冲突，本次生成已回滚，未消耗流水号"
            )
            self._audit_failure(
                OP_GENERATE,
                str(record_id),
                f"生成{type_info.label}编号失败",
                str(error),
            )
            raise error from exc
        except sqlite3.Error as exc:
            error = DataAccessError("生成证书编号失败，编号未保存")
            logger.exception("生成证书编号失败")
            self._audit_failure(
                OP_GENERATE,
                str(record_id),
                f"生成{type_info.label}编号失败",
                str(error),
            )
            raise error from exc

    # ------------------------------------------------------------------ 内部

    def _load_rule(
        self,
        type_info: CertificateType,
        connection: sqlite3.Connection,
    ) -> NumberingRuleView:
        row = self.rules.get(type_info.key, connection=connection)
        if row is None:
            raise ValidationError(f"编号规则不存在：{type_info.label}")
        sequence = self.sequences.get(type_info.key, connection=connection)
        if sequence is None:
            raise DataAccessError(
                f"{type_info.label}的流水记录不存在，请检查数据库初始化"
            )
        return NumberingRuleView(
            rule_key=type_info.key,
            rule_name=row["rule_name"],
            fixed_texts=tuple(row[field] or "" for field in FIXED_TEXT_FIELDS),
            sequence_width=int(row["sequence_width"]),
            enabled=bool(row["enabled"]),
            next_value=int(sequence["next_value"]),
        )

    def _write_success_log(
        self,
        connection: sqlite3.Connection,
        operation_type: str,
        target_type: str,
        target_id: str,
        summary: str,
    ) -> None:
        """成功审计：与业务写入同一事务，失败则抛出并回滚。"""
        try:
            self.logs.create(
                operation_type,
                target_type,
                target_id,
                summary,
                "success",
                connection=connection,
            )
        except sqlite3.Error as exc:
            raise AuditWriteError("审计日志写入失败，操作已回滚") from exc

    def _audit_failure(
        self,
        operation_type: str,
        target_id: str | None,
        summary: str,
        message: str,
    ) -> None:
        """失败审计：独立 best-effort，不覆盖原始业务错误。"""
        try:
            self.logs.create(
                operation_type,
                TARGET_RULE if operation_type == OP_RULE_UPDATE else TARGET_RECORD,
                target_id,
                summary,
                "failure",
                error_message=message,
            )
        except Exception:  # noqa: BLE001 - 审计失败不得覆盖原始异常
            logger.exception("失败审计日志写入失败；原始错误：%s", message)


def _require_type(rule_key: str) -> CertificateType:
    """校验证书类型键并返回对应配置。"""
    if not isinstance(rule_key, str) or rule_key not in CERTIFICATE_TYPE_MAP:
        raise ValidationError(f"不支持的证书类型：{rule_key}")
    return CERTIFICATE_TYPE_MAP[rule_key]


def _ensure_enabled(view: NumberingRuleView) -> None:
    if not view.enabled:
        raise ValidationError(f"编号规则已停用，无法预览或生成编号：{view.rule_name}")


def _ensure_width(view: NumberingRuleView) -> None:
    """数据库中的异常位数要转换为可读业务错误，避免生成超长编号。"""
    if not MIN_SEQUENCE_WIDTH <= view.sequence_width <= MAX_SEQUENCE_WIDTH:
        raise ValidationError(
            f"{view.rule_name}的序号位数配置为 {view.sequence_width}，"
            f"不在 {MIN_SEQUENCE_WIDTH}–{MAX_SEQUENCE_WIDTH} 范围内，"
            "请先到编号规则设置中修正"
        )


def _normalize_rule_changes(changes: Mapping[str, Any]) -> dict[str, Any]:
    """校验并清洗规则更新输入。"""
    if not isinstance(changes, Mapping):
        raise ValidationError("规则变更必须是字段映射")
    unknown = [key for key in changes if key not in RULE_WRITABLE_FIELDS]
    if unknown:
        raise ValidationError(f"包含不支持的规则字段：{'、'.join(unknown)}")
    if not changes:
        raise ValidationError("没有需要更新的规则字段")

    normalized: dict[str, Any] = {}
    for name, raw in changes.items():
        if name == "rule_name":
            if not isinstance(raw, str) or not raw.strip():
                raise ValidationError("规则名称不能为空")
            normalized[name] = raw.strip()
        elif name in FIXED_TEXT_FIELDS:
            if not isinstance(raw, str):
                raise ValidationError(f"固定文本片段 {name} 必须为文本")
            normalized[name] = raw.strip()
        elif name == "sequence_width":
            if (
                not isinstance(raw, int)
                or isinstance(raw, bool)
                or not MIN_SEQUENCE_WIDTH <= raw <= MAX_SEQUENCE_WIDTH
            ):
                raise ValidationError(
                    f"序号位数必须是 {MIN_SEQUENCE_WIDTH} 到 {MAX_SEQUENCE_WIDTH} "
                    "之间的整数"
                )
            normalized[name] = raw
        elif name == "enabled":
            if isinstance(raw, bool):
                normalized[name] = int(raw)
            elif isinstance(raw, int) and raw in (0, 1):
                normalized[name] = raw
            else:
                raise ValidationError("启用状态必须为布尔值或 0/1")
    return normalized
