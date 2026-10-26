"""证书编号规则与批量编号编排 Service。

职责边界：

- 提供唯一的编号拼接逻辑，预览与正式编号共用；
- 预览只读，不写数据库、不占用编号；起始流水号由用户手动输入；
- 批量编号在单个事务内完成规则读取、编号计算、唯一性检查、编号写入与成功审计，
  任一步骤失败都整体回滚；
- 编号冲突时立即回滚，不产生部分编号、不覆盖已有编号；
- 规则更新与成功审计处于同一事务，审计失败则规则更新回滚。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.data.db import Database
from app.repositories import (
    AppSettingsRepository,
    CertificateRecordRepository,
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
OP_CLEAR = "certificate_number_clear"

# 「记住上次证书类型」的业务配置键；值必须是三类类型键之一。
LAST_RULE_KEY = "numbering.last_rule_key"
DEFAULT_RULE_KEY = "graduation"

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
    *FIXED_TEXT_FIELDS,
    "sequence_width",
    "education_levels",
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
    """一次规则读取的快照。"""

    rule_key: str
    fixed_texts: tuple[str, str, str, str]
    sequence_width: int
    education_levels: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NumberPreview:
    """编号预览结果；预览不写入数据库。"""

    rule_key: str
    label: str
    number: str
    sequence_value: int
    sequence_width: int


@dataclass(frozen=True, slots=True)
class NumberingPlan:
    """批量编号的只读计划，用于确认弹窗展示。"""

    rule_key: str
    label: str
    education_levels: tuple[str, ...]
    total_count: int
    matched_count: int
    level_skipped_count: int
    skipped_count: int
    pending_count: int
    start_sequence: int
    first_number: str | None
    last_number: str | None


@dataclass(frozen=True, slots=True)
class BulkNumberingResult:
    """批量编号的执行结果。"""

    rule_key: str
    label: str
    education_levels: tuple[str, ...]
    numbered_count: int
    skipped_count: int
    level_skipped_count: int
    first_number: str | None
    last_number: str | None


@dataclass(frozen=True, slots=True)
class NumberClearingResult:
    """清空某类证书编号的执行结果。"""

    rule_key: str
    label: str
    cleared_count: int


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
        records: CertificateRecordRepository | None = None,
        logs: OperationLogRepository | None = None,
        settings: AppSettingsRepository | None = None,
    ) -> None:
        self.database = database
        self.rules = rules if rules is not None else NumberingRuleRepository(database)
        self.records = (
            records if records is not None else CertificateRecordRepository(database)
        )
        self.logs = logs if logs is not None else OperationLogRepository(database)
        self.settings = (
            settings if settings is not None else AppSettingsRepository(database)
        )

    # ------------------------------------------------------------------ 只读

    @staticmethod
    def available_types() -> tuple[CertificateType, ...]:
        """返回三类证书类型，顺序固定。"""
        return CERTIFICATE_TYPES

    def get_last_type(self) -> str:
        """读取上次使用的证书类型；缺失、非法或读取失败时回退毕结业证书。"""
        try:
            row = self.settings.get(LAST_RULE_KEY)
        except sqlite3.Error:
            logger.exception("读取上次证书类型失败，回退默认类型")
            return DEFAULT_RULE_KEY
        if row is None:
            return DEFAULT_RULE_KEY
        value = row.get("value")
        if isinstance(value, str) and value in CERTIFICATE_TYPE_MAP:
            return value
        return DEFAULT_RULE_KEY

    def remember_last_type(self, rule_key: str) -> None:
        """记录上次使用的证书类型；写入失败只记录日志，不影响主流程。"""
        type_info = _require_type(rule_key)
        try:
            self.settings.set_value(LAST_RULE_KEY, type_info.key)
        except sqlite3.Error:
            logger.exception("保存上次证书类型失败")

    def get_rule(self, rule_key: str) -> NumberingRuleView:
        """读取指定类型的规则；不存在或非文本键时报业务错误。"""
        type_info = _require_type(rule_key)
        try:
            with self.database.connect() as connection:
                return self._load_rule(type_info, connection)
        except sqlite3.Error as exc:
            logger.exception("读取编号规则失败")
            raise DataAccessError("读取编号规则失败，请稍后重试") from exc

    def preview(self, rule_key: str, start_sequence: int) -> NumberPreview:
        """按规则与手动起始流水号计算预览编号，不修改数据库。"""
        type_info = _require_type(rule_key)
        _validate_start_sequence(start_sequence)
        try:
            with self.database.connect() as connection:
                view = self._load_rule(type_info, connection)
        except sqlite3.Error as exc:
            logger.exception("预览证书编号失败")
            raise DataAccessError("预览证书编号失败，请稍后重试") from exc

        _ensure_width(view, type_info.label)
        number = compose_certificate_number(
            view.fixed_texts, start_sequence, view.sequence_width
        )
        return NumberPreview(
            rule_key=type_info.key,
            label=type_info.label,
            number=number,
            sequence_value=start_sequence,
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
            error = DataAccessError("更新编号规则失败，请稍后重试")
            logger.exception("更新编号规则失败")
            self._audit_failure(
                OP_RULE_UPDATE,
                type_info.key,
                f"更新编号规则失败：{type_info.label}",
                str(error),
            )
            raise error from exc

    # ------------------------------------------------------------------ 批量生成

    def plan_numbers(
        self, record_ids: Sequence[int], rule_key: str, start_sequence: int
    ) -> NumberingPlan:
        """计算批量编号计划，供确认弹窗展示。

        只读：不写数据库、不占用编号。计划中的待编号条数、起始流水号与
        首末编号均由手动起始值推算。
        """
        type_info = _require_type(rule_key)
        _validate_start_sequence(start_sequence)
        ids = _normalize_record_ids(record_ids)
        try:
            with self.database.connect() as connection:
                view = self._load_rule(type_info, connection)
                _ensure_width(view, type_info.label)
                _ensure_levels(view)
                records = self._ordered_records(ids, connection)
        except sqlite3.Error as exc:
            logger.exception("计算批量编号计划失败")
            raise DataAccessError("读取编号数据失败，请稍后重试") from exc

        matched = _match_education_levels(records, view.education_levels)
        skipped = sum(1 for record in matched if record.get(type_info.number_field))
        pending = len(matched) - skipped
        if pending:
            first_number = compose_certificate_number(
                view.fixed_texts, start_sequence, view.sequence_width
            )
            last_number = compose_certificate_number(
                view.fixed_texts,
                start_sequence + pending - 1,
                view.sequence_width,
            )
        else:
            first_number = None
            last_number = None

        return NumberingPlan(
            rule_key=type_info.key,
            label=type_info.label,
            education_levels=view.education_levels,
            total_count=len(records),
            matched_count=len(matched),
            level_skipped_count=len(records) - len(matched),
            pending_count=pending,
            skipped_count=skipped,
            start_sequence=start_sequence,
            first_number=first_number,
            last_number=last_number,
        )

    def generate_numbers(
        self, record_ids: Sequence[int], rule_key: str, start_sequence: int
    ) -> BulkNumberingResult:
        """按给定顺序、以手动起始流水号批量生成编号。

        已有该类编号的记录一律跳过且保持原值，序号不因跳过产生空档。
        整批在一个事务内完成：冲突、记录被删除或数据库错误都会整体回滚，
        不产生部分编号；成功时只写一条汇总审计。
        """
        type_info = _require_type(rule_key)
        _validate_start_sequence(start_sequence)
        ids = _normalize_record_ids(record_ids)

        try:
            with self.database.transaction() as connection:
                view = self._load_rule(type_info, connection)
                _ensure_width(view, type_info.label)
                _ensure_levels(view)
                records = self._ordered_records(ids, connection)

                matched = _match_education_levels(records, view.education_levels)
                level_skipped = len(records) - len(matched)
                sequence = start_sequence
                numbered = 0
                skipped = 0
                first_number: str | None = None
                last_number: str | None = None

                for record in matched:
                    if record.get(type_info.number_field):
                        skipped += 1
                        continue

                    number = compose_certificate_number(
                        view.fixed_texts, sequence, view.sequence_width
                    )
                    if self.records.exists_by_number(
                        type_info.number_field, number, connection=connection
                    ):
                        raise NumberConflictError(
                            f"{type_info.label}编号 {number} 已存在，"
                            "本次批量编号已回滚，未写入任何编号"
                        )
                    if not self.records.update(
                        record["id"],
                        {type_info.number_field: number},
                        connection=connection,
                    ):
                        raise RecordNotFoundError(
                            f"记录 {record['id']} 不存在或已被删除，本次批量编号已回滚"
                        )
                    if first_number is None:
                        first_number = number
                    last_number = number
                    sequence += 1
                    numbered += 1

                if numbered:
                    self._write_success_log(
                        connection,
                        OP_GENERATE,
                        TARGET_RECORD,
                        None,
                        f"批量生成{type_info.label}编号"
                        f"（层次：{'、'.join(view.education_levels)}，"
                        f"起始流水号：{start_sequence}）："
                        f"编号 {numbered} 条，跳过 {skipped} 条，"
                        f"层次不符 {level_skipped} 条，"
                        f"编号区间 {first_number}–{last_number}",
                    )

                return BulkNumberingResult(
                    rule_key=type_info.key,
                    label=type_info.label,
                    education_levels=view.education_levels,
                    numbered_count=numbered,
                    skipped_count=skipped,
                    level_skipped_count=level_skipped,
                    first_number=first_number,
                    last_number=last_number,
                )
        except AuditWriteError as exc:
            logger.exception("批量生成证书编号成功但审计写入失败，事务已回滚：%s", exc)
            raise
        except ServiceError as exc:
            self._audit_failure(
                OP_GENERATE,
                type_info.key,
                f"批量生成{type_info.label}编号失败",
                str(exc),
            )
            raise
        except sqlite3.IntegrityError as exc:
            error = NumberConflictError(
                f"{type_info.label}编号与已有编号冲突，本次批量编号已回滚，未写入任何编号"
            )
            self._audit_failure(
                OP_GENERATE,
                type_info.key,
                f"批量生成{type_info.label}编号失败",
                str(error),
            )
            raise error from exc
        except sqlite3.Error as exc:
            error = DataAccessError("批量生成证书编号失败，编号未保存")
            logger.exception("批量生成证书编号失败")
            self._audit_failure(
                OP_GENERATE,
                type_info.key,
                f"批量生成{type_info.label}编号失败",
                str(error),
            )
            raise error from exc

    # ------------------------------------------------------------------ 层次与清空

    def list_education_levels(self, rule_key: str) -> tuple[str, ...]:
        """返回编号应用条件的层次候选值。

        数据中已出现的层次按值排序；规则中已保存但数据中已不存在的层次
        追加在后，避免旧配置丢失。
        """
        type_info = _require_type(rule_key)
        try:
            with self.database.connect() as connection:
                data_levels = self.records.list_education_levels(connection=connection)
                row = self.rules.get(type_info.key, connection=connection)
        except sqlite3.Error as exc:
            logger.exception("读取编号应用条件候选值失败")
            raise DataAccessError("读取层次候选值失败，请稍后重试") from exc

        candidates = list(data_levels)
        saved = _parse_education_levels(
            row.get("education_levels") if row is not None else None,
            type_info.label,
        )
        for level in saved:
            if level not in candidates:
                candidates.append(level)
        return tuple(candidates)

    def count_numbers(self, rule_key: str) -> int:
        """统计该类型已有编号的记录数（不受筛选与层次条件限制）。"""
        type_info = _require_type(rule_key)
        try:
            return self.records.count_by_number_field(type_info.number_field)
        except sqlite3.Error as exc:
            logger.exception("统计证书编号失败")
            raise DataAccessError("读取证书编号失败，请稍后重试") from exc

    def clear_numbers(self, rule_key: str) -> NumberClearingResult:
        """清空该类型全部记录的编号。

        不受学生列表筛选与层次条件限制；只改该编号字段，不读写任何流水值，
        同一事务内写一条汇总审计。
        """
        type_info = _require_type(rule_key)

        try:
            existing = self.records.count_by_number_field(type_info.number_field)
        except sqlite3.Error as exc:
            logger.exception("统计待清空编号失败")
            raise DataAccessError("读取证书编号失败，请稍后重试") from exc
        if existing == 0:
            raise ValidationError("没有可清空的编号")

        try:
            with self.database.transaction() as connection:
                cleared = self.records.clear_number_field(
                    type_info.number_field, connection=connection
                )
                self._write_success_log(
                    connection,
                    OP_CLEAR,
                    TARGET_RECORD,
                    type_info.key,
                    f"清空{type_info.label}编号：清空 {cleared} 条",
                )
                return NumberClearingResult(
                    rule_key=type_info.key,
                    label=type_info.label,
                    cleared_count=cleared,
                )
        except AuditWriteError as exc:
            logger.exception("清空证书编号成功但审计写入失败，事务已回滚：%s", exc)
            raise
        except ServiceError as exc:
            self._audit_failure(
                OP_CLEAR,
                type_info.key,
                f"清空{type_info.label}编号失败",
                str(exc),
            )
            raise
        except sqlite3.Error as exc:
            error = DataAccessError("清空证书编号失败，编号未清空")
            logger.exception("清空证书编号失败")
            self._audit_failure(
                OP_CLEAR,
                type_info.key,
                f"清空{type_info.label}编号失败",
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
        return NumberingRuleView(
            rule_key=type_info.key,
            fixed_texts=tuple(row[field] or "" for field in FIXED_TEXT_FIELDS),
            sequence_width=int(row["sequence_width"]),
            education_levels=_parse_education_levels(
                row.get("education_levels"), type_info.label
            ),
        )

    def _ordered_records(
        self, record_ids: Sequence[int], connection: sqlite3.Connection
    ) -> list[dict[str, Any]]:
        """按传入的 ID 顺序返回记录快照；有任何记录缺失时报业务错误。"""
        rows = {
            row["id"]: row
            for row in self.records.list_by_ids(list(record_ids), connection=connection)
        }
        missing = [record_id for record_id in record_ids if record_id not in rows]
        if missing:
            raise RecordNotFoundError(
                f"记录 {missing[0]} 不存在或已被删除，本次批量编号已回滚"
            )
        return [rows[record_id] for record_id in record_ids]

    def _write_success_log(
        self,
        connection: sqlite3.Connection,
        operation_type: str,
        target_type: str,
        target_id: str | None,
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


def _normalize_record_ids(record_ids: Sequence[int]) -> list[int]:
    """校验批量编号的记录 ID：必须为正整数，去重并保持调用方顺序。"""
    if isinstance(record_ids, (str, bytes)) or not isinstance(record_ids, Sequence):
        raise ValidationError("记录 ID 列表格式不正确")

    normalized: list[int] = []
    seen: set[int] = set()
    for record_id in record_ids:
        if (
            not isinstance(record_id, int)
            or isinstance(record_id, bool)
            or record_id < 1
        ):
            raise ValidationError("记录 ID 必须是正整数")
        if record_id in seen:
            continue
        seen.add(record_id)
        normalized.append(record_id)

    if not normalized:
        raise ValidationError("当前筛选结果为空，无可编号记录")
    return normalized


def _ensure_width(view: NumberingRuleView, label: str) -> None:
    """数据库中的异常位数要转换为可读业务错误，避免生成超长编号。"""
    if not MIN_SEQUENCE_WIDTH <= view.sequence_width <= MAX_SEQUENCE_WIDTH:
        raise ValidationError(
            f"{label}的序号位数配置为 {view.sequence_width}，"
            f"不在 {MIN_SEQUENCE_WIDTH}–{MAX_SEQUENCE_WIDTH} 范围内，"
            "请先到证书编码中修正"
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
        if name in FIXED_TEXT_FIELDS:
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
        elif name == "education_levels":
            normalized[name] = _normalize_education_levels(raw)
    return normalized


def _normalize_education_levels(raw: Any) -> str:
    """校验层次选择并序列化为 JSON 数组字符串（保留中文原文）。"""
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise ValidationError("编号应用条件必须是字符串列表")

    levels: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValidationError("编号应用条件必须是字符串列表")
        text = item.strip()
        if text and text not in levels:
            levels.append(text)
    return json.dumps(levels, ensure_ascii=False, separators=(",", ":"))


def _parse_education_levels(raw: Any, label: str) -> tuple[str, ...]:
    """解析数据库中的层次 JSON；空串视为未选择。"""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return ()
    if not isinstance(raw, str):
        raise ValidationError(f"{label}的编号应用条件数据不合法")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"{label}的编号应用条件数据损坏，请修正后重试"
        ) from exc
    if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
        raise ValidationError(f"{label}的编号应用条件数据不合法")
    return tuple(item for item in parsed if item)


def _validate_start_sequence(start_sequence: Any) -> None:
    """校验手动起始流水号：必须是大于等于 1 的整数。"""
    if (
        not isinstance(start_sequence, int)
        or isinstance(start_sequence, bool)
        or start_sequence < 1
    ):
        raise ValidationError("请输入起始流水号（大于等于 1 的整数）")


def _ensure_levels(view: NumberingRuleView) -> None:
    if not view.education_levels:
        raise ValidationError("请先选择编号应用的层次")


def _match_education_levels(
    records: Sequence[Mapping[str, Any]], levels: Sequence[str]
) -> list[dict[str, Any]]:
    """按规则中保存的层次过滤记录；空层次不匹配任何记录。"""
    level_set = set(levels)
    return [dict(record) for record in records if record.get("education_level") in level_set]
