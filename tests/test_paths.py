"""路径管理测试：校验 A.2 目录布局、资源回退与失败路径；全部使用临时目录。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.bootstrap import bootstrap
from app.config.paths import AppPaths, application_dir, default_program_root


def test_default_root_is_application_dir() -> None:
    assert default_program_root() == application_dir()
    assert default_program_root().is_dir()


def test_directories_follow_temporary_program_root(tmp_path) -> None:
    root = tmp_path / "program"
    paths = AppPaths.for_program(root)

    assert paths.resource_dir == root / "resources"
    assert paths.db_dir == root / "data"
    assert paths.db_file == root / "data" / "certificates.db"
    assert paths.photos_dir == root / "photos"
    assert paths.logs_dir == root / "logs"
    assert paths.log_file == root / "logs" / "app.log"
    assert paths.config_dir == root / "config"

    for child in (
        paths.resource_dir,
        paths.db_dir,
        paths.photos_dir,
        paths.logs_dir,
        paths.config_dir,
    ):
        assert child.is_relative_to(root)


def _simulate_frozen(monkeypatch, tmp_path: Path) -> Path:
    """模拟 PyInstaller 运行环境：设置 ``sys.frozen`` 与 ``_MEIPASS``。"""
    meipass = tmp_path / "meipass"
    (meipass / "resources").mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    return meipass


def test_resource_dir_falls_back_to_meipass(monkeypatch, tmp_path) -> None:
    meipass = _simulate_frozen(monkeypatch, tmp_path)
    paths = AppPaths.for_program(tmp_path / "program")

    assert paths.resource_dir == meipass / "resources"


def test_program_resources_take_precedence_over_meipass(monkeypatch, tmp_path) -> None:
    root = tmp_path / "program"
    (root / "resources").mkdir(parents=True)
    meipass = _simulate_frozen(monkeypatch, tmp_path)
    paths = AppPaths.for_program(root)

    assert paths.resource_dir == root / "resources"
    assert paths.resource_dir != meipass / "resources"


def test_ensure_user_dirs_is_idempotent(tmp_path) -> None:
    paths = AppPaths.for_program(tmp_path)

    paths.ensure_user_dirs()
    paths.ensure_user_dirs()

    for directory in (
        paths.program_root,
        paths.db_dir,
        paths.photos_dir,
        paths.logs_dir,
        paths.config_dir,
    ):
        assert directory.is_dir()

    # 只创建目录：数据库文件由 bootstrap 调用数据库初始化时创建
    assert not paths.db_file.exists()


def test_ensure_dirs_raises_when_program_root_cannot_be_created(tmp_path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    paths = AppPaths.for_program(blocker / "program")

    with pytest.raises(OSError):
        paths.ensure_user_dirs()

    assert not (blocker / "program").exists()
    # 不得静默回退：临时根下除阻塞文件外不应出现任何目录
    assert sorted(item.name for item in tmp_path.iterdir()) == ["blocker"]


def test_bootstrap_propagates_oserror_without_fallback(tmp_path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    paths = AppPaths.for_program(blocker / "program")

    with pytest.raises(OSError):
        bootstrap(paths)

    assert not (blocker / "program").exists()
    assert sorted(item.name for item in tmp_path.iterdir()) == ["blocker"]
