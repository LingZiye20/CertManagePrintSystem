"""证书打印服务。

职责边界：

- 预检：记录、证书编号、模板、设备、纸张与布局可绘制区域；
- 逐张建立独立打印任务，调用 :class:`CertificateRenderer` 绘制，不重复实现渲染；
- 打印状态只记录程序可观测到的结果：
  ``failed``（有证据的失败）、``unknown``（无法确认提交结果）、
  ``accepted``（设备明确接受，Qt 不提供）、以及未开始（不写记录）；
- 每张打印尝试独立事务写入 ``print_records``，失败不掩盖打印结果；
- 每次任务写一条 ``operation_logs`` 批次摘要，只列出真正写入成功的打印记录 ID，
  清单截断或审计失败时如实告警，绝不声称批次关联完整。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config.paths import AppPaths
from app.data.db import Database
from app.repositories import (
    CertificateRecordRepository,
    OperationLogRepository,
    PrintRecordRepository,
)
from app.services.errors import (
    PhotoPathError,
    PrintError,
    PrintTemplateError,
    PrinterUnavailableError,
    TemplateResourceError,
)
from app.services.numbering_service import CERTIFICATE_TYPES
from app.services.print_target import (
    STATE_ABORTED,
    STATE_ERROR,
    PageGeometry,
    PrintTarget,
)
from app.services.template_model import CertificateTemplate, PhotoElement
from app.services.template_renderer import CertificateRenderer
from app.services.template_service import TemplateService
from app.utils.mm_units import validate_dpi
from app.utils.photo_paths import photo_file_path

logger = logging.getLogger(__name__)

PRINT_FAILED = "failed"
PRINT_UNKNOWN = "unknown"
PRINT_ACCEPTED = "accepted"

OPERATION_BATCH = "certificate_print_batch"
TARGET_TEMPLATE = "certificate_template"

BATCH_SUMMARY_LIMIT = 1800
SIZE_TOLERANCE_MM = 0.5
HARD_MARGIN_NOTICE = (
    "程序无法获取打印机硬件硬边距；打印按布局可绘制区域 1:1 居中，"
    "若边缘被裁切请调整纸张边距或校准打印机。"
)
ZERO_MARGIN_NOTICE = "页面边距为 0 不代表硬件支持无边距打印，请实际打印确认。"


@dataclass(frozen=True, slots=True)
class PrintTemplateOption:
    """打印对话框中的模板候选项。"""

    id: int | None
    name: str
    certificate_type: str
    canvas_width_mm: float
    canvas_height_mm: float
    is_builtin: bool
    source: str  # "database" | "builtin_resource"


@dataclass(frozen=True, slots=True)
class PrintPreflight:
    """打印前检查结果。"""

    template_name: str
    template_id: int | None
    certificate_type: str
    record_count: int
    paper_mm: tuple[float, float]
    printable_mm: tuple[float, float, float, float] | None
    warnings: tuple[str, ...]
    blocking_errors: tuple[str, ...]

    @property
    def can_print(self) -> bool:
        return not self.blocking_errors

    @property
    def blocking(self) -> tuple[str, ...]:
        """``blocking_errors`` 的简写，便于界面直接展示。"""
        return self.blocking_errors


@dataclass(frozen=True, slots=True)
class PrintAttempt:
    """单条证书的打印尝试结果。"""

    record_id: int
    student_no: str
    certificate_no: str
    rendered: bool
    result: str
    error: str | None
    warnings: tuple[str, ...]
    audit_id: int | None
    audit_saved: bool
    audit_error: str | None
    device_fault: bool = False


@dataclass(frozen=True, slots=True)
class PrintBatchResult:
    """一次打印任务的结果汇总。"""

    batch_id: str
    printer_name: str
    template_name: str
    template_id: int | None
    certificate_type: str
    preflight: PrintPreflight
    attempts: tuple[PrintAttempt, ...]
    not_started: tuple[tuple[int, str], ...]
    cancelled_by_user: bool
    batch_log_id: int | None
    batch_log_saved: bool
    warnings: tuple[str, ...]
    message: str
    started_at: str
    finished_at: str

    @property
    def rendered_count(self) -> int:
        return sum(1 for attempt in self.attempts if attempt.rendered)

    @property
    def failed_count(self) -> int:
        return sum(1 for attempt in self.attempts if attempt.result == PRINT_FAILED)

    @property
    def unknown_count(self) -> int:
        return sum(1 for attempt in self.attempts if attempt.result == PRINT_UNKNOWN)

    @property
    def accepted_count(self) -> int:
        return sum(1 for attempt in self.attempts if attempt.result == PRINT_ACCEPTED)

    @property
    def audit_failed_count(self) -> int:
        return sum(1 for attempt in self.attempts if not attempt.audit_saved)

    @property
    def not_started_count(self) -> int:
        return len(self.not_started)

    @property
    def blocking_errors(self) -> tuple[str, ...]:
        return self.preflight.blocking_errors


def _utc_now() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _format_mm(value: float) -> str:
    return f"{value:g}"


class PrintService:
    """打印预检、渲染与审计编排的业务入口。"""

    def __init__(
        self,
        database: Database,
        templates: TemplateService,
        paths: AppPaths | None = None,
        *,
        renderer: CertificateRenderer | None = None,
        records: CertificateRecordRepository | None = None,
        print_records: PrintRecordRepository | None = None,
        logs: OperationLogRepository | None = None,
    ) -> None:
        self.database = database
        self.templates = templates
        self.paths = paths
        self.renderer = renderer if renderer is not None else CertificateRenderer()
        self.records = records if records is not None else CertificateRecordRepository(database)
        self.print_records = (
            print_records if print_records is not None else PrintRecordRepository(database)
        )
        self.logs = logs if logs is not None else OperationLogRepository(database)

    # ------------------------------------------------------------------ 模板

    def available_templates(self, certificate_type: str) -> list[PrintTemplateOption]:
        """返回指定证书类型的模板；数据库为空时回退内置只读资源。"""
        self._number_field(certificate_type)
        options = [
            PrintTemplateOption(
                id=summary.id,
                name=summary.name,
                certificate_type=summary.certificate_type,
                canvas_width_mm=summary.canvas_width_mm,
                canvas_height_mm=summary.canvas_height_mm,
                is_builtin=summary.is_builtin,
                source="database",
            )
            for summary in self.templates.list_templates(certificate_type)
        ]
        if options:
            return options

        try:
            builtins = self.templates.load_builtin_templates()
        except TemplateResourceError:
            logger.warning("内置模板资源不可用，无法回退打印模板", exc_info=True)
            return []

        return [
            PrintTemplateOption(
                id=None,
                name=template.name,
                certificate_type=template.certificate_type,
                canvas_width_mm=template.canvas_width_mm,
                canvas_height_mm=template.canvas_height_mm,
                is_builtin=True,
                source="builtin_resource",
            )
            for template in builtins
            if template.certificate_type == certificate_type
        ]

    def load_template(
        self, certificate_type: str, template_id: int | None
    ) -> CertificateTemplate:
        """加载打印模板；``template_id`` 为 ``None`` 时使用内置资源。"""
        if template_id is not None:
            try:
                return self.templates.load_template(template_id)
            except Exception as exc:  # noqa: BLE001 - 统一转为打印模板错误
                raise PrintTemplateError(f"无法加载打印模板：{exc}") from exc

        try:
            builtins = self.templates.load_builtin_templates()
        except TemplateResourceError as exc:
            raise PrintTemplateError(str(exc)) from exc
        for template in builtins:
            if template.certificate_type == certificate_type:
                return template
        raise PrintTemplateError(f"没有可用于 {certificate_type} 的内置模板")

    @staticmethod
    def _number_field(certificate_type: str) -> str:
        for type_info in CERTIFICATE_TYPES:
            if type_info.key == certificate_type:
                return type_info.number_field
        raise PrintError(f"不支持的证书类型：{certificate_type}")

    # ------------------------------------------------------------------ 预检

    def preflight(
        self,
        records: Sequence[Mapping[str, Any]],
        template: CertificateTemplate,
        certificate_type: str,
        target: PrintTarget,
    ) -> PrintPreflight:
        """在开始打印前检查记录、编号、模板与设备，不写入任何数据。"""
        number_field = self._number_field(certificate_type)
        blocking: list[str] = []
        warnings: list[str] = []

        if not records:
            blocking.append("没有可打印的记录")

        if template.certificate_type != certificate_type:
            blocking.append(
                f"模板证书类型为 {template.certificate_type}，与打印类型 "
                f"{certificate_type} 不一致"
            )

        missing_numbers = [
            str(record.get("student_no") or "（空学号）")
            for record in records
            if not str(record.get(number_field) or "").strip()
        ]
        if missing_numbers:
            preview = "、".join(missing_numbers[:5])
            more = f" 等共 {len(missing_numbers)} 条" if len(missing_numbers) > 5 else ""
            blocking.append(f"以下记录缺少证书编号：{preview}{more}")

        if not target.is_valid():
            blocking.append("打印设备不可用，请检查打印机连接、电源与驱动")

        dpi = target.resolution()
        try:
            dpi = validate_dpi(dpi)
        except ValueError as exc:
            blocking.append(f"无法读取打印机分辨率：{exc}")

        geometry = target.page_geometry()
        if geometry.printable_available:
            printable = (
                float(geometry.printable_x_mm or 0.0),
                float(geometry.printable_y_mm or 0.0),
                float(geometry.printable_width_mm or 0.0),
                float(geometry.printable_height_mm or 0.0),
            )
        else:
            printable = None
            warnings.append("无法读取布局可绘制区域，已按整张纸居中打印。")

        if (
            abs(geometry.paper_width_mm - template.canvas_width_mm) > SIZE_TOLERANCE_MM
            or abs(geometry.paper_height_mm - template.canvas_height_mm) > SIZE_TOLERANCE_MM
        ):
            warnings.append(
                f"模板 {_format_mm(template.canvas_width_mm)}×"
                f"{_format_mm(template.canvas_height_mm)}mm 与纸张 "
                f"{_format_mm(geometry.paper_width_mm)}×"
                f"{_format_mm(geometry.paper_height_mm)}mm 不一致，"
                "将按 1:1 居中打印，不缩放。"
            )

        if printable is not None:
            _, _, printable_width, printable_height = printable
            if (
                template.canvas_width_mm > printable_width + 1e-6
                or template.canvas_height_mm > printable_height + 1e-6
            ):
                warnings.append(
                    "模板超出布局可绘制区域，边缘可能被裁切；"
                    "请检查纸张方向与页边距。"
                )

        warnings.append(HARD_MARGIN_NOTICE)
        if all(abs(value) < 0.01 for value in geometry.margins_mm):
            warnings.append(ZERO_MARGIN_NOTICE)

        if any(isinstance(element, PhotoElement) for element in template.elements):
            missing_photos = sum(1 for record in records if not record.get("photo_path"))
            if missing_photos:
                warnings.append(
                    f"{missing_photos} 条记录没有照片，照片区域将留空并产生渲染告警。"
                )
            unsafe = sum(
                1
                for record in records
                if record.get("photo_path")
                and not self._photo_path_is_safe(str(record.get("photo_path")))
            )
            if unsafe:
                warnings.append(
                    f"{unsafe} 条记录的照片路径不安全，这些记录将被判定为打印失败。"
                )

        return PrintPreflight(
            template_name=template.name,
            template_id=None,
            certificate_type=certificate_type,
            record_count=len(records),
            paper_mm=(geometry.paper_width_mm, geometry.paper_height_mm),
            printable_mm=printable,
            warnings=tuple(warnings),
            blocking_errors=tuple(blocking),
        )

    # ------------------------------------------------------------------ 打印

    def print_batch(
        self,
        records: Sequence[Mapping[str, Any]],
        template: CertificateTemplate | None,
        certificate_type: str,
        target: PrintTarget,
        *,
        template_id: int | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> PrintBatchResult:
        """逐张打印给定记录快照，并为每次尝试写入审计。"""
        if template is None:
            raise PrintError("没有可用的打印模板")

        preflight = self.preflight(records, template, certificate_type, target)
        preflight = PrintPreflight(
            template_name=preflight.template_name,
            template_id=template_id,
            certificate_type=preflight.certificate_type,
            record_count=preflight.record_count,
            paper_mm=preflight.paper_mm,
            printable_mm=preflight.printable_mm,
            warnings=preflight.warnings,
            blocking_errors=preflight.blocking_errors,
        )

        batch_id = self._new_batch_id()
        started_at = _utc_now()
        printer_name = target.name()
        number_field = self._number_field(certificate_type)
        attempts: list[PrintAttempt] = []
        not_started: list[tuple[int, str]] = []
        cancelled_by_user = False
        cancelled_count = 0
        warnings: list[str] = []

        if not preflight.can_print:
            not_started = [
                (int(record["id"]), str(record.get("student_no") or ""))
                for record in records
            ]
        else:
            device_dead = False
            for record in records:
                record_id = int(record["id"])
                student_no = str(record.get("student_no") or "")

                if should_cancel is not None and should_cancel():
                    cancelled_by_user = True
                    cancelled_count += 1
                    not_started.append((record_id, student_no))
                    continue
                if device_dead:
                    not_started.append((record_id, student_no))
                    continue

                attempt = self._print_one(
                    record,
                    template,
                    certificate_type,
                    number_field,
                    template_id,
                    printer_name,
                    target,
                    preflight,
                )
                attempts.append(attempt)
                if attempt.device_fault:
                    device_dead = True

        counts = {
            "rendered": sum(1 for attempt in attempts if attempt.rendered),
            "failed": sum(1 for attempt in attempts if attempt.result == PRINT_FAILED),
            "unknown": sum(1 for attempt in attempts if attempt.result == PRINT_UNKNOWN),
            "accepted": sum(1 for attempt in attempts if attempt.result == PRINT_ACCEPTED),
            "not_started": len(not_started),
            "audit_failed": sum(1 for attempt in attempts if not attempt.audit_saved),
            "cancelled": cancelled_count,
        }
        saved_ids = [
            attempt.audit_id
            for attempt in attempts
            if attempt.audit_saved and attempt.audit_id is not None
        ]

        audit_failures = [attempt for attempt in attempts if not attempt.audit_saved]
        if audit_failures:
            warnings.append(
                f"{len(audit_failures)} 条打印记录审计写入失败"
                f"（学号：{'、'.join(attempt.student_no or '（空）' for attempt in audit_failures[:5])}），"
                "批次与打印记录的关联不完整。"
            )

        summary, truncated, summary_warning = self._build_batch_summary(
            batch_id,
            template,
            template_id,
            printer_name,
            counts,
            saved_ids,
            preflight,
        )
        if truncated:
            warnings.append(summary_warning or "批次 id 清单已截断，无法据此完整关联。")

        batch_log_id, batch_log_saved, batch_warning = self._write_batch_log(
            summary, counts, preflight, attempts
        )
        if not batch_log_saved:
            warnings.append(batch_warning or "批次审计未写入，本次打印无法按批次关联。")

        finished_at = _utc_now()
        message = self._build_message(
            preflight, counts, cancelled_by_user, len(warnings)
        )
        return PrintBatchResult(
            batch_id=batch_id,
            printer_name=printer_name,
            template_name=template.name,
            template_id=template_id,
            certificate_type=certificate_type,
            preflight=preflight,
            attempts=tuple(attempts),
            not_started=tuple(not_started),
            cancelled_by_user=cancelled_by_user,
            batch_log_id=batch_log_id,
            batch_log_saved=batch_log_saved,
            warnings=tuple(warnings),
            message=message,
            started_at=started_at,
            finished_at=finished_at,
        )

    # ------------------------------------------------------------------ 单张

    def _print_one(
        self,
        record: Mapping[str, Any],
        template: CertificateTemplate,
        certificate_type: str,
        number_field: str,
        template_id: int | None,
        printer_name: str,
        target: PrintTarget,
        preflight: PrintPreflight,
    ) -> PrintAttempt:
        record_id = int(record["id"])
        student_no = str(record.get("student_no") or "")
        certificate_no = str(record.get(number_field) or "").strip()

        rendered = False
        result = PRINT_FAILED
        error: str | None = None
        render_warnings: tuple[str, ...] = ()
        device_fault = False

        try:
            painter = target.begin()
        except Exception as exc:  # noqa: BLE001 - 设备初始化异常按单条失败处理
            logger.exception("初始化打印设备失败（记录 %s）", record_id)
            painter = None
            error = f"无法初始化打印设备：{exc}"
            device_fault = True

        if painter is None:
            if error is None:
                error = "无法初始化打印设备"
            device_fault = True
        else:
            try:
                photo_file = self._resolve_photo(record)
                offset = self._offset_px(template, preflight, target.resolution())
                painter.save()
                try:
                    painter.translate(offset[0], offset[1])
                    render_warnings = tuple(
                        f"{warning.element_id}｜{warning.code}｜{warning.message}"
                        for warning in self.renderer.paint(
                            painter,
                            template,
                            record,
                            photo_file=photo_file,
                            dpi=target.resolution(),
                        )
                    )
                finally:
                    painter.restore()
                rendered = True
            except PhotoPathError as exc:
                target.abort()
                error = f"照片路径不安全：{exc}"
            except Exception as exc:  # noqa: BLE001 - 渲染异常按单条失败处理
                logger.exception("打印渲染失败（记录 %s）", record_id)
                target.abort()
                state = self._safe_state(target)
                if state in (STATE_ERROR, STATE_ABORTED):
                    error = f"打印设备异常（{state}）：{exc}"
                    device_fault = True
                else:
                    error = f"渲染失败：{exc}"
            else:
                try:
                    target.end()
                except Exception as exc:  # noqa: BLE001 - 结束状态无法确认
                    logger.exception("结束打印任务失败（记录 %s）", record_id)
                    state = self._safe_state(target)
                    if state in (STATE_ERROR, STATE_ABORTED):
                        result = PRINT_FAILED
                        error = f"打印设备异常（{state}）：{exc}"
                        device_fault = True
                    else:
                        result = PRINT_UNKNOWN
                        error = f"任务结束状态无法确认：{exc}"
                else:
                    state = self._safe_state(target)
                    if state in (STATE_ERROR, STATE_ABORTED):
                        result = PRINT_FAILED
                        error = f"打印设备状态异常：{state}"
                        device_fault = True
                    elif target.acceptance() is True:
                        result = PRINT_ACCEPTED
                    else:
                        result = PRINT_UNKNOWN

        audit_id, audit_saved, audit_error = self._write_attempt_audit(
            record_id,
            certificate_type,
            certificate_no,
            template_id,
            printer_name,
            result,
            error,
        )
        return PrintAttempt(
            record_id=record_id,
            student_no=student_no,
            certificate_no=certificate_no,
            rendered=rendered,
            result=result,
            error=error,
            warnings=render_warnings,
            audit_id=audit_id,
            audit_saved=audit_saved,
            audit_error=audit_error,
            device_fault=device_fault,
        )

    # ------------------------------------------------------------------ 坐标

    @staticmethod
    def _offset_px(
        template: CertificateTemplate,
        preflight: PrintPreflight,
        dpi: int,
    ) -> tuple[int, int]:
        """模板相对纸张左上角的设备像素偏移；毫米到像素只换算一次。"""
        scale = dpi / 25.4
        if preflight.printable_mm is not None:
            x_mm, y_mm, width_mm, height_mm = preflight.printable_mm
            offset_x_mm = x_mm + (width_mm - template.canvas_width_mm) / 2
            offset_y_mm = y_mm + (height_mm - template.canvas_height_mm) / 2
        else:
            offset_x_mm = (preflight.paper_mm[0] - template.canvas_width_mm) / 2
            offset_y_mm = (preflight.paper_mm[1] - template.canvas_height_mm) / 2
        return round(offset_x_mm * scale), round(offset_y_mm * scale)

    # ------------------------------------------------------------------ 审计

    def _write_attempt_audit(
        self,
        record_id: int,
        certificate_type: str,
        certificate_no: str,
        template_id: int | None,
        printer_name: str,
        result: str,
        error: str | None,
    ) -> tuple[int | None, bool, str | None]:
        if not certificate_no:
            return None, False, "证书编号为空，无法写入打印记录"
        try:
            with self.database.transaction() as connection:
                print_id = self.print_records.create(
                    record_id,
                    certificate_type,
                    certificate_no,
                    result,
                    template_id=template_id,
                    printer_name=printer_name,
                    error_message=error,
                    connection=connection,
                )
            return print_id, True, None
        except Exception as exc:  # noqa: BLE001 - 审计失败不得掩盖打印结果
            logger.exception("写入打印审计失败（记录 %s）", record_id)
            return None, False, str(exc)

    def _build_batch_summary(
        self,
        batch_id: str,
        template: CertificateTemplate,
        template_id: int | None,
        printer_name: str,
        counts: Mapping[str, int],
        saved_ids: Sequence[int],
        preflight: PrintPreflight,
    ) -> tuple[str, bool, str | None]:
        template_label = (
            f"{template.name}（id={template_id}）"
            if template_id is not None
            else f"{template.name}（内置资源，未入库）"
        )
        submission = (
            "无法确认"
            if counts["unknown"] or counts["not_started"]
            else ("设备已接受" if counts["accepted"] else "无提交")
        )
        head = (
            f"批次 {batch_id}｜模板：{template_label}/"
            f"{_format_mm(template.canvas_width_mm)}×"
            f"{_format_mm(template.canvas_height_mm)}mm｜打印机：{printer_name}｜"
            f"渲染完成 {counts['rendered']}｜失败 {counts['failed']}｜"
            f"结果未知 {counts['unknown']}｜已接受 {counts['accepted']}｜"
            f"用户中止 {counts['cancelled']}｜未开始 {counts['not_started']}｜"
            f"审计失败 {counts['audit_failed']}｜提交状态：{submission}"
        )
        if preflight.blocking_errors:
            head += "｜未开始原因：" + "；".join(preflight.blocking_errors[:3])

        prefix = "｜打印记录 id："
        available = BATCH_SUMMARY_LIMIT - len(head) - len(prefix)
        if not saved_ids:
            text = "无（没有成功写入的打印记录）"
            return head + prefix + text, False, None

        full = ",".join(str(print_id) for print_id in saved_ids)
        if len(full) <= available:
            return head + prefix + full, False, None

        suffix = f"…（清单已截断，共 {len(saved_ids)} 条）"
        budget = max(0, available - len(suffix) - 1)
        kept: list[str] = []
        for print_id in saved_ids:
            candidate = ",".join([*kept, str(print_id)])
            if len(candidate) > budget:
                break
            kept.append(str(print_id))
        warning = (
            "批次 id 清单已截断（共 "
            f"{len(saved_ids)} 条，仅列出 {len(kept)} 条），无法据此完整关联。"
        )
        summary = head + prefix + (",".join(kept) + "…" if kept else "") + suffix
        return summary, True, warning

    def _write_batch_log(
        self,
        summary: str,
        counts: Mapping[str, int],
        preflight: PrintPreflight,
        attempts: Sequence[PrintAttempt],
    ) -> tuple[int | None, bool, str | None]:
        if counts["failed"] or counts["not_started"] or counts["audit_failed"]:
            result = "failure"
        elif counts["unknown"]:
            result = "unknown"
        elif counts["accepted"]:
            result = "success"
        else:
            result = "failure"

        errors = [attempt.error for attempt in attempts if attempt.error]
        error_message = "；".join(errors[:5]) if errors else None
        if preflight.blocking_errors and error_message is None:
            error_message = "；".join(preflight.blocking_errors[:5])

        try:
            log_id = self.logs.create(
                OPERATION_BATCH,
                TARGET_TEMPLATE,
                str(preflight.template_id) if preflight.template_id is not None else None,
                summary,
                result,
                error_message=error_message,
            )
            return log_id, True, None
        except Exception:  # noqa: BLE001 - 批次日志失败不得掩盖打印结果
            logger.exception("写入打印批次审计失败")
            return None, False, "批次审计未写入，本次打印无法按批次关联。"

    @staticmethod
    def _build_message(
        preflight: PrintPreflight,
        counts: Mapping[str, int],
        cancelled_by_user: bool,
        warning_count: int,
    ) -> str:
        if preflight.blocking_errors:
            return "未开始打印：" + "；".join(preflight.blocking_errors)
        parts = [
            f"渲染完成 {counts['rendered']} 张",
            f"失败 {counts['failed']} 张",
            f"结果未知 {counts['unknown']} 张",
            f"未开始 {counts['not_started']} 张",
        ]
        if counts["accepted"]:
            parts.append(f"驱动已接受 {counts['accepted']} 张（不代表纸张已输出）")
        if cancelled_by_user and counts["cancelled"]:
            parts.append(f"用户中止 {counts['cancelled']} 张未开始")
        if not (counts["accepted"] and not counts["unknown"] and not counts["failed"]):
            parts.append("提交状态无法确认，请检查打印机队列")
        if counts["audit_failed"]:
            parts.append(f"审计不完整：{counts['audit_failed']} 条打印记录未写入")
        elif warning_count:
            parts.append(f"{warning_count} 条告警")
        return "｜".join(parts)

    # ------------------------------------------------------------------ 辅助

    def _resolve_photo(self, record: Mapping[str, Any]) -> Path | None:
        photo_path = record.get("photo_path")
        if not photo_path:
            return None
        if self.paths is None:
            raise PhotoPathError("未配置照片目录，无法解析照片")
        try:
            return photo_file_path(self.paths.photos_dir, photo_path)
        except ValueError as exc:
            raise PhotoPathError(str(exc)) from exc

    def _photo_path_is_safe(self, photo_path: str) -> bool:
        if self.paths is None:
            return False
        try:
            photo_file_path(self.paths.photos_dir, photo_path)
            return True
        except ValueError:
            return False

    @staticmethod
    def _safe_state(target: PrintTarget) -> str:
        try:
            return target.state()
        except Exception:  # noqa: BLE001
            return "unknown"

    @staticmethod
    def _new_batch_id() -> str:
        now = datetime.now(timezone.utc)
        return f"PRINT-{now.strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:6]}"
