"""系统设置 Service。

职责边界：

- 只管理业务配置（``app_settings``）；窗口几何等界面偏好仍由 QSettings 保存；
- 每个设置键都有唯一定义与默认值，读取时逐项校验，损坏或非法值回退默认值并给出告警，
  不静默覆盖数据库中的原值；
- 修改设置与成功审计处于同一事务，审计失败则设置回滚；
- 校验失败写入独立 best-effort 失败审计，不覆盖原始错误。
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.data.db import EXCEL_FIELD_NAMES, Database
from app.repositories import AppSettingsRepository, OperationLogRepository
from app.services.errors import (
    AuditWriteError,
    DataAccessError,
    SettingsValidationError,
)
from app.utils.excel_template import EXCEL_COLUMN_NAMES

logger = logging.getLogger(__name__)

SETTINGS_OPERATION_TYPE = "settings_update"
SETTINGS_RESET_OPERATION_TYPE = "settings_reset"
TARGET_TYPE = "app_setting"

PAGE_SIZE_KEY = "student.page_size"
DEFAULT_CERTIFICATE_TYPE_KEY = "default.certificate_type"
TABLE_COLUMNS_KEY = "student.table_columns"

# 既有 10 列：键与中文标签保持原样，标签不随 C.2 Excel 列名变化。
_BASE_TABLE_FIELDS: tuple[tuple[str, str], ...] = (
    ("student_no", "学号"),
    ("name", "姓名"),
    ("gender", "性别"),
    ("faculty_name", "学院"),
    ("major_name", "专业"),
    ("class_name", "班级"),
    ("certificate_no", "毕业证书编号"),
    ("degree_certificate_no", "学位证书编号"),
    ("other_certificate_no", "其他证书编号"),
    ("updated_at", "更新时间"),
)

# 默认显示列（未配置或配置非法时的回退值），保持历史默认顺序。
DEFAULT_TABLE_COLUMN_KEYS: tuple[str, ...] = tuple(
    key for key, _label in _BASE_TABLE_FIELDS
)

_BASE_FIELD_KEYS: frozenset[str] = frozenset(DEFAULT_TABLE_COLUMN_KEYS)

# 可配置字段池 = 既有 10 列 + 其余 28 个 Excel 导入字段（按 Excel 模板列顺序），共 38 项。
# 表格第一列“选择”勾选列固定存在，不在此列表中。
STUDENT_TABLE_FIELDS: tuple[tuple[str, str], ...] = (
    *_BASE_TABLE_FIELDS,
    *(
        (field, label)
        for field, label in zip(EXCEL_FIELD_NAMES, EXCEL_COLUMN_NAMES)
        if field not in _BASE_FIELD_KEYS
    ),
)

TABLE_COLUMN_KEYS: tuple[str, ...] = tuple(field for field, _label in STUDENT_TABLE_FIELDS)
STUDENT_TABLE_LABELS: dict[str, str] = dict(STUDENT_TABLE_FIELDS)

# 设置项在审计摘要中的展示长度上限，超长时截断并标注剩余项数。
TABLE_COLUMNS_DISPLAY_LIMIT = 120


@dataclass(frozen=True, slots=True)
class SettingDefinition:
    """一个业务设置项的元数据。"""

    key: str
    label: str
    default: Any
    choices: tuple[Any, ...]
    value_type: str
    requires_restart: bool
    description: str


SETTING_DEFINITIONS: tuple[SettingDefinition, ...] = (
    SettingDefinition(
        key=PAGE_SIZE_KEY,
        label="学生列表每页条数",
        default=20,
        choices=(20, 50, 100),
        value_type="int",
        requires_restart=True,
        description="修改后需重启程序生效。",
    ),
    SettingDefinition(
        key=DEFAULT_CERTIFICATE_TYPE_KEY,
        label="默认证书类型",
        default="graduation",
        choices=("graduation", "degree", "other"),
        value_type="str",
        requires_restart=False,
        description="打印、核验与编号设置窗口打开时的默认证书类型。",
    ),
    SettingDefinition(
        key=TABLE_COLUMNS_KEY,
        label="主页表格展示字段",
        default=DEFAULT_TABLE_COLUMN_KEYS,
        choices=TABLE_COLUMN_KEYS,
        value_type="table_columns",
        requires_restart=False,
        description="勾选主页学生表格显示的字段并调整顺序，保存后立即生效。",
    ),
)

DEFINITION_MAP: dict[str, SettingDefinition] = {
    definition.key: definition for definition in SETTING_DEFINITIONS
}

CERTIFICATE_TYPE_LABELS: dict[str, str] = {
    "graduation": "毕结业证书",
    "degree": "学位证书",
    "other": "其他证书",
}


@dataclass(frozen=True, slots=True)
class SettingsSnapshot:
    """一次读取的系统设置快照。"""

    values: Mapping[str, Any]
    warnings: tuple[str, ...]

    def get(self, key: str) -> Any:
        return self.values[key]


class SettingsService:
    """系统设置的读取、修改与恢复默认值入口。"""

    def __init__(
        self,
        database: Database,
        *,
        settings: AppSettingsRepository | None = None,
        logs: OperationLogRepository | None = None,
    ) -> None:
        self.database = database
        self.settings = settings if settings is not None else AppSettingsRepository(database)
        self.logs = logs if logs is not None else OperationLogRepository(database)

    # ------------------------------------------------------------------ 读取

    def load(self) -> SettingsSnapshot:
        """读取全部设置；缺失取默认值，非法值回退并告警。"""
        try:
            rows = self.settings.list_all()
        except sqlite3.Error as exc:
            logger.exception("读取系统设置失败")
            raise DataAccessError("读取系统设置失败，请稍后重试") from exc

        stored = {row["key"]: row["value"] for row in rows}
        values: dict[str, Any] = {}
        warnings: list[str] = []
        for definition in SETTING_DEFINITIONS:
            if definition.key not in stored:
                values[definition.key] = definition.default
                continue
            raw = stored[definition.key]
            parsed = _parse_value(definition, raw)
            if parsed is None:
                values[definition.key] = definition.default
                warnings.append(
                    f"系统设置「{definition.label}」的值 {raw!r} 无效，"
                    f"已使用默认值 {_display(definition, definition.default)}；"
                    "原值未被修改"
                )
            else:
                values[definition.key] = parsed
        return SettingsSnapshot(values=values, warnings=tuple(warnings))

    def get(self, key: str) -> Any:
        """读取单个设置值；未知键报业务错误。"""
        if key not in DEFINITION_MAP:
            raise SettingsValidationError(f"不支持的系统设置项：{key}")
        return self.load().values[key]

    # ------------------------------------------------------------------ 修改

    def update(self, changes: Mapping[str, Any]) -> None:
        """校验并保存设置；成功审计与写入同一事务。"""
        try:
            normalized = _normalize_changes(changes)
        except SettingsValidationError as exc:
            self._audit_failure("修改系统设置失败", str(exc))
            raise

        try:
            with self.database.transaction() as connection:
                for key, value in normalized.items():
                    definition = DEFINITION_MAP[key]
                    old_display = self._current_display(definition, connection)
                    new_display = _display(definition, value)
                    self.settings.set_value(key, _to_stored(value), connection=connection)
                    self._write_success_log(
                        connection,
                        f"修改系统设置：{definition.label}：{old_display} → {new_display}",
                        key,
                    )
        except AuditWriteError:
            logger.exception("系统设置成功审计写入失败，设置修改已回滚")
            raise
        except sqlite3.Error as exc:
            logger.exception("保存系统设置失败")
            message = "保存系统设置失败，修改未生效"
            self._audit_failure("修改系统设置失败", message)
            raise DataAccessError(message) from exc

    def reset_to_defaults(self) -> None:
        """清除自定义设置值，恢复全部默认配置；不触碰业务数据。"""
        try:
            with self.database.transaction() as connection:
                rows = self.settings.list_all(connection=connection)
                customized = [
                    row for row in rows if row["key"] in DEFINITION_MAP
                ]
                for row in customized:
                    self.settings.delete(row["key"], connection=connection)
                if customized:
                    self._write_success_log(
                        connection,
                        f"恢复默认系统设置：清除 {len(customized)} 项自定义配置",
                        None,
                        SETTINGS_RESET_OPERATION_TYPE,
                    )
        except AuditWriteError:
            logger.exception("恢复默认设置审计写入失败，操作已回滚")
            raise
        except sqlite3.Error as exc:
            logger.exception("恢复默认系统设置失败")
            message = "恢复默认系统设置失败，配置未改变"
            self._audit_failure("恢复默认系统设置失败", message)
            raise DataAccessError(message) from exc

    # ------------------------------------------------------------------ 内部

    def _current_display(
        self, definition: SettingDefinition, connection: sqlite3.Connection
    ) -> str:
        row = self.settings.get(definition.key, connection=connection)
        if row is None:
            return _display(definition, definition.default)
        parsed = _parse_value(definition, row["value"])
        if parsed is None:
            return f"{row['value']!r}（无效值）"
        return _display(definition, parsed)

    def _write_success_log(
        self,
        connection: sqlite3.Connection,
        summary: str,
        target_id: str | None,
        operation_type: str = SETTINGS_OPERATION_TYPE,
    ) -> None:
        try:
            self.logs.create(
                operation_type,
                TARGET_TYPE,
                target_id,
                summary,
                "success",
                connection=connection,
            )
        except sqlite3.Error as exc:
            raise AuditWriteError("审计日志写入失败，设置修改已回滚") from exc

    def _audit_failure(self, summary: str, message: str) -> None:
        """失败审计：独立 best-effort，绝不覆盖原始业务错误。"""
        try:
            self.logs.create(
                SETTINGS_OPERATION_TYPE,
                TARGET_TYPE,
                None,
                summary,
                "failure",
                error_message=message,
            )
        except Exception:  # noqa: BLE001 - 审计失败不得覆盖原始异常
            logger.exception("失败审计日志写入失败；原始错误：%s", message)


def _normalize_changes(changes: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(changes, Mapping):
        raise SettingsValidationError("设置变更必须是字段映射")
    unknown = [key for key in changes if key not in DEFINITION_MAP]
    if unknown:
        raise SettingsValidationError(
            f"包含不支持的系统设置项：{'、'.join(str(key) for key in unknown)}"
        )
    if not changes:
        raise SettingsValidationError("没有需要保存的设置项")

    normalized: dict[str, Any] = {}
    for key, raw in changes.items():
        definition = DEFINITION_MAP[key]
        value = _coerce(definition, raw)
        if value is None:
            raise SettingsValidationError(
                f"「{definition.label}」的取值不合法，"
                f"可选值为 {'、'.join(_display(definition, choice) for choice in definition.choices)}"
            )
        normalized[key] = value
    return normalized


def _coerce(definition: SettingDefinition, raw: Any) -> Any | None:
    if definition.value_type == "table_columns":
        return _coerce_table_columns(definition, raw)
    if definition.value_type == "int":
        if not isinstance(raw, int) or isinstance(raw, bool):
            return None
    elif definition.value_type == "str":
        if not isinstance(raw, str):
            return None
    else:  # pragma: no cover - 定义错误
        return None
    return raw if raw in definition.choices else None


def _coerce_table_columns(
    definition: SettingDefinition, raw: Any
) -> tuple[str, ...] | None:
    """校验表格字段序列：非空、无重复、全部为合法字段键。"""
    if isinstance(raw, (str, bytes)) or isinstance(raw, bool):
        return None
    if not isinstance(raw, (tuple, list)):
        return None
    if not raw:
        return None
    allowed = set(definition.choices)
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or item not in allowed or item in seen:
            return None
        seen.add(item)
    return tuple(raw)


def _parse_value(definition: SettingDefinition, raw: Any) -> Any | None:
    if definition.value_type == "int":
        if not isinstance(raw, str):
            return None
        text = raw.strip()
        if not text.isdigit():
            return None
        value = int(text)
        return value if value in definition.choices else None
    if definition.value_type == "str":
        return raw if raw in definition.choices else None
    if definition.value_type == "table_columns":
        if not isinstance(raw, str):
            return None
        keys = [part.strip() for part in raw.split(",")]
        if not keys or any(not key for key in keys):
            return None
        allowed = set(definition.choices)
        if len(set(keys)) != len(keys):
            return None
        if any(key not in allowed for key in keys):
            return None
        return tuple(keys)
    return None  # pragma: no cover - 定义错误


def _to_stored(value: Any) -> str:
    if isinstance(value, tuple):
        return ",".join(value)
    return str(value)


def _display(definition: SettingDefinition, value: Any) -> str:
    if definition.value_type == "str" and definition.key == DEFAULT_CERTIFICATE_TYPE_KEY:
        return CERTIFICATE_TYPE_LABELS.get(value, str(value))
    if definition.value_type == "table_columns":
        keys = (value,) if isinstance(value, str) else tuple(value)
        labels = [STUDENT_TABLE_LABELS.get(key, str(key)) for key in keys]
        text = "、".join(labels)
        if len(text) <= TABLE_COLUMNS_DISPLAY_LIMIT:
            return text
        shown: list[str] = []
        length = 0
        for label in labels:
            extra = len(label) + (1 if shown else 0)
            if length + extra > TABLE_COLUMNS_DISPLAY_LIMIT:
                break
            shown.append(label)
            length += extra
        return f"{'、'.join(shown)}…等 {len(labels) - len(shown)} 项"
    return str(value)
