"""打印服务测试：状态判定、坐标换算、设备失效、审计与批次日志。"""

from __future__ import annotations

from typing import Any

import pytest
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from app.config.paths import AppPaths
from app.data.db import Database
from app.services import PrintService, TemplateService
from app.services import print_service as print_service_module
from app.services.errors import PrintError
from app.services.print_service import (
    PRINT_ACCEPTED,
    PRINT_FAILED,
    PRINT_UNKNOWN,
    PrintPreflight,
)
from app.services.print_target import STATE_ABORTED, STATE_ERROR
from app.services.template_model import (
    CertificateTemplate,
    FontSpec,
    PhotoElement,
    TextElement,
)
from app.services.template_renderer import CertificateRenderer
from print_fakes import FakePrintTarget


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _text_template(
    *,
    width_mm: float = 100.0,
    height_mm: float = 50.0,
    with_photo: bool = False,
) -> CertificateTemplate:
    elements: list[TextElement | PhotoElement] = [
        TextElement(
            id="name_value",
            x_mm=10.0,
            y_mm=10.0,
            width_mm=40.0,
            height_mm=10.0,
            font=FontSpec(family="Microsoft YaHei", size_pt=12.0),
            field="name",
        )
    ]
    if with_photo:
        elements.append(
            PhotoElement(
                id="photo",
                x_mm=60.0,
                y_mm=10.0,
                width_mm=30.0,
                height_mm=40.0,
            )
        )
    return CertificateTemplate(
        name="打印模板",
        certificate_type="graduation",
        canvas_width_mm=width_mm,
        canvas_height_mm=height_mm,
        elements=tuple(elements),
    )


def _record(record_id: int, student_no: str | None = None, **values: Any) -> dict[str, Any]:
    return {
        "id": record_id,
        "student_no": student_no if student_no is not None else f"S-{record_id:03d}",
        "name": "张三",
        "certificate_no": f"BJ{record_id:04d}",
        "photo_path": None,
        **values,
    }


@pytest.fixture
def service(repository_database: Database, app_paths: AppPaths) -> PrintService:
    templates = TemplateService(repository_database, app_paths)
    resolved = PrintService(repository_database, templates, app_paths)
    # 打印审计带外键约束：预建 12 条真实记录，供 _record(index) 使用。
    for index in range(1, 13):
        resolved.records.create(
            {
                "student_no": f"S-{index:03d}",
                "name": "张三",
                "certificate_no": f"BJ{index:04d}",
            }
        )
    return resolved


def _template_id(service: PrintService, template: CertificateTemplate) -> int:
    return service.templates.create_template(template)


# ---------------------------------------------------------------- 预检


def test_preflight_blocks_missing_certificate_numbers(
    service: PrintService, qapp: QApplication
) -> None:
    records = [
        _record(1),
        _record(2, certificate_no=None),
        _record(3, certificate_no="   "),
    ]
    preflight = service.preflight(
        records, _text_template(), "graduation", FakePrintTarget()
    )

    assert preflight.blocking
    assert any("证书编号" in item and "S-002" in item for item in preflight.blocking)
    assert preflight.can_print is False


def test_preflight_warns_size_mismatch_and_margin_limits(
    service: PrintService, qapp: QApplication
) -> None:
    target = FakePrintTarget(paper_mm=(210.0, 297.0))

    preflight = service.preflight(
        [_record(1)], _text_template(), "graduation", target
    )

    assert preflight.blocking == ()
    joined = "\n".join(preflight.warnings)
    assert "不一致" in joined and "不缩放" in joined
    # 无法获取硬件边距属于固定限制提示，不能省略。
    assert "硬边距" in joined
    assert "0 不代表硬件支持无边距" in joined


def test_preflight_warns_template_exceeding_layout_area(
    service: PrintService, qapp: QApplication
) -> None:
    target = FakePrintTarget(
        paper_mm=(100.0, 50.0), printable=(5.0, 5.0, 80.0, 40.0)
    )

    preflight = service.preflight([_record(1)], _text_template(), "graduation", target)

    assert any("超出布局可绘制区域" in item for item in preflight.warnings)


def test_preflight_warns_unreadable_layout_area(
    service: PrintService, qapp: QApplication
) -> None:
    preflight = service.preflight(
        [_record(1)], _text_template(), "graduation", FakePrintTarget(printable=None)
    )

    assert any("无法读取布局可绘制区域" in item for item in preflight.warnings)


def test_preflight_blocks_invalid_device(
    service: PrintService, qapp: QApplication
) -> None:
    preflight = service.preflight(
        [_record(1)], _text_template(), "graduation", FakePrintTarget(valid=False)
    )

    assert preflight.can_print is False
    assert any("打印设备不可用" in item for item in preflight.blocking)


def test_preflight_counts_missing_photos(
    service: PrintService, qapp: QApplication
) -> None:
    preflight = service.preflight(
        [_record(1), _record(2)],
        _text_template(with_photo=True),
        "graduation",
        FakePrintTarget(),
    )

    assert any("没有照片" in item for item in preflight.warnings)


# ---------------------------------------------------------------- 单张/批量


def test_print_single_record_renders_and_records_unknown(
    service: PrintService, qapp: QApplication
) -> None:
    template = _text_template()
    template_id = _template_id(service, template)
    called: list[dict[str, Any]] = []

    original_paint = CertificateRenderer.paint

    def _spy(self, painter, template_arg, values, *, photo_file=None, dpi=300):
        called.append({"values": dict(values), "dpi": dpi})
        return original_paint(
            self, painter, template_arg, values, photo_file=photo_file, dpi=dpi
        )

    CertificateRenderer.paint = _spy  # type: ignore[method-assign]
    try:
        target = FakePrintTarget()
        result = service.print_batch(
            [_record(1)],
            template,
            "graduation",
            target,
            template_id=template_id,
        )
    finally:
        CertificateRenderer.paint = original_paint  # type: ignore[method-assign]

    assert len(called) == 1
    assert called[0]["dpi"] == 300
    assert called[0]["values"]["certificate_no"] == "BJ0001"

    attempt = result.attempts[0]
    assert attempt.rendered is True
    assert attempt.result == PRINT_UNKNOWN
    assert attempt.audit_saved is True

    row = service.print_records.get(attempt.audit_id)
    assert row is not None
    assert row["certificate_record_id"] == 1
    assert row["certificate_type"] == "graduation"
    assert row["certificate_no_snapshot"] == "BJ0001"
    assert row["template_id"] == template_id
    assert row["printer_name"] == "测试打印机"
    assert row["result"] == PRINT_UNKNOWN
    assert "成功" not in result.message
    assert "无法确认" in result.message


def test_print_batch_preserves_order_and_writes_each_attempt(
    service: PrintService, qapp: QApplication
) -> None:
    records = [_record(index) for index in (3, 1, 2)]
    target = FakePrintTarget()

    result = service.print_batch(records, _text_template(), "graduation", target)

    assert target.begin_count == 3
    assert [attempt.record_id for attempt in result.attempts] == [3, 1, 2]
    assert result.unknown_count == 3
    assert result.failed_count == 0
    assert result.not_started_count == 0
    assert service.print_records.count_records() == 3


def test_accepted_only_when_target_reports_acceptance(
    service: PrintService, qapp: QApplication
) -> None:
    result = service.print_batch(
        [_record(1)],
        _text_template(),
        "graduation",
        FakePrintTarget(acceptance=True),
    )

    assert result.attempts[0].result == PRINT_ACCEPTED
    assert result.accepted_count == 1
    assert result.unknown_count == 0


def test_end_failure_with_error_state_is_failed_and_stops(
    service: PrintService, qapp: QApplication
) -> None:
    records = [_record(1), _record(2), _record(3)]
    target = FakePrintTarget()
    target.end_error = "驱动会话异常"
    target._state = STATE_ERROR  # noqa: SLF001 - 测试替身

    result = service.print_batch(records, _text_template(), "graduation", target)

    assert len(result.attempts) == 1
    assert result.attempts[0].result == PRINT_FAILED
    assert "打印设备" in (result.attempts[0].error or "")
    assert target.begin_count == 1
    assert result.not_started_count == 2
    assert [record_id for record_id, _ in result.not_started] == [2, 3]
    assert service.print_records.count_records() == 1


def test_end_failure_without_evidence_is_unknown(
    service: PrintService, qapp: QApplication
) -> None:
    target = FakePrintTarget()
    target.end_error = "结束状态不可读"

    result = service.print_batch([_record(1)], _text_template(), "graduation", target)

    assert result.attempts[0].result == PRINT_UNKNOWN
    assert result.failed_count == 0


def test_render_error_continues_with_next_record(
    service: PrintService, qapp: QApplication
) -> None:
    calls = {"count": 0}

    class _FlakyRenderer(CertificateRenderer):
        def paint(self, painter, template_arg, values, *, photo_file=None, dpi=300):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("渲染异常")
            return super().paint(
                painter, template_arg, values, photo_file=photo_file, dpi=dpi
            )

    service.renderer = _FlakyRenderer()
    target = FakePrintTarget()

    result = service.print_batch(
        [_record(1), _record(2)], _text_template(), "graduation", target
    )

    assert result.attempts[0].result == PRINT_FAILED
    assert result.attempts[0].rendered is False
    assert result.attempts[1].result == PRINT_UNKNOWN
    assert target.begin_count == 2
    assert result.not_started_count == 0


def test_unsafe_photo_path_fails_record(
    service: PrintService, qapp: QApplication
) -> None:
    result = service.print_batch(
        [_record(1, photo_path="../secret.jpg")],
        _text_template(with_photo=True),
        "graduation",
        FakePrintTarget(),
    )

    assert result.attempts[0].result == PRINT_FAILED
    assert "照片路径" in (result.attempts[0].error or "")
    assert result.attempts[0].audit_saved is True


def test_cancel_between_records_leaves_not_started(
    service: PrintService, qapp: QApplication
) -> None:
    target = FakePrintTarget()
    state = {"count": 0}

    def _should_cancel() -> bool:
        state["count"] += 1
        return state["count"] > 1

    result = service.print_batch(
        [_record(1), _record(2), _record(3)],
        _text_template(),
        "graduation",
        target,
        should_cancel=_should_cancel,
    )

    assert result.cancelled_by_user is True
    assert len(result.attempts) == 1
    assert target.begin_count == 1
    assert result.not_started_count == 2
    assert service.print_records.count_records() == 1
    # 取消数量只统计因用户中止而未被开始的记录，且如实写入批次摘要。
    batch_log = service.logs.get(result.batch_log_id)
    assert batch_log is not None
    assert "用户中止 2" in batch_log["summary"]
    assert "用户中止 2 张未开始" in result.message


def test_cancel_without_skipped_records_reports_zero(
    service: PrintService, qapp: QApplication
) -> None:
    target = FakePrintTarget()

    result = service.print_batch(
        [_record(1)],
        _text_template(),
        "graduation",
        target,
        should_cancel=lambda: False,
    )

    assert result.cancelled_by_user is False
    assert "用户中止" not in result.message
    batch_log = service.logs.get(result.batch_log_id)
    assert batch_log is not None
    assert "用户中止 0" in batch_log["summary"]


def test_begin_exception_fails_record_and_stops_batch(
    service: PrintService, qapp: QApplication
) -> None:
    records = [_record(1), _record(2), _record(3)]
    target = FakePrintTarget(begin_error="驱动拒绝打开设备")

    result = service.print_batch(records, _text_template(), "graduation", target)

    assert len(result.attempts) == 1
    attempt = result.attempts[0]
    assert attempt.rendered is False
    assert attempt.result == PRINT_FAILED
    assert "无法初始化打印设备" in (attempt.error or "")
    assert attempt.audit_saved is True
    assert attempt.audit_id is not None
    assert result.failed_count == 1
    assert result.not_started_count == 2
    assert [record_id for record_id, _ in result.not_started] == [2, 3]
    assert target.begin_count == 1
    assert target.end_count == 0
    assert service.print_records.count_records() == 1

    row = service.print_records.get(attempt.audit_id)
    assert row is not None
    assert row["result"] == PRINT_FAILED
    assert "驱动拒绝打开设备" in (row["error_message"] or "")

    assert result.batch_log_saved is True
    batch_log = service.logs.get(result.batch_log_id)
    assert batch_log is not None
    assert batch_log["result"] == "failure"
    assert "失败 1" in batch_log["summary"]
    assert "未开始 2" in batch_log["summary"]


def test_invalid_device_on_start_marks_all_failed(
    service: PrintService, qapp: QApplication
) -> None:
    target = FakePrintTarget(fail_begin=True)

    result = service.print_batch(
        [_record(1), _record(2)], _text_template(), "graduation", target
    )

    assert len(result.attempts) == 1
    assert result.attempts[0].result == PRINT_FAILED
    assert result.not_started_count == 1
    assert target.begin_count == 1


def test_blocked_preflight_writes_no_attempts(
    service: PrintService, qapp: QApplication
) -> None:
    result = service.print_batch(
        [_record(1, certificate_no=None)],
        _text_template(),
        "graduation",
        FakePrintTarget(),
    )

    assert result.attempts == ()
    assert result.not_started_count == 1
    assert result.blocking_errors
    assert service.print_records.count_records() == 0
    assert result.batch_log_saved is True


# ---------------------------------------------------------------- 坐标


def _expected_page(
    template: CertificateTemplate,
    values: dict[str, Any],
    *,
    dpi: int,
    paper_mm: tuple[float, float],
    offset_mm: tuple[float, float],
) -> QImage:
    page = QImage(
        round(paper_mm[0] / 25.4 * dpi),
        round(paper_mm[1] / 25.4 * dpi),
        QImage.Format.Format_ARGB32,
    )
    page.fill(QColor("white"))
    rendered = CertificateRenderer().render_to_image(template, values, dpi=dpi)
    painter = QPainter(page)
    try:
        painter.drawImage(
            round(offset_mm[0] / 25.4 * dpi),
            round(offset_mm[1] / 25.4 * dpi),
            rendered.image,
        )
    finally:
        painter.end()
    return page


def test_offset_centers_canvas_within_layout_area(
    service: PrintService, qapp: QApplication
) -> None:
    template = _text_template()
    target = FakePrintTarget(
        paper_mm=(200.0, 100.0), printable=(10.0, 5.0, 180.0, 90.0)
    )
    record = _record(1)

    service.print_batch([record], template, "graduation", target)

    expected = _expected_page(
        template,
        record,
        dpi=300,
        paper_mm=(200.0, 100.0),
        offset_mm=(10.0 + (180.0 - 100.0) / 2, 5.0 + (90.0 - 50.0) / 2),
    )
    assert target.images[0] == expected


def test_paper_smaller_than_template_keeps_canvas_pixels(
    service: PrintService, qapp: QApplication
) -> None:
    template = _text_template(width_mm=297.0, height_mm=210.0)
    target = FakePrintTarget(paper_mm=(210.0, 297.0))
    record = _record(1)

    result = service.print_batch([record], template, "graduation", target)

    reference = CertificateRenderer().render_to_image(template, record, dpi=300)
    assert reference.width_px == round(297.0 / 25.4 * 300)
    assert reference.height_px == round(210.0 / 25.4 * 300)
    assert target.images[0].width() == round(210.0 / 25.4 * 300)
    assert any("不一致" in item for item in result.preflight.warnings)


def test_invalid_dpi_blocks_printing(
    service: PrintService, qapp: QApplication
) -> None:
    target = FakePrintTarget(dpi=0)

    preflight = service.preflight([_record(1)], _text_template(), "graduation", target)

    assert preflight.can_print is False
    assert any("分辨率" in item for item in preflight.blocking)


# ---------------------------------------------------------------- 审计


def test_audit_failure_keeps_print_result_and_warns(
    service: PrintService,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*args: object, **kwargs: object) -> int:
        raise RuntimeError("审计库不可写")

    monkeypatch.setattr(service.print_records, "create", _boom)

    result = service.print_batch(
        [_record(1), _record(2)], _text_template(), "graduation", FakePrintTarget()
    )

    assert [attempt.result for attempt in result.attempts] == [
        PRINT_UNKNOWN,
        PRINT_UNKNOWN,
    ]
    assert all(attempt.audit_saved is False for attempt in result.attempts)
    assert result.audit_failed_count == 2
    assert any("审计" in warning for warning in result.warnings)
    assert "审计不完整" in result.message


def test_batch_log_lists_only_saved_print_record_ids(
    service: PrintService,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_create = service.print_records.create

    def _fail_second(*args: object, **kwargs: object) -> int:
        if args[0] == 2:
            raise RuntimeError("第二条审计写入失败")
        return original_create(*args, **kwargs)

    monkeypatch.setattr(service.print_records, "create", _fail_second)

    result = service.print_batch(
        [_record(1), _record(2), _record(3)],
        _text_template(),
        "graduation",
        FakePrintTarget(),
    )

    saved_ids = [attempt.audit_id for attempt in result.attempts if attempt.audit_saved]
    assert len(saved_ids) == 2
    assert result.batch_log_id is not None
    batch_log = service.logs.get(result.batch_log_id)
    assert batch_log is not None
    assert batch_log["operation_type"] == "certificate_print_batch"
    for record_id in saved_ids:
        assert str(record_id) in batch_log["summary"]
    assert str(result.attempts[1].audit_id) not in batch_log["summary"]
    assert "审计失败 1" in batch_log["summary"]
    assert "不完整" in " ".join(result.warnings)


def test_batch_log_includes_batch_id_template_and_counts(
    service: PrintService, qapp: QApplication
) -> None:
    template = _text_template()
    template_id = _template_id(service, template)

    result = service.print_batch(
        [_record(1), _record(2)],
        template,
        "graduation",
        FakePrintTarget(),
        template_id=template_id,
    )

    batch_log = service.logs.get(result.batch_log_id)
    assert batch_log is not None
    summary = batch_log["summary"]
    assert result.batch_id in summary
    assert "打印模板" in summary
    assert "100×50mm" in summary
    assert "测试打印机" in summary
    assert "渲染完成 2" in summary
    assert "结果未知 2" in summary
    assert batch_log["result"] == "unknown"
    assert batch_log["target_type"] == "certificate_template"
    assert batch_log["target_id"] == str(template_id)


def test_batch_log_failure_reported(
    service: PrintService,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*args: object, **kwargs: object) -> int:
        raise RuntimeError("日志库不可写")

    monkeypatch.setattr(service.logs, "create", _boom)

    result = service.print_batch(
        [_record(1)], _text_template(), "graduation", FakePrintTarget()
    )

    assert result.batch_log_saved is False
    assert result.batch_log_id is None
    assert any("批次审计未写入" in warning for warning in result.warnings)
    # 单条打印审计不受影响。
    assert result.attempts[0].audit_saved is True


def test_batch_summary_truncates_long_id_list(
    service: PrintService, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(print_service_module, "BATCH_SUMMARY_LIMIT", 100)

    records = [_record(index) for index in range(1, 13)]
    result = service.print_batch(
        records, _text_template(), "graduation", FakePrintTarget()
    )

    batch_log = service.logs.get(result.batch_log_id)
    assert batch_log is not None
    summary = batch_log["summary"]
    assert "截断" in summary
    assert result.batch_id in summary
    assert any("截断" in warning for warning in result.warnings)


def test_print_error_when_template_missing(
    service: PrintService, qapp: QApplication
) -> None:
    with pytest.raises(PrintError):
        service.print_batch([_record(1)], None, "graduation", FakePrintTarget())


def test_preflight_dataclass_is_exported() -> None:
    assert PrintPreflight.__name__ == "PrintPreflight"
    assert STATE_ABORTED == "aborted"
