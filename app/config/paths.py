"""统一路径管理。

路径以程序所在目录为基准：源码运行时为项目根，打包后为可执行文件所在目录。
目录布局（对应提示词包 A.2）：::

    <程序目录>/
    ├─ resources/   # 只读资源、图标、默认模板
    ├─ data/        # SQLite 数据库
    ├─ photos/      # 系统管理的学生照片
    ├─ logs/        # 运行日志
    └─ config/      # 独立存放的运行期配置文件

数据库、照片、日志等长期数据不得写入 ``_MEIPASS`` 临时解包目录。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

DB_FILE_NAME = "certificates.db"
LOG_FILE_NAME = "app.log"
RESOURCE_DIR_NAME = "resources"
DATA_DIR_NAME = "data"
PHOTOS_DIR_NAME = "photos"
LOGS_DIR_NAME = "logs"
CONFIG_DIR_NAME = "config"


def application_dir() -> Path:
    """返回程序所在目录：源码运行时为项目根，打包后为可执行文件所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


def _resolve_resource_dir(program_root: Path) -> Path:
    """按程序根目录解析只读资源目录。

    优先使用 ``<program_root>/resources``；该目录不存在且运行在 PyInstaller
    打包环境时，回退到只读资源目录 ``<_MEIPASS>/resources``。该回退仅用于
    只读资源，运行期数据目录不参与此回退。
    """
    local_resources = program_root / RESOURCE_DIR_NAME
    if local_resources.is_dir():
        return local_resources

    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bundled = Path(meipass) / RESOURCE_DIR_NAME
            if bundled.is_dir():
                return bundled

    return local_resources


def default_program_root() -> Path:
    """返回运行期数据根目录，即程序所在目录。"""
    return application_dir()


@dataclass(frozen=True, slots=True)
class AppPaths:
    """一次性解析出的路径集合。

    使用不可变数据类，避免以全局可变状态在模块间传递业务数据；
    测试可传入临时根目录，不触碰真实程序目录。
    """

    program_root: Path

    @classmethod
    def for_program(cls, root: Path | None = None) -> AppPaths:
        """解析程序根目录；``root`` 仅用于测试或显式覆盖。"""
        return cls(program_root=Path(root) if root is not None else default_program_root())

    @property
    def db_dir(self) -> Path:
        return self.program_root / DATA_DIR_NAME

    @property
    def db_file(self) -> Path:
        return self.db_dir / DB_FILE_NAME

    @property
    def photos_dir(self) -> Path:
        return self.program_root / PHOTOS_DIR_NAME

    @property
    def logs_dir(self) -> Path:
        return self.program_root / LOGS_DIR_NAME

    @property
    def log_file(self) -> Path:
        return self.logs_dir / LOG_FILE_NAME

    @property
    def config_dir(self) -> Path:
        return self.program_root / CONFIG_DIR_NAME

    @property
    def resource_dir(self) -> Path:
        """当前实例程序根目录下的只读资源目录（含 PyInstaller 回退）。"""
        return _resolve_resource_dir(self.program_root)

    def ensure_user_dirs(self) -> None:
        """创建必要的运行期目录。

        创建过程本身即验证程序目录可写；失败时由 ``OSError`` 直接暴露给调用方，
        不静默切换其他位置。
        """
        for directory in (
            self.program_root,
            self.db_dir,
            self.photos_dir,
            self.logs_dir,
            self.config_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
