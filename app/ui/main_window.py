"""应用主窗口。

阶段 4 起在启动上下文可用时接入学生数据管理页面；无上下文时保留占位界面，
便于界面单元测试与后续阶段复用。
"""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QKeySequence
from PySide6.QtWidgets import QLabel, QMainWindow, QMessageBox, QWidget

from app import __version__
from app.bootstrap import AppContext
from app.services import (
    DEFAULT_CERTIFICATE_TYPE_KEY,
    PAGE_SIZE_KEY,
    AuditService,
    ExcelImportService,
    NumberingService,
    PhotoImportService,
    PhotoService,
    PhotoStorageError,
    PrintService,
    ResetService,
    SettingsService,
    StudentService,
    TemplateService,
    VerificationService,
)
from app.ui.audit_log_dialog import AuditLogDialog
from app.ui.numbering_rule_dialog import NumberingRuleDialog
from app.ui.photo_import_dialog import PhotoImportDialog
from app.ui.settings_dialog import SettingsDialog
from app.ui.student_page import StudentPage
from app.ui.system_reset_dialog import SystemResetDialog
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
        self.photo_import_service: PhotoImportService | None = None
        self.import_service: ExcelImportService | None = None
        self.template_service: TemplateService | None = None
        self.print_service: PrintService | None = None
        self.verification_service: VerificationService | None = None
        self.settings_service: SettingsService | None = None
        self.audit_service: AuditService | None = None
        self.reset_service: ResetService | None = None
        self.default_certificate_type = "graduation"
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
            self.photo_import_service = PhotoImportService(self.photo_service)
            self.import_service = ExcelImportService(self._context.database)
            self.template_service = TemplateService(
                self._context.database, self._context.paths
            )
            self.print_service = PrintService(
                self._context.database,
                self.template_service,
                self._context.paths,
            )
            self.verification_service = VerificationService(self._context.database)
            self.settings_service = SettingsService(self._context.database)
            snapshot = self.settings_service.load()
            page_size = snapshot.values.get(PAGE_SIZE_KEY, 20)
            self.default_certificate_type = snapshot.values.get(
                DEFAULT_CERTIFICATE_TYPE_KEY, "graduation"
            )
            self.audit_service = AuditService(self._context.database)
            self.reset_service = ResetService(
                self._context.database,
                self._context.paths,
                template_service=self.template_service,
            )
            self.student_page = StudentPage(
                service,
                self,
                numbering_service=self.numbering_service,
                photo_service=self.photo_service,
                import_service=self.import_service,
                print_service=self.print_service,
                verification_service=self.verification_service,
                initial_page_size=page_size,
                default_certificate_type=self.default_certificate_type,
            )
            self.setCentralWidget(self.student_page)
            return

        placeholder = QLabel("项目骨架已启动\n业务功能将在后续阶段实现")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet("color: #666666; font-size: 16px;")
        self.setCentralWidget(placeholder)

    def _build_menus(self) -> None:
        if self.settings_service is not None:
            self._build_system_menu()
        if self.student_page is not None:
            self._build_data_menu()
        if self.photo_service is not None:
            self._build_photo_menu()
        if self.student_page is not None:
            self._build_numbering_menu()
        if self.template_service is not None:
            self._build_template_menu()
        if self.print_service is not None:
            self._build_print_menu()
        if self.verification_service is not None:
            self._build_verify_menu()

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

        import_action = QAction("导入 Excel(&I)", self)
        import_action.triggered.connect(page.import_excel)
        data_menu.addAction(import_action)
        page.bind_import_action(import_action)

        edit_action = QAction("编辑记录(&E)", self)
        edit_action.triggered.connect(page.edit_selected)
        data_menu.addAction(edit_action)

        delete_action = QAction("删除记录(&D)", self)
        delete_action.triggered.connect(page.delete_selected)
        data_menu.addAction(delete_action)

    def _build_photo_menu(self) -> None:
        service = self.photo_service
        if service is None:  # pragma: no cover - 仅防御
            return
        photo_menu = self.menuBar().addMenu("照片管理(&Z)")

        import_action = QAction("导入照片(&I)…", self)
        import_action.triggered.connect(self.open_photo_import)
        photo_menu.addAction(import_action)

        page = self.student_page
        if page is not None:
            set_action = QAction("为选中记录设置照片(&S)…", self)
            set_action.triggered.connect(page.set_photo_for_selected)
            photo_menu.addAction(set_action)

        open_action = QAction("打开照片目录(&O)", self)
        open_action.triggered.connect(self.open_photos_directory)
        photo_menu.addAction(open_action)

    def open_photo_import(self) -> None:
        """打开照片批量导入对话框；导入成功后刷新学生列表。"""
        service = self.photo_import_service
        if service is None:
            return
        dialog = PhotoImportDialog(service, self)
        dialog.exec()
        report = dialog.last_report
        if report is not None and report.imported_count > 0:
            page = self.student_page
            if page is not None:
                page.reload()

    def open_photos_directory(self) -> None:
        """在系统文件管理器中打开照片目录；目录缺失时先创建。"""
        service = self.photo_service
        if service is None:
            return
        try:
            photos_dir = service.ensure_photos_dir()
        except PhotoStorageError as exc:
            QMessageBox.warning(self, "打开照片目录", str(exc))
            return

        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(photos_dir))):
            QMessageBox.warning(
                self, "打开照片目录", f"无法打开照片目录：{photos_dir}"
            )

    def _build_numbering_menu(self) -> None:
        page = self.student_page
        service = self.numbering_service
        if page is None or service is None:  # pragma: no cover - 仅防御
            return
        numbering_menu = self.menuBar().addMenu("编号管理(&N)")

        rule_action = QAction("编号规则设置(&R)", self)
        rule_action.triggered.connect(
            lambda: NumberingRuleDialog(
                service, self, initial_type=self.default_certificate_type
            ).exec()
        )
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

    def _build_print_menu(self) -> None:
        page = self.student_page
        if page is None:  # pragma: no cover - 仅防御
            return
        print_menu = self.menuBar().addMenu("打印管理(&P)")

        selected_action = QAction("打印选中记录(&S)", self)
        selected_action.triggered.connect(page.print_selected)
        print_menu.addAction(selected_action)

        filtered_action = QAction("打印当前筛选结果(&F)", self)
        filtered_action.triggered.connect(page.print_filtered)
        print_menu.addAction(filtered_action)

        checked_action = QAction("打印勾选记录(&C)", self)
        checked_action.triggered.connect(page.print_checked)
        print_menu.addAction(checked_action)

    def _build_verify_menu(self) -> None:
        page = self.student_page
        if page is None:  # pragma: no cover - 仅防御
            return
        verify_menu = self.menuBar().addMenu("核验管理(&V)")

        verify_action = QAction("核验证书(&V)…", self)
        verify_action.triggered.connect(page.verify_certificates)
        verify_menu.addAction(verify_action)

    def _build_system_menu(self) -> None:
        settings_service = self.settings_service
        audit_service = self.audit_service
        reset_service = self.reset_service
        if settings_service is None or audit_service is None or reset_service is None:
            return  # pragma: no cover - 仅防御

        system_menu = self.menuBar().addMenu("系统管理(&S)")

        settings_action = QAction("系统设置(&S)…", self)
        settings_action.triggered.connect(
            lambda: SettingsDialog(settings_service, self).exec()
        )
        system_menu.addAction(settings_action)

        audit_action = QAction("操作审计(&A)…", self)
        audit_action.triggered.connect(
            lambda: AuditLogDialog(audit_service, self).exec()
        )
        system_menu.addAction(audit_action)

        system_menu.addSeparator()

        reset_action = QAction("系统重置(&R)…", self)
        reset_action.triggered.connect(self.open_system_reset)
        system_menu.addAction(reset_action)

        system_menu.addSeparator()

        quit_action = QAction("退出(&Q)", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        system_menu.addAction(quit_action)

    def open_system_reset(self) -> None:
        """打开两步确认的系统重置窗口。"""
        if self.reset_service is None:
            return
        dialog = SystemResetDialog(self.reset_service, self)
        dialog.reset_completed.connect(self.handle_reset_completed)
        dialog.exec()

    def handle_reset_completed(self, result: object) -> None:
        """重置后刷新受影响的界面状态。"""
        page = self.student_page
        if page is not None:
            page.reload(reset_page=True)
        message = getattr(result, "message", "系统重置已完成")
        self.statusBar().showMessage(message, 10000)

    def _show_about(self) -> None:
        QMessageBox.about(self, "关于", f"{WINDOW_TITLE}\n版本 {__version__}")

    def _restore_geometry(self) -> None:
        geometry = self._settings.value(GEOMETRY_KEY)
        if geometry is not None:
            self.restoreGeometry(geometry)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._settings.setValue(GEOMETRY_KEY, self.saveGeometry())
        super().closeEvent(event)
