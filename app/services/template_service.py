"""证书模板 Service。

职责边界：

- 保存/更新/删除模板时，数据库列（名称、类型、宽高）一律由经过校验的模板模型派生，
  不接受调用方单独传入的宽高；
- 从数据库读取时校验 ``template_json`` 与各列一致，不一致返回 ``TemplateDataError``；
- 内置模板来自只读资源，标记 ``is_builtin=1`` 后禁止修改与删除；
- 内置模板播种必须由调用方提供显式事务连接，重复播种只跳过、不覆盖；
- 渲染只做编排：解析照片安全路径后交给 ``CertificateRenderer``，不自行绘制。
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config.paths import AppPaths
from app.data.db import Database
from app.repositories import CertificateTemplateRepository, OperationLogRepository
from app.services.errors import (
    AuditWriteError,
    DataAccessError,
    PhotoPathError,
    TemplateNotFoundError,
    TemplateReadOnlyError,
    TemplateResourceError,
    TemplateValidationError,
)
from app.services.template_model import CertificateTemplate, template_from_json
from app.services.template_renderer import (
    DEFAULT_DPI,
    CertificateRenderer,
    RenderResult,
)
from app.utils.photo_paths import photo_file_path

BUILTIN_TEMPLATE_DIR_NAME = "templates"

TARGET_TEMPLATE = "certificate_template"
OP_TEMPLATE_CREATE = "template_create"
OP_TEMPLATE_UPDATE = "template_update"
OP_TEMPLATE_DELETE = "template_delete"

CERTIFICATE_TYPE_LABELS: dict[str, str] = {
    "graduation": "毕结业证书",
    "degree": "学位证书",
    "other": "其他证书",
}

logger = logging.getLogger(__name__)

# 固定映射：证书类型 → 内置模板资源文件名，不扫描目录中的任意 JSON。
BUILTIN_TEMPLATE_FILES: dict[str, str] = {
    "graduation": "builtin_graduation.json",
    "degree": "builtin_degree.json",
    "other": "builtin_other.json",
}


@dataclass(frozen=True, slots=True)
class TemplateSummary:
    """列表展示所需的模板摘要。"""

    id: int
    name: str
    certificate_type: str
    canvas_width_mm: float
    canvas_height_mm: float
    is_builtin: bool


@dataclass(frozen=True, slots=True)
class SeedResult:
    """一次内置模板播种的统计。"""

    created: int
    skipped: int


class TemplateService:
    """模板持久化、内置资源与渲染编排的业务入口。"""

    def __init__(
        self,
        database: Database,
        paths: AppPaths | None = None,
        *,
        templates: CertificateTemplateRepository | None = None,
        renderer: CertificateRenderer | None = None,
        logs: OperationLogRepository | None = None,
    ) -> None:
        self.database = database
        self.paths = paths
        self.templates = (
            templates if templates is not None else CertificateTemplateRepository(database)
        )
        self.renderer = renderer if renderer is not None else CertificateRenderer()
        self.logs = logs if logs is not None else OperationLogRepository(database)

    # ------------------------------------------------------------------ 读写

    def create_template(self, template: CertificateTemplate) -> int:
        """新增自定义模板，返回主键；数据库列由模型派生，成功审计同事务。"""
        if not isinstance(template, CertificateTemplate):
            raise TemplateValidationError("模板对象类型不正确")
        try:
            with self.database.transaction() as connection:
                template_id = self.templates.create(
                    is_builtin=0,
                    connection=connection,
                    **template.to_record_values(),
                )
                self._write_success_log(
                    connection,
                    OP_TEMPLATE_CREATE,
                    template_id,
                    f"新增证书模板：{_describe(template)}",
                )
                return template_id
        except AuditWriteError:
            logger.exception("模板新增成功但审计写入失败，已回滚")
            raise
        except sqlite3.Error as exc:
            logger.exception("保存证书模板失败")
            raise DataAccessError("保存证书模板失败，未写入数据库") from exc

    def update_template(self, template_id: int, template: CertificateTemplate) -> None:
        """更新自定义模板；内置模板只读，成功审计同事务。"""
        if not isinstance(template, CertificateTemplate):
            raise TemplateValidationError("模板对象类型不正确")
        try:
            with self.database.transaction() as connection:
                row = self.templates.get(template_id, connection=connection)
                if row is None:
                    raise TemplateNotFoundError(f"模板 {template_id} 不存在或已被删除")
                self._ensure_editable(row)
                self.templates.update(
                    template_id, template.to_record_values(), connection=connection
                )
                self._write_success_log(
                    connection,
                    OP_TEMPLATE_UPDATE,
                    template_id,
                    f"修改证书模板：{_describe(template)}",
                )
        except AuditWriteError:
            logger.exception("模板修改成功但审计写入失败，已回滚")
            raise
        except sqlite3.Error as exc:
            logger.exception("更新证书模板失败")
            raise DataAccessError("更新证书模板失败，模板未改变") from exc

    def delete_template(self, template_id: int) -> None:
        """删除自定义模板；内置模板只读，成功审计同事务。"""
        try:
            with self.database.transaction() as connection:
                row = self.templates.get(template_id, connection=connection)
                if row is None:
                    raise TemplateNotFoundError(f"模板 {template_id} 不存在或已被删除")
                self._ensure_editable(row)
                if not self.templates.delete(template_id, connection=connection):
                    raise TemplateNotFoundError(f"模板 {template_id} 不存在或已被删除")
                label = CERTIFICATE_TYPE_LABELS.get(
                    row["certificate_type"], row["certificate_type"]
                )
                self._write_success_log(
                    connection,
                    OP_TEMPLATE_DELETE,
                    template_id,
                    f"删除证书模板：{row['name']}（{label}，"
                    f"{_format_mm(row['canvas_width_mm'])}×"
                    f"{_format_mm(row['canvas_height_mm'])}mm）",
                )
        except AuditWriteError:
            logger.exception("模板删除成功但审计写入失败，已回滚")
            raise
        except sqlite3.Error as exc:
            logger.exception("删除证书模板失败")
            raise DataAccessError("删除证书模板失败，模板未改变") from exc

    def load_template(self, template_id: int) -> CertificateTemplate:
        """按 ID 加载模板并校验列与 JSON 一致。"""
        row = self._require_row(template_id)
        return CertificateTemplate.from_record(row)

    def list_templates(self, certificate_type: str | None = None) -> list[TemplateSummary]:
        """列出模板摘要，按主键升序。"""
        rows = (
            self.templates.list_all()
            if certificate_type is None
            else self.templates.list_by_type(certificate_type)
        )
        return [
            TemplateSummary(
                id=row["id"],
                name=row["name"],
                certificate_type=row["certificate_type"],
                canvas_width_mm=row["canvas_width_mm"],
                canvas_height_mm=row["canvas_height_mm"],
                is_builtin=bool(row["is_builtin"]),
            )
            for row in rows
        ]

    # ------------------------------------------------------------------ 内置

    def load_builtin_templates(self) -> tuple[CertificateTemplate, ...]:
        """加载三份内置模板资源。

        Raises:
            TemplateResourceError: 资源目录/文件缺失、内容无效或类型错配。
        """
        if self.paths is None:
            raise TemplateResourceError("未配置资源目录，无法加载内置模板")
        directory = self.paths.resource_dir / BUILTIN_TEMPLATE_DIR_NAME

        templates: list[CertificateTemplate] = []
        for certificate_type, file_name in BUILTIN_TEMPLATE_FILES.items():
            path = directory / file_name
            if not path.is_file():
                raise TemplateResourceError(f"缺少内置模板资源：{file_name}")
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise TemplateResourceError(
                    f"无法读取内置模板资源：{file_name}"
                ) from exc
            try:
                template = template_from_json(text)
            except TemplateValidationError as exc:
                raise TemplateResourceError(
                    f"内置模板 {file_name} 内容无效：{exc}"
                ) from exc
            if template.certificate_type != certificate_type:
                raise TemplateResourceError(
                    f"内置模板 {file_name} 的证书类型为 {template.certificate_type}，"
                    f"与约定类型 {certificate_type} 不一致"
                )
            templates.append(template)
        return tuple(templates)

    def seed_builtin_templates(self, connection: sqlite3.Connection) -> SeedResult:
        """在调用方事务内补齐缺失的内置模板；已存在则跳过、不覆盖。

        Raises:
            ValueError: 传入连接未处于显式事务中。
            TemplateResourceError: 内置模板资源不可用。
        """
        templates = self.load_builtin_templates()
        created = 0
        skipped = 0
        for template in templates:
            existing = self.templates.get_builtin(
                template.certificate_type, connection=connection
            )
            if existing is not None:
                skipped += 1
                continue
            self.templates.create(
                is_builtin=1,
                connection=connection,
                **template.to_record_values(),
            )
            created += 1
        return SeedResult(created=created, skipped=skipped)

    # ------------------------------------------------------------------ 渲染

    def render_record(
        self,
        template_id: int,
        record: Mapping[str, Any],
        *,
        dpi: int = DEFAULT_DPI,
    ) -> RenderResult:
        """按模板渲染一条业务记录；照片路径经过安全校验。

        Raises:
            TemplateNotFoundError: 模板不存在。
            PhotoPathError: 记录中的照片路径不安全或超出照片目录。
            TemplateResourceError: 需要照片但未配置程序路径。
        """
        template = self.load_template(template_id)
        photo_file: Path | None = None

        photo_path = record.get("photo_path")
        if photo_path:
            if self.paths is None:
                raise TemplateResourceError("未配置程序路径，无法解析照片文件")
            try:
                photo_file = photo_file_path(self.paths.photos_dir, photo_path)
            except ValueError as exc:
                raise PhotoPathError(str(exc)) from exc

        return self.renderer.render_to_image(
            template, record, photo_file=photo_file, dpi=dpi
        )

    # ------------------------------------------------------------------ 辅助

    def _write_success_log(
        self,
        connection: sqlite3.Connection,
        operation_type: str,
        template_id: int,
        summary: str,
    ) -> None:
        """成功审计：与模板写入同一事务，失败则回滚。"""
        try:
            self.logs.create(
                operation_type,
                TARGET_TEMPLATE,
                str(template_id),
                summary,
                "success",
                connection=connection,
            )
        except sqlite3.Error as exc:
            raise AuditWriteError("审计日志写入失败，模板修改已回滚") from exc

    def _require_row(self, template_id: int) -> dict[str, Any]:
        row = self.templates.get(template_id)
        if row is None:
            raise TemplateNotFoundError(f"模板 {template_id} 不存在或已被删除")
        return row

    @staticmethod
    def _ensure_editable(row: Mapping[str, Any]) -> None:
        if row["is_builtin"]:
            raise TemplateReadOnlyError("内置模板为只读，不能修改或删除")


def _describe(template: CertificateTemplate) -> str:
    label = CERTIFICATE_TYPE_LABELS.get(
        template.certificate_type, template.certificate_type
    )
    return (
        f"{template.name}（{label}，{_format_mm(template.canvas_width_mm)}×"
        f"{_format_mm(template.canvas_height_mm)}mm）"
    )


def _format_mm(value: Any) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:g}"
