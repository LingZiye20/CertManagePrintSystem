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


def _build_window(tmp_path, qapp: QApplication):
    from PySide6.QtCore import QSettings

    from app.bootstrap import bootstrap
    from app.config.paths import AppPaths
    from app.ui.main_window import MainWindow

    paths = AppPaths.for_program(tmp_path)
    context = bootstrap(paths)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings=settings, context=context)
    window.show()
    qapp.processEvents()
    return window, context


def test_system_menu_is_wired(qapp: QApplication, tmp_path) -> None:
    from PySide6.QtGui import QAction

    window, _context = _build_window(tmp_path, qapp)

    assert window.settings_service is not None
    assert window.audit_service is not None
    assert window.reset_service is not None

    actions = {action.text() for action in window.findChildren(QAction)}
    assert "系统设置(&S)…" in actions
    assert "操作审计(&A)…" in actions
    assert "系统重置(&R)…" in actions
    window.close()


def test_window_applies_stored_settings(qapp: QApplication, tmp_path) -> None:
    from app.services import DEFAULT_CERTIFICATE_TYPE_KEY, PAGE_SIZE_KEY, SettingsService

    window, context = _build_window(tmp_path, qapp)
    window.close()

    SettingsService(context.database).update(
        {PAGE_SIZE_KEY: 50, DEFAULT_CERTIFICATE_TYPE_KEY: "degree"}
    )

    from PySide6.QtCore import QSettings

    from app.ui.main_window import MainWindow

    settings = QSettings(str(tmp_path / "settings2.ini"), QSettings.Format.IniFormat)
    reloaded = MainWindow(settings=settings, context=context)
    reloaded.show()
    qapp.processEvents()

    assert reloaded.student_page is not None
    assert reloaded.student_page.page_size_combo.currentData() == 50
    assert reloaded.student_page.default_certificate_type == "degree"
    assert reloaded.default_certificate_type == "degree"
    reloaded.close()


def test_reset_completed_refreshes_student_page(
    qapp: QApplication, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, _context = _build_window(tmp_path, qapp)
    assert window.student_page is not None
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        window.student_page, "reload", lambda **kwargs: calls.append(kwargs)
    )

    class _Result:
        message = "系统重置完成：测试"

    window.handle_reset_completed(_Result())

    assert calls == [{"reset_page": True}]
    assert "系统重置完成" in window.statusBar().currentMessage()
    window.close()


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


def test_verify_menu_is_wired(
    qapp: QApplication, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtGui import QAction

    from app.bootstrap import bootstrap
    from app.config.paths import AppPaths
    from app.ui import student_page as student_page_module

    paths = AppPaths.for_program(tmp_path)
    context = bootstrap(paths)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings=settings, context=context)
    window.show()
    qapp.processEvents()

    assert window.verification_service is not None
    assert window.student_page is not None
    assert window.student_page.verification_service is window.verification_service

    opened: list[str] = []

    class _FakeDialog:
        def __init__(
            self,
            service,
            filters,
            parent=None,
            *,
            order_by,
            descending,
            initial_type="graduation",
        ):
            opened.append(order_by)

        def exec(self) -> int:
            return 0

    monkeypatch.setattr(student_page_module, "VerificationDialog", _FakeDialog)

    actions = {action.text(): action for action in window.findChildren(QAction)}
    assert "核验证书(&V)…" in actions
    actions["核验证书(&V)…"].trigger()

    assert opened == ["student_no"]

    window.close()
    settings.sync()


def test_menu_bar_order_starts_with_system_and_has_no_file_menu(
    qapp: QApplication, tmp_path
) -> None:
    from PySide6.QtGui import QKeySequence
    from PySide6.QtWidgets import QMenu

    window, _context = _build_window(tmp_path, qapp)

    menu_titles = [
        action.text()
        for action in window.menuBar().actions()
        if not action.isSeparator()
    ]
    assert menu_titles == [
        "系统管理(&S)",
        "数据管理(&D)",
        "照片管理(&Z)",
        "编号管理(&N)",
        "模板管理(&T)",
        "打印管理(&P)",
        "核验管理(&V)",
        "帮助(&H)",
    ]
    assert "文件(&F)" not in menu_titles

    system_menu = next(
        menu for menu in window.findChildren(QMenu) if menu.title() == "系统管理(&S)"
    )
    system_actions = system_menu.actions()
    assert system_actions[-1].text() == "退出(&Q)"
    assert system_actions[-1].shortcut() == QKeySequence(QKeySequence.StandardKey.Quit)
    assert system_actions[-2].isSeparator()
    assert "系统重置(&R)…" in [action.text() for action in system_actions]

    window.close()


def test_data_menu_order_has_template_import_export_refresh(
    qapp: QApplication, tmp_path
) -> None:
    from PySide6.QtGui import QKeySequence
    from PySide6.QtWidgets import QMenu

    window, _context = _build_window(tmp_path, qapp)

    data_menu = next(
        menu for menu in window.findChildren(QMenu) if menu.title() == "数据管理(&D)"
    )
    action_texts = [
        action.text()
        for action in data_menu.actions()
        if not action.isSeparator()
    ]
    assert action_texts == [
        "下载导入模板(&T)…",
        "导入 Excel(&I)",
        "导出数据(&E)…",
        "刷新(&R)",
    ]

    # 编辑/删除菜单项已从数据管理菜单移除
    assert "编辑记录(&E)" not in action_texts
    assert "删除记录(&D)" not in action_texts

    # 刷新仍保留 F5 快捷键
    refresh = next(a for a in data_menu.actions() if a.text() == "刷新(&R)")
    assert refresh.shortcut() == QKeySequence("F5")

    # 工具栏编辑/删除按钮及其功能不受影响
    page = window.student_page
    assert page is not None
    assert hasattr(page, "edit_button")
    assert hasattr(page, "delete_button")
    assert hasattr(page, "edit_selected")
    assert hasattr(page, "delete_selected")

    window.close()


def test_photo_menu_is_absent_without_photo_service(qapp: QApplication, tmp_path) -> None:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings=settings)
    window.show()
    qapp.processEvents()

    menu_titles = [
        action.text()
        for action in window.menuBar().actions()
        if not action.isSeparator()
    ]
    assert window.photo_service is None
    assert "照片管理(&Z)" not in menu_titles
    assert menu_titles[-1] == "帮助(&H)"

    window.close()
