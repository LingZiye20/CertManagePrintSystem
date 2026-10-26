"""NumberingService 测试：手动流水号预览、批量事务、冲突回滚、审计与并发。"""

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
    return service.records.create(
        {
            "student_no": student_no,
            "name": "测试",
            "education_level": "本科",
            **values,
        }
    )


def _enable_levels(
    service: NumberingService, levels: tuple[str, ...] = ("本科",)
) -> None:
    service.update_rule("graduation", {"education_levels": list(levels)})


# ---------------------------------------------------------------- 预览


def test_preview_is_read_only_with_manual_sequence(
    numbering_service: NumberingService,
) -> None:
    numbering_service.update_rule(
        "graduation", {"fixed_text_1": "GX", "sequence_width": 4}
    )

    preview = numbering_service.preview("graduation", 100)

    assert preview.number == "GX0100"
    assert preview.sequence_value == 100
    assert preview.sequence_width == 4
    # 预览不写数据库、不占用编号。
    assert numbering_service.records.count_records() == 0
    assert (
        numbering_service.logs.list_logs(
            operation_type="certificate_number_generate"
        )
        == []
    )
    assert numbering_service.preview("graduation", 100).number == "GX0100"


def test_preview_keeps_sequence_beyond_width(
    numbering_service: NumberingService,
) -> None:
    preview = numbering_service.preview("graduation", 123456)

    assert preview.number == "123456"


def test_preview_missing_rule_is_reported(
    numbering_service: NumberingService,
) -> None:
    with numbering_service.database.transaction() as connection:
        connection.execute(
            "DELETE FROM numbering_rules WHERE rule_key = ?", ("other",)
        )

    with pytest.raises(ValidationError):
        numbering_service.preview("other", 1)

    # 其他类型不受影响。
    assert numbering_service.preview("graduation", 1).number == "0001"


@pytest.mark.parametrize("start_sequence", [0, -1, True, "1", None, 1.0])
def test_preview_rejects_invalid_start_sequence(
    numbering_service: NumberingService, start_sequence: Any
) -> None:
    with pytest.raises(ValidationError, match="请输入起始流水号"):
        numbering_service.preview("graduation", start_sequence)


def test_invalid_type_rejected(numbering_service: NumberingService) -> None:
    for rule_key in ("unknown", "", None, 1):
        with pytest.raises(ValidationError):
            numbering_service.preview(rule_key, 1)  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            numbering_service.generate_numbers([1], rule_key, 1)  # type: ignore[arg-type]


# ---------------------------------------------------------------- 规则更新


def test_update_rule_trims_fragments_and_logs_success(
    numbering_service: NumberingService,
) -> None:
    numbering_service.update_rule(
        "graduation",
        {
            "fixed_text_1": "  GX 学 ",
            "fixed_text_2": "   ",
            "fixed_text_3": "",
            "fixed_text_4": "2026",
            "sequence_width": 6,
            "education_levels": ["本科"],
        },
    )

    view = numbering_service.get_rule("graduation")
    assert view.fixed_texts == ("GX 学", "", "", "2026")
    assert view.sequence_width == 6
    assert view.education_levels == ("本科",)

    logs = numbering_service.logs.list_logs(operation_type="numbering_rule_update")
    assert len(logs) == 1
    assert logs[0]["result"] == "success"
    assert logs[0]["target_id"] == "graduation"
    # 审计摘要使用证书类型中文名。
    assert "毕结业证书" in logs[0]["summary"]


@pytest.mark.parametrize("removed", [{"rule_name": "新名称"}, {"enabled": False}])
def test_update_rule_rejects_removed_fields(
    numbering_service: NumberingService, removed: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError, match="不支持的规则字段"):
        numbering_service.update_rule("graduation", removed)

    assert numbering_service.logs.count_logs() == 1  # 仅失败审计


def test_last_type_defaults_and_remembers(
    numbering_service: NumberingService,
) -> None:
    assert numbering_service.get_last_type() == "graduation"

    numbering_service.remember_last_type("degree")
    assert numbering_service.get_last_type() == "degree"

    numbering_service.remember_last_type("other")
    assert numbering_service.get_last_type() == "other"


def test_last_type_falls_back_for_invalid_value(
    numbering_service: NumberingService,
) -> None:
    numbering_service.settings.set_value("numbering.last_rule_key", "invalid")

    assert numbering_service.get_last_type() == "graduation"


def test_last_type_falls_back_when_read_fails(
    numbering_service: NumberingService, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_get(*args: Any, **kwargs: Any) -> Any:
        raise sqlite3.Error("模拟读取失败")

    monkeypatch.setattr(numbering_service.settings, "get", broken_get)

    assert numbering_service.get_last_type() == "graduation"


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
    ],
)
def test_update_rule_rejects_invalid_changes(
    numbering_service: NumberingService, changes: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError):
        numbering_service.update_rule("graduation", changes)

    view = numbering_service.get_rule("graduation")
    assert view.fixed_texts == ("", "", "", "")
    assert view.sequence_width == 4


def test_update_rule_accepts_width_boundaries(
    numbering_service: NumberingService,
) -> None:
    numbering_service.update_rule("graduation", {"sequence_width": 1})
    assert numbering_service.preview("graduation", 1).number == "1"

    numbering_service.update_rule("graduation", {"sequence_width": 20})
    assert numbering_service.preview("graduation", 1).number == "0" * 19 + "1"


@pytest.mark.parametrize("width", [21, 999_999_999])
def test_stored_invalid_width_is_reported_without_writing(
    numbering_service: NumberingService, width: int
) -> None:
    record_id = _create_record(numbering_service, "S-1")
    _enable_levels(numbering_service)
    with numbering_service.database.transaction() as connection:
        connection.execute(
            "UPDATE numbering_rules SET sequence_width = ? WHERE rule_key = ?",
            (width, "graduation"),
        )

    # 规则仍可读取，便于界面修正异常配置。
    assert numbering_service.get_rule("graduation").sequence_width == width

    with pytest.raises(ValidationError, match="1–20"):
        numbering_service.preview("graduation", 1)
    with pytest.raises(ValidationError, match="1–20"):
        numbering_service.generate_numbers([record_id], "graduation", 1)

    assert numbering_service.records.get(record_id)["certificate_no"] is None


# ---------------------------------------------------------------- 批量编号


def test_batch_generation_starts_from_manual_sequence(
    numbering_service: NumberingService,
) -> None:
    numbering_service.update_rule(
        "graduation", {"fixed_text_1": "GX", "education_levels": ["本科"]}
    )
    record_ids = [
        _create_record(numbering_service, student_no)
        for student_no in ("S-3", "S-1", "S-2")
    ]

    result = numbering_service.generate_numbers(record_ids, "graduation", 100)

    assert result.numbered_count == 3
    assert result.skipped_count == 0
    assert result.first_number == "GX0100"
    assert result.last_number == "GX0102"
    numbers = [
        numbering_service.records.get(record_id)["certificate_no"]
        for record_id in record_ids
    ]
    assert numbers == ["GX0100", "GX0101", "GX0102"]

    logs = numbering_service.logs.list_logs(
        operation_type="certificate_number_generate"
    )
    assert len(logs) == 1
    assert logs[0]["result"] == "success"
    assert "起始流水号：100" in logs[0]["summary"]
    assert "编号 3 条" in logs[0]["summary"]
    assert "跳过 0 条" in logs[0]["summary"]
    assert "GX0100–GX0102" in logs[0]["summary"]


def test_batch_generation_skips_existing_numbers_without_gaps(
    numbering_service: NumberingService,
) -> None:
    numbering_service.update_rule("graduation", {"education_levels": ["本科"]})
    record_ids = [
        _create_record(numbering_service, "S-1", certificate_no="OLD-001"),
        _create_record(numbering_service, "S-2"),
        _create_record(numbering_service, "S-3"),
    ]

    result = numbering_service.generate_numbers(record_ids, "graduation", 100)

    assert result.numbered_count == 2
    assert result.skipped_count == 1
    rows = [numbering_service.records.get(record_id) for record_id in record_ids]
    # 跳过已有编号的记录后序号仍然连续，不留空档。
    assert [row["certificate_no"] for row in rows] == [
        "OLD-001",
        "0100",
        "0101",
    ]


def test_batch_generation_conflict_rolls_back_whole_batch(
    numbering_service: NumberingService,
) -> None:
    numbering_service.update_rule(
        "graduation", {"fixed_text_1": "GX", "education_levels": ["本科"]}
    )
    _create_record(numbering_service, "S-0", certificate_no="GX0101")
    record_ids = [
        _create_record(numbering_service, "S-1"),
        _create_record(numbering_service, "S-2"),
    ]

    with pytest.raises(NumberConflictError, match="GX0101"):
        numbering_service.generate_numbers(record_ids, "graduation", 100)

    rows = [numbering_service.records.get(record_id) for record_id in record_ids]
    assert [row["certificate_no"] for row in rows] == [None, None]
    assert (
        numbering_service.records.get_by_student_no("S-0")["certificate_no"] == "GX0101"
    )
    assert (
        numbering_service.logs.list_logs(
            operation_type="certificate_number_generate"
        )
        != []
    )


@pytest.mark.parametrize("start_sequence", [0, -1, True, "100", None, 2.5])
def test_batch_generation_rejects_invalid_start_sequence(
    numbering_service: NumberingService, start_sequence: Any
) -> None:
    record_id = _create_record(numbering_service, "S-1")
    _enable_levels(numbering_service)

    with pytest.raises(ValidationError, match="请输入起始流水号"):
        numbering_service.plan_numbers([record_id], "graduation", start_sequence)
    with pytest.raises(ValidationError, match="请输入起始流水号"):
        numbering_service.generate_numbers([record_id], "graduation", start_sequence)

    assert numbering_service.records.get(record_id)["certificate_no"] is None


def test_batch_generation_rejects_empty_list(
    numbering_service: NumberingService,
) -> None:
    with pytest.raises(ValidationError, match="当前筛选结果为空"):
        numbering_service.generate_numbers([], "graduation", 1)

    assert numbering_service.logs.count_logs() == 0


def test_batch_generation_without_pending_records_writes_nothing(
    numbering_service: NumberingService,
) -> None:
    numbering_service.update_rule("graduation", {"education_levels": ["本科"]})
    record_ids = [
        _create_record(numbering_service, "S-1", certificate_no="0001"),
        _create_record(numbering_service, "S-2", certificate_no="0002"),
    ]

    result = numbering_service.generate_numbers(record_ids, "graduation", 100)

    assert result.numbered_count == 0
    assert result.skipped_count == 2
    assert result.first_number is None
    assert result.last_number is None
    assert (
        numbering_service.logs.list_logs(
            operation_type="certificate_number_generate"
        )
        == []
    )


def test_batch_generation_uses_new_manual_start_each_time(
    numbering_service: NumberingService,
) -> None:
    numbering_service.update_rule("graduation", {"education_levels": ["本科"]})
    first_batch = [
        _create_record(numbering_service, "S-1"),
        _create_record(numbering_service, "S-2"),
    ]
    second_batch = [_create_record(numbering_service, "S-3")]

    numbering_service.generate_numbers(first_batch, "graduation", 1)
    result = numbering_service.generate_numbers(second_batch, "graduation", 500)

    assert result.first_number == "0500"
    assert result.last_number == "0500"


def test_batch_generation_accepts_large_start_sequence(
    numbering_service: NumberingService,
) -> None:
    _enable_levels(numbering_service)
    record_id = _create_record(numbering_service, "S-1")

    result = numbering_service.generate_numbers([record_id], "graduation", 123456)

    assert result.first_number == "123456"
    assert (
        numbering_service.records.get(record_id)["certificate_no"] == "123456"
    )


def test_batch_generation_missing_record_rolls_back(
    numbering_service: NumberingService,
) -> None:
    numbering_service.update_rule("graduation", {"education_levels": ["本科"]})
    record_id = _create_record(numbering_service, "S-1")

    with pytest.raises(RecordNotFoundError):
        numbering_service.generate_numbers([record_id, 999], "graduation", 1)

    assert numbering_service.records.get(record_id)["certificate_no"] is None


def test_batch_generation_rejects_invalid_ids(
    numbering_service: NumberingService,
) -> None:
    for record_ids in ([0], [-1], [True], ["1"], [None]):
        with pytest.raises(ValidationError):
            numbering_service.generate_numbers(record_ids, "graduation", 1)  # type: ignore[arg-type]


def test_batch_plan_is_read_only_and_reports_skips(
    numbering_service: NumberingService,
) -> None:
    numbering_service.update_rule(
        "graduation", {"fixed_text_1": "GX", "education_levels": ["本科"]}
    )
    record_ids = [
        _create_record(numbering_service, "S-1", certificate_no="OLD-001"),
        _create_record(numbering_service, "S-2"),
        _create_record(numbering_service, "S-3"),
    ]

    plan = numbering_service.plan_numbers(record_ids, "graduation", 100)

    assert plan.total_count == 3
    assert plan.pending_count == 2
    assert plan.skipped_count == 1
    assert plan.start_sequence == 100
    assert plan.first_number == "GX0100"
    assert plan.last_number == "GX0101"
    # 计划只读：不写记录、不写审计。
    assert numbering_service.records.get(record_ids[1])["certificate_no"] is None
    assert (
        numbering_service.logs.list_logs(
            operation_type="certificate_number_generate"
        )
        == []
    )


def test_batch_generation_audit_failure_rolls_back(
    numbering_service: NumberingService,
) -> None:
    numbering_service.update_rule("graduation", {"education_levels": ["本科"]})
    record_ids = [
        _create_record(numbering_service, "S-1"),
        _create_record(numbering_service, "S-2"),
    ]

    def broken_create(*args: Any, **kwargs: Any) -> int:
        raise sqlite3.Error("模拟审计失败")

    numbering_service.logs.create = broken_create  # type: ignore[method-assign]

    with pytest.raises(AuditWriteError):
        numbering_service.generate_numbers(record_ids, "graduation", 1)

    rows = [numbering_service.records.get(record_id) for record_id in record_ids]
    assert [row["certificate_no"] for row in rows] == [None, None]


def test_batch_generation_only_numbers_matching_education_levels(
    numbering_service: NumberingService,
) -> None:
    numbering_service.update_rule(
        "graduation", {"fixed_text_1": "GX", "education_levels": ["本科"]}
    )
    bachelor = _create_record(numbering_service, "S-1", education_level="本科")
    junior = _create_record(numbering_service, "S-2", education_level="专科")
    empty_level = _create_record(numbering_service, "S-3", education_level=None)
    record_ids = [bachelor, junior, empty_level]

    plan = numbering_service.plan_numbers(record_ids, "graduation", 100)
    assert plan.total_count == 3
    assert plan.matched_count == 1
    assert plan.level_skipped_count == 2
    assert plan.pending_count == 1

    result = numbering_service.generate_numbers(record_ids, "graduation", 100)

    assert result.numbered_count == 1
    assert result.skipped_count == 0
    assert result.level_skipped_count == 2
    assert numbering_service.records.get(bachelor)["certificate_no"] == "GX0100"
    assert numbering_service.records.get(junior)["certificate_no"] is None
    assert numbering_service.records.get(empty_level)["certificate_no"] is None


def test_batch_generation_requires_selected_education_levels(
    numbering_service: NumberingService,
) -> None:
    record_id = _create_record(numbering_service, "S-1")

    with pytest.raises(ValidationError, match="请先选择编号应用的层次"):
        numbering_service.plan_numbers([record_id], "graduation", 1)
    with pytest.raises(ValidationError, match="请先选择编号应用的层次"):
        numbering_service.generate_numbers([record_id], "graduation", 1)

    assert numbering_service.records.get(record_id)["certificate_no"] is None


def test_batch_generation_with_no_matching_level_writes_nothing(
    numbering_service: NumberingService,
) -> None:
    numbering_service.update_rule("graduation", {"education_levels": ["本科"]})
    record_id = _create_record(numbering_service, "S-1", education_level="专科")

    result = numbering_service.generate_numbers([record_id], "graduation", 100)

    assert result.numbered_count == 0
    assert result.level_skipped_count == 1
    assert numbering_service.records.get(record_id)["certificate_no"] is None
    assert (
        numbering_service.logs.list_logs(
            operation_type="certificate_number_generate"
        )
        == []
    )


# ---------------------------------------------------------------- 清空编号


def test_clear_numbers_clears_only_selected_field(
    numbering_service: NumberingService,
) -> None:
    _create_record(
        numbering_service,
        "S-1",
        certificate_no="BJ-001",
        degree_certificate_no="XW-001",
    )
    _create_record(numbering_service, "S-2", certificate_no="BJ-002")

    result = numbering_service.clear_numbers("graduation")

    assert result.cleared_count == 2
    rows = {
        student_no: numbering_service.records.get_by_student_no(student_no)
        for student_no in ("S-1", "S-2")
    }
    assert rows["S-1"]["certificate_no"] is None
    assert rows["S-2"]["certificate_no"] is None
    # 其他两类编号不受影响。
    assert rows["S-1"]["degree_certificate_no"] == "XW-001"

    logs = numbering_service.logs.list_logs(
        operation_type="certificate_number_clear"
    )
    assert len(logs) == 1
    assert logs[0]["result"] == "success"
    assert "清空 2 条" in logs[0]["summary"]
    # 清空编号不再涉及任何流水表述。
    assert "流水" not in logs[0]["summary"]


def test_clear_numbers_rejects_when_no_numbers_exist(
    numbering_service: NumberingService,
) -> None:
    _create_record(numbering_service, "S-1")

    with pytest.raises(ValidationError, match="没有可清空的编号"):
        numbering_service.clear_numbers("graduation")

    assert numbering_service.logs.count_logs() == 0


def test_clear_numbers_does_not_touch_any_sequence_table(
    numbering_service: NumberingService,
) -> None:
    _create_record(numbering_service, "S-1", certificate_no="BJ-001")

    numbering_service.clear_numbers("graduation")

    with numbering_service.database.connect() as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "number_sequences" not in tables


# ---------------------------------------------------------------- 层次候选


def test_education_level_candidates_merge_saved_values(
    numbering_service: NumberingService,
) -> None:
    _create_record(numbering_service, "S-1", education_level="本科")
    _create_record(numbering_service, "S-2", education_level="专科")
    _create_record(numbering_service, "S-3", education_level="本科")
    _create_record(numbering_service, "S-4", education_level=None)
    numbering_service.update_rule(
        "graduation", {"education_levels": ["本科", "硕士研究生"]}
    )

    assert numbering_service.list_education_levels("graduation") == (
        "专科",
        "本科",
        "硕士研究生",
    )


def test_update_rule_normalizes_education_levels(
    numbering_service: NumberingService,
) -> None:
    numbering_service.update_rule(
        "graduation",
        {"education_levels": [" 本科 ", "", "本科", "专科"]},
    )

    view = numbering_service.get_rule("graduation")
    assert view.education_levels == ("本科", "专科")
    assert (
        numbering_service.rules.get("graduation")["education_levels"]
        == '["本科","专科"]'
    )


@pytest.mark.parametrize("bad", ["本科", 1, None, ["本科", 1], [None]])
def test_update_rule_rejects_invalid_education_levels(
    numbering_service: NumberingService, bad: Any
) -> None:
    with pytest.raises(ValidationError):
        numbering_service.update_rule("graduation", {"education_levels": bad})

    assert numbering_service.get_rule("graduation").education_levels == ()


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
    _enable_levels(numbering_service)
    record_ids = [
        _create_record(numbering_service, f"S-{index}") for index in range(4)
    ]

    results = _run_concurrently(
        [
            (
                lambda record_id=record_id, start=index + 1: (
                    numbering_service.generate_numbers(
                        [record_id], "graduation", start
                    )
                )
            )
            for index, record_id in enumerate(record_ids)
        ]
    )

    assert all(status == "ok" for status, _ in results), results
    numbers = sorted(
        numbering_service.records.get(record_id)["certificate_no"]
        for record_id in record_ids
    )
    assert numbers == ["0001", "0002", "0003", "0004"]


def test_concurrent_generation_for_same_record_writes_once(
    numbering_service: NumberingService,
) -> None:
    _enable_levels(numbering_service)
    record_id = _create_record(numbering_service, "S-1")

    results = _run_concurrently(
        [
            lambda: numbering_service.generate_numbers([record_id], "graduation", 1),
            lambda: numbering_service.generate_numbers([record_id], "graduation", 100),
        ]
    )

    statuses = [status for status, _ in results]
    # 写事务串行执行：一次真正写入，另一次看到已有编号后跳过。
    assert statuses == ["ok", "ok"], results
    numbered_counts = sorted(
        value.numbered_count for status, value in results if status == "ok"
    )
    assert numbered_counts == [0, 1]

    number = numbering_service.records.get(record_id)["certificate_no"]
    assert number in {"0001", "0100"}
    assert (
        len(
            numbering_service.logs.list_logs(
                operation_type="certificate_number_generate"
            )
        )
        == 1
    )


def test_service_uses_repository_connections(
    numbering_service: NumberingService, repository_database: Database
) -> None:
    _enable_levels(numbering_service)
    record_id = _create_record(numbering_service, "S-1")

    numbering_service.generate_numbers([record_id], "graduation", 1)

    # 生成结束后连接已释放，后续读写使用新连接可正常进行。
    assert repository_database.db_path.is_file()
    assert numbering_service.preview("graduation", 2).number == "0002"
