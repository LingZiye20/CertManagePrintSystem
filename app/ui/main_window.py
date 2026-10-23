"""应用主窗口。

阶段 4 起在启动上下文可用时接入学生数据管理页面；无上下文时保留占位界面，
便于界面单元测试与后续阶段复用。
"""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import QLabel, QMainWindow, QMessageBox, QWidget

from app import __version__
from app.bootstrap import AppContext
from app.services import (
    ExcelImportService,
    NumberingService,
    PhotoService,
    StudentService,
    TemplateService,
)
from app.ui.numbering_rule_dialog import NumberingRuleDialog
from app.ui.student_page import StudentPage
from app.ui.template_designer_dialog import TemplateDesignerDialog

WINDOW_TITLE = "证书管理与打印系统"
DEFAULT_WIDTH = 1024
DEFAULT_HEIGHT = 700
GEOMETRY_KEY = "ui/main_window/geometry"


class MainWindow(QMainWindow):
    """应用主窗口。"""

    def __init__(
        self,
        settings: QSettings | None = None,
        parent: QWidget | None = None,
        *,
        context: AppContext | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings if settings is not None else QSettings()
        self._context = context
        self.student_page: StudentPage | None = None
        self.numbering_service: NumberingService | None = None
        self.photo_service: PhotoService | None = None
        self.import_service: ExcelImportService | None = None
        self.template_service: TemplateService | None = None
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(DEFAULT_WIDTH, DEFAULT_HEIGHT)
        self._build_central_widget()
        self._build_menus()
        self.statusBar().showMessage("就绪")
        self._restore_geometry()

    def _build_central_widget(self) -> None:
        if self._context is not None:
            service = StudentService(self._context.database)
            self.numbering_service = NumberingService(self._context.database)
            self.photo_service = PhotoService(
                self._context.database, self._context.paths
            )
            self.import_service = ExcelImportService(self._context.database)
            self.template_service = TemplateService(
                self._context.database, self._context.paths
            )
            self.student_page = StudentPage(
                service,
                self,
                numbering_service=self.numbering_service,
                photo_service=self.photo_service,
                import_service=self.import_service,
            )
            self.setCentralWidget(self.student_page)
            return

        placeholder = QLabel("项目骨架已启动\n业务功能将在后续阶段实现")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet("color: #666666; font-size: 16px;")
        self.setCentralWidget(placeholder)

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("文件(&F)")
        quit_action = QAction("退出(&Q)", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        if self.student_page is not None:
            self._build_data_menu()
            self._build_numbering_menu()
        if self.template_service is not None:
            self._build_template_menu()

        help_menu = self.menuBar().addMenu("帮助(&H)")
        about_action = QAction("关于(&A)", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _build_data_menu(self) -> None:
        page = self.student_page
        if page is None:  # pragma: no cover - 仅防御
            return
        data_menu = self.menuBar().addMenu("数据管理(&D)")

        refresh_action = QAction("刷新(&R)", self)
        refresh_action.setShortcut(QKeySequence("F5"))
        refresh_action.triggered.connect(lambda: page.reload())
        data_menu.addAction(refresh_action)

        add_action = QAction("新增记录(&A)", self)
        add_action.triggered.connect(page.add_record)
        data_menu.addAction(add_action)

        edit_action = QAction("编辑记录(&E)", self)
        edit_action.triggered.connect(page.edit_selected)
        data_menu.addAction(edit_action)

        delete_action = QAction("删除记录(&D)", self)
        delete_action.triggered.connect(page.delete_selected)
        data_menu.addAction(delete_action)

    def _build_numbering_menu(self) -> None:
        page = self.student_page
        service = self.numbering_service
        if page is None or service is None:  # pragma: no cover - 仅防御
            return
        numbering_menu = self.menuBar().addMenu("编号管理(&N)")

        rule_action = QAction("编号规则设置(&R)", self)
        rule_action.triggered.connect(lambda: NumberingRuleDialog(service, self).exec())
        numbering_menu.addAction(rule_action)

        generate_action = QAction("生成证书编号(&G)", self)
        generate_action.triggered.connect(page.generate_number_for_selected)
        numbering_menu.addAction(generate_action)

    def _build_template_menu(self) -> None:
        service = self.template_service
        if service is None:  # pragma: no cover - 仅防御
            return
        template_menu = self.menuBar().addMenu("模板管理(&T)")

        designer_action = QAction("模板设计器(&D)", self)
        designer_action.triggered.connect(
            lambda: TemplateDesignerDialog(service, self).exec()
        )
        template_menu.addAction(designer_action)

    def _show_about(self) -> None:
        QMessageBox.about(self, "关于", f"{WINDOW_TITLE}\n版本 {__version__}")

    def _restore_geometry(self) -> None:
        geometry = self._settings.value(GEOMETRY_KEY)
        if geometry is not None:
            self.restoreGeometry(geometry)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._settings.setValue(GEOMETRY_KEY, self.saveGeometry())
        super().closeEvent(event)
