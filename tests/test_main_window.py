"""主窗口空壳测试：离屏创建并显示窗口，使用临时 QSettings 避免污染注册表。"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow, WINDOW_TITLE


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    return app


def test_main_window_can_show(qapp, tmp_path) -> None:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings=settings)

    window.show()
    qapp.processEvents()

    assert window.windowTitle() == WINDOW_TITLE
    assert window.centralWidget() is not None
    assert window.isVisible()

    window.close()
    settings.sync()
    assert (tmp_path / "settings.ini").is_file()
    assert settings.value("ui/main_window/geometry") is not None


def test_template_designer_menu_is_wired(
    qapp: QApplication,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PySide6.QtGui import QAction

    from app.bootstrap import bootstrap
    from app.config.paths import AppPaths
    from app.ui import main_window as main_window_module

    paths = AppPaths.for_program(tmp_path)
    context = bootstrap(paths)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings=settings, context=context)
    window.show()
    qapp.processEvents()

    assert window.template_service is not None

    captured: list[object] = []

    class _FakeDesigner:
        def __init__(self, service: object, parent: object | None = None) -> None:
            captured.append(service)

        def exec(self) -> int:
            return 0

    monkeypatch.setattr(main_window_module, "TemplateDesignerDialog", _FakeDesigner)

    actions = {action.text(): action for action in window.findChildren(QAction)}
    assert "模板设计器(&D)" in actions
    actions["模板设计器(&D)"].trigger()

    assert captured == [window.template_service]

    window.close()
    settings.sync()


def test_print_menu_is_wired(qapp: QApplication, tmp_path) -> None:
    from PySide6.QtGui import QAction

    from app.bootstrap import bootstrap
    from app.config.paths import AppPaths

    paths = AppPaths.for_program(tmp_path)
    context = bootstrap(paths)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings=settings, context=context)
    window.show()
    qapp.processEvents()

    assert window.print_service is not None
    assert window.student_page is not None
    assert window.student_page.print_service is window.print_service
    assert window.student_page.print_button.isEnabled() is True

    actions = {action.text(): action for action in window.findChildren(QAction)}
    for label in (
        "打印选中记录(&S)",
        "打印当前筛选结果(&F)",
        "打印勾选记录(&C)",
    ):
        assert label in actions

    window.close()
    settings.sync()
