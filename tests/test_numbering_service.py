"""NumberingService 测试：预览只读、生成事务、冲突回滚、审计与并发。"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from typing import Any

import pytest

from app.data.db import Database
from app.services import (
    AuditWriteError,
    NumberConflictError,
    NumberingService,
    RecordNotFoundError,
    ValidationError,
)


def _create_record(service: NumberingService, student_no: str, **values: Any) -> int:
    return service.records.create({"student_no": student_no, "name": "测试", **values})


def _sequence_value(service: NumberingService, rule_key: str) -> int:
    return int(service.sequences.get(rule_key)["next_value"])


# ---------------------------------------------------------------- 预览


def test_preview_is_read_only(numbering_service: NumberingService) -> None:
    numbering_service.update_rule(
        "graduation", {"fixed_text_1": "GX", "sequence_width": 4}
    )

    preview = numbering_service.preview("graduation")

    assert preview.number == "GX0001"
    assert preview.sequence_value == 1
    # 预览不写数据库、不递增流水、不占用编号。
    assert _sequence_value(numbering_service, "graduation") == 1
    assert numbering_service.records.count_records() == 0
    assert numbering_service.preview("graduation").number == "GX0001"


def test_preview_keeps_sequence_beyond_width(numbering_service: NumberingService) -> None:
    numbering_service.sequences.update_next_value("graduation", 10000)

    preview = numbering_service.preview("graduation")

    assert preview.number == "10000"


def test_preview_missing_or_disabled_rule_is_reported(
    numbering_service: NumberingService,
) -> None:
    with numbering_service.database.transaction() as connection:
        connection.execute(
            "DELETE FROM numbering_rules WHERE rule_key = ?", ("other",)
        )

    with pytest.raises(ValidationError):
        numbering_service.preview("other")

    numbering_service.update_rule("degree", {"enabled": False})
    with pytest.raises(ValidationError):
        numbering_service.preview("degree")
    # 其他类型不受影响。
    assert numbering_service.preview("graduation").number == "0001"


def test_invalid_type_rejected(numbering_service: NumberingService) -> None:
    for rule_key in ("unknown", "", None, 1):
        with pytest.raises(ValidationError):
            numbering_service.preview(rule_key)  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            numbering_service.generate_number(1, rule_key)  # type: ignore[arg-type]


# ---------------------------------------------------------------- 正式生成


def test_generate_writes_number_increments_sequence_and_logs(
    numbering_service: NumberingService,
) -> None:
    numbering_service.update_rule(
        "graduation",
        {
            "fixed_text_1": " GX ",
            "fixed_text_2": "2026",
            "sequence_width": 4,
        },
    )
    record_id = _create_record(numbering_service, "S-1")

    result = numbering_service.generate_number(record_id, "graduation")

    assert result.number == "GX20260001"
    assert result.sequence_value == 1
    assert result.next_value == 2
    row = numbering_service.records.get(record_id)
    assert row["certificate_no"] == "GX20260001"
    assert row["degree_certificate_no"] is None
    assert _sequence_value(numbering_service, "graduation") == 2

    logs = numbering_service.logs.list_logs(
        operation_type="certificate_number_generate"
    )
    assert len(logs) == 1
    assert logs[0]["result"] == "success"
    assert logs[0]["target_id"] == str(record_id)
    assert "GX20260001" in logs[0]["summary"]


def test_generate_rejects_record_that_already_has_number(
    numbering_service: NumberingService,
) -> None:
    record_id = _create_record(
        numbering_service, "S-1", certificate_no="OLD-001"
    )

    with pytest.raises(NumberConflictError):
        numbering_service.generate_number(record_id, "graduation")

    assert numbering_service.records.get(record_id)["certificate_no"] == "OLD-001"
    assert _sequence_value(numbering_service, "graduation") == 1


def test_generate_conflict_rolls_back_without_consuming_sequence(
    numbering_service: NumberingService,
) -> None:
    numbering_service.update_rule("graduation", {"fixed_text_1": "GX"})
    _create_record(numbering_service, "S-1", certificate_no="GX0001")
    record_id = _create_record(numbering_service, "S-2")

    with pytest.raises(NumberConflictError):
        numbering_service.generate_number(record_id, "graduation")

    assert numbering_service.records.get(record_id)["certificate_no"] is None
    assert _sequence_value(numbering_service, "graduation") == 1
    # 冲突不会覆盖已有编号。
    assert numbering_service.records.get_by_student_no("S-1")["certificate_no"] == "GX0001"


def test_generate_missing_record_does_not_consume_sequence(
    numbering_service: NumberingService,
) -> None:
    with pytest.raises(RecordNotFoundError):
        numbering_service.generate_number(999, "graduation")
    assert _sequence_value(numbering_service, "graduation") == 1


def test_generate_audit_failure_rolls_back(numbering_service: NumberingService) -> None:
    record_id = _create_record(numbering_service, "S-1")

    def broken_create(*args: Any, **kwargs: Any) -> int:
        raise sqlite3.Error("模拟审计失败")

    numbering_service.logs.create = broken_create  # type: ignore[method-assign]

    with pytest.raises(AuditWriteError):
        numbering_service.generate_number(record_id, "graduation")

    assert numbering_service.records.get(record_id)["certificate_no"] is None
    assert _sequence_value(numbering_service, "graduation") == 1


def test_failure_audit_does_not_override_business_error(
    numbering_service: NumberingService,
) -> None:
    def broken_create(*args: Any, **kwargs: Any) -> int:
        raise RuntimeError("模拟失败审计也失败")

    numbering_service.logs.create = broken_create  # type: ignore[method-assign]

    with pytest.raises(RecordNotFoundError):
        numbering_service.generate_number(999, "graduation")


def test_types_keep_independent_sequences_and_fields(
    numbering_service: NumberingService,
) -> None:
    numbering_service.update_rule("graduation", {"fixed_text_1": "G"})
    numbering_service.update_rule("degree", {"fixed_text_1": "D"})
    record_id = _create_record(numbering_service, "S-1")

    graduation = numbering_service.generate_number(record_id, "graduation")
    degree = numbering_service.generate_number(record_id, "degree")

    assert graduation.number == "G0001"
    assert degree.number == "D0001"
    row = numbering_service.records.get(record_id)
    assert row["certificate_no"] == "G0001"
    assert row["degree_certificate_no"] == "D0001"
    assert _sequence_value(numbering_service, "graduation") == 2
    assert _sequence_value(numbering_service, "degree") == 2
    assert _sequence_value(numbering_service, "other") == 1


def test_same_text_allowed_across_different_number_fields(
    numbering_service: NumberingService,
) -> None:
    record_id = _create_record(numbering_service, "S-1")

    first = numbering_service.generate_number(record_id, "graduation")
    second = numbering_service.generate_number(record_id, "degree")

    assert first.number == second.number == "0001"


# ---------------------------------------------------------------- 规则更新


def test_update_rule_trims_fragments_and_logs_success(
    numbering_service: NumberingService,
) -> None:
    numbering_service.update_rule(
        "graduation",
        {
            "rule_name": " 毕结业证书 ",
            "fixed_text_1": "  GX 学 ",
            "fixed_text_2": "   ",
            "fixed_text_3": "",
            "fixed_text_4": "2026",
            "sequence_width": 6,
            "enabled": False,
        },
    )

    view = numbering_service.get_rule("graduation")
    assert view.rule_name == "毕结业证书"
    assert view.fixed_texts == ("GX 学", "", "", "2026")
    assert view.sequence_width == 6
    assert view.enabled is False
    assert view.next_value == 1

    logs = numbering_service.logs.list_logs(operation_type="numbering_rule_update")
    assert len(logs) == 1
    assert logs[0]["result"] == "success"
    assert logs[0]["target_id"] == "graduation"


def test_update_rule_audit_failure_rolls_back(
    numbering_service: NumberingService,
) -> None:
    def broken_create(*args: Any, **kwargs: Any) -> int:
        raise sqlite3.Error("模拟审计失败")

    numbering_service.logs.create = broken_create  # type: ignore[method-assign]

    with pytest.raises(AuditWriteError):
        numbering_service.update_rule("graduation", {"fixed_text_1": "GX"})

    view = numbering_service.get_rule("graduation")
    assert view.fixed_texts[0] == ""
    assert view.sequence_width == 4


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"unknown": "x"},
        {"rule_name": "   "},
        {"rule_name": 123},
        {"fixed_text_1": 123},
        {"fixed_text_1": None},
        {"sequence_width": 0},
        {"sequence_width": -1},
        {"sequence_width": 21},
        {"sequence_width": 999_999_999},
        {"sequence_width": True},
        {"sequence_width": "4"},
        {"sequence_width": 4.0},
        {"sequence_width": None},
        {"enabled": 2},
        {"enabled": "1"},
    ],
)
def test_update_rule_rejects_invalid_changes(
    numbering_service: NumberingService, changes: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError):
        numbering_service.update_rule("graduation", changes)

    view = numbering_service.get_rule("graduation")
    assert view.rule_name == "毕结业证书"
    assert view.fixed_texts == ("", "", "", "")
    assert view.sequence_width == 4
    assert view.enabled is True


def test_update_rule_accepts_width_boundaries(
    numbering_service: NumberingService,
) -> None:
    numbering_service.update_rule("graduation", {"sequence_width": 1})
    assert numbering_service.preview("graduation").number == "1"

    numbering_service.update_rule("graduation", {"sequence_width": 20})
    assert numbering_service.preview("graduation").number == "0" * 19 + "1"


@pytest.mark.parametrize("width", [21, 999_999_999])
def test_stored_invalid_width_is_reported_without_writing(
    numbering_service: NumberingService, width: int
) -> None:
    record_id = _create_record(numbering_service, "S-1")
    with numbering_service.database.transaction() as connection:
        connection.execute(
            "UPDATE numbering_rules SET sequence_width = ? WHERE rule_key = ?",
            (width, "graduation"),
        )

    # 规则仍可读取，便于界面修正异常配置。
    assert numbering_service.get_rule("graduation").sequence_width == width

    with pytest.raises(ValidationError, match="1–20"):
        numbering_service.preview("graduation")
    with pytest.raises(ValidationError, match="1–20"):
        numbering_service.generate_number(record_id, "graduation")

    assert numbering_service.records.get(record_id)["certificate_no"] is None
    assert _sequence_value(numbering_service, "graduation") == 1


# ---------------------------------------------------------------- 并发


def _run_concurrently(tasks: list[Callable[[], Any]]) -> list[tuple[str, Any]]:
    """让所有任务在同一屏障后并发执行，返回 (状态, 结果或异常)。"""
    barrier = threading.Barrier(len(tasks))
    results: list[tuple[str, Any]] = []
    lock = threading.Lock()

    def worker(task: Callable[[], Any]) -> None:
        barrier.wait()
        try:
            outcome: tuple[str, Any] = ("ok", task())
        except Exception as exc:  # noqa: BLE001 - 测试需要收集失败结果
            outcome = ("error", exc)
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker, args=(task,)) for task in tasks]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
        assert not thread.is_alive(), "并发任务超时未完成"
    return results


def test_concurrent_generation_for_different_records(
    numbering_service: NumberingService,
) -> None:
    record_ids = [
        _create_record(numbering_service, f"S-{index}") for index in range(4)
    ]

    results = _run_concurrently(
        [
            (lambda record_id=record_id: numbering_service.generate_number(
                record_id, "graduation"
            ))
            for record_id in record_ids
        ]
    )

    assert all(status == "ok" for status, _ in results), results
    numbers = sorted(
        numbering_service.records.get(record_id)["certificate_no"]
        for record_id in record_ids
    )
    assert numbers == ["0001", "0002", "0003", "0004"]
    assert _sequence_value(numbering_service, "graduation") == 5


def test_concurrent_generation_for_same_record_consumes_sequence_once(
    numbering_service: NumberingService,
) -> None:
    record_id = _create_record(numbering_service, "S-1")

    results = _run_concurrently(
        [
            lambda: numbering_service.generate_number(record_id, "graduation"),
            lambda: numbering_service.generate_number(record_id, "graduation"),
        ]
    )

    statuses = [status for status, _ in results]
    assert statuses.count("ok") == 1
    errors = [value for status, value in results if status == "error"]
    assert len(errors) == 1
    assert isinstance(errors[0], NumberConflictError)

    assert numbering_service.records.get(record_id)["certificate_no"] == "0001"
    assert _sequence_value(numbering_service, "graduation") == 2


def test_service_uses_repository_connections(
    numbering_service: NumberingService, repository_database: Database
) -> None:
    record_id = _create_record(numbering_service, "S-1")

    numbering_service.generate_number(record_id, "graduation")

    # 生成结束后连接已释放，后续读写使用新连接可正常进行。
    assert repository_database.db_path.is_file()
    assert numbering_service.preview("graduation").number == "0002"
