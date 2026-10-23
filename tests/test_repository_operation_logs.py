"""OperationLogRepository 测试：时间/类型筛选与稳定排序。"""

from __future__ import annotations

import pytest

from app.data.db import Database
from app.repositories import OperationLogRepository

EARLY = "2026-10-20T08:00:00.000Z"
MIDDLE = "2026-10-21T08:00:00.000Z"
LATE = "2026-10-22T08:00:00.000Z"


@pytest.fixture
def repo(repository_database: Database) -> OperationLogRepository:
    return OperationLogRepository(repository_database)


def test_create_and_get(repo: OperationLogRepository) -> None:
    log_id = repo.create(
        "export",
        "certificate_record",
        None,
        "导出测试",
        "success",
        created_at=EARLY,
    )

    row = repo.get(log_id)
    assert row["id"] == log_id
    assert row["operation_type"] == "export"
    assert row["target_type"] == "certificate_record"
    assert row["target_id"] is None
    assert row["summary"] == "导出测试"
    assert row["result"] == "success"
    assert row["error_message"] is None
    assert row["created_at"] == EARLY
    assert repo.get(999999) is None


def test_default_created_at_is_utc(repo: OperationLogRepository) -> None:
    log_id = repo.create("import", "import_batch", "1", "默认时间", "running")

    assert repo.get(log_id)["created_at"].endswith("Z")


def test_time_range_and_type_filters(repo: OperationLogRepository) -> None:
    repo.create("import", "import_batch", "1", "早", "success", created_at=EARLY)
    repo.create("export", "certificate_record", "1", "中", "success", created_at=MIDDLE)
    repo.create("import", "import_batch", "2", "晚", "success", created_at=LATE)

    # 时间范围包含上下界。
    rows = repo.list_logs(start_time=MIDDLE, end_time=LATE)
    assert [row["summary"] for row in rows] == ["晚", "中"]
    assert repo.count_logs(start_time=MIDDLE, end_time=LATE) == 2

    import_rows = repo.list_logs(operation_type="import")
    assert {row["summary"] for row in import_rows} == {"早", "晚"}
    assert repo.count_logs(operation_type="import") == 2

    assert repo.count_logs() == 3
    assert repo.list_logs(start_time=LATE, end_time=EARLY) == []


def test_order_is_created_desc_then_id_desc(repo: OperationLogRepository) -> None:
    first = repo.create("import", "import_batch", "1", "同时间一", "success", created_at=MIDDLE)
    second = repo.create("import", "import_batch", "2", "同时间二", "success", created_at=MIDDLE)
    earliest = repo.create("import", "import_batch", "3", "更早", "success", created_at=EARLY)

    rows = repo.list_logs()
    assert [row["id"] for row in rows] == [second, first, earliest]
    assert rows[-1]["summary"] == "更早"


def test_special_characters_and_arbitrary_result_text(repo: OperationLogRepository) -> None:
    summary = "x'); DROP TABLE operation_logs;--"
    log_id = repo.create("任意操作类型", "任意目标", "a'); DELETE FROM operation_logs;--", summary, "待业务确认")

    row = repo.get(log_id)
    assert row["summary"] == summary
    assert row["result"] == "待业务确认"
    assert repo.count_logs() == 1


def test_pagination_and_validation(repo: OperationLogRepository) -> None:
    for index in range(3):
        repo.create("import", "import_batch", str(index), f"日志{index}", "success", created_at=MIDDLE)

    page = repo.list_logs(limit=1, offset=1)
    assert len(page) == 1

    with pytest.raises(ValueError):
        repo.list_logs(limit=0)
    with pytest.raises(ValueError):
        repo.list_logs(offset=-1)
