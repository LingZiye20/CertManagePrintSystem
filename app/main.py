"""应用入口：创建 QApplication、执行启动引导并显示主窗口。"""

from __future__ import annotations

import logging
import sqlite3
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from app.bootstrap import bootstrap
from app.data.db import SchemaVersionError
from app.ui.main_window import MainWindow

ORGANIZATION_NAME = "CertManagePrintSystem"
APPLICATION_NAME = "CertManagePrintSystem"


def run() -> int:
    """启动应用，返回进程退出码。"""
    app = QApplication(sys.argv)
    app.setOrganizationName(ORGANIZATION_NAME)
    app.setApplicationName(APPLICATION_NAME)

    try:
        context = bootstrap()
    except (OSError, sqlite3.Error, SchemaVersionError) as exc:
        logging.getLogger(__name__).exception("启动初始化失败")
        QMessageBox.critical(None, "启动失败", f"无法初始化程序运行环境：\n{exc}")
        return 1

    window = MainWindow(context=context)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
