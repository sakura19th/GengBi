"""赓笔 小说续写器入口模块。

负责初始化日志、加载配置、启动 PySide6 主窗口。
"""
from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from novelforge.core.config import ConfigManager
from novelforge.core.logger import setup_logging
from novelforge.services.async_runner import AsyncLoopRunner
from novelforge.ui.main_window import MainWindow


def main() -> int:
    """应用主入口。

    执行顺序：日志初始化 → 配置加载 → 创建 QApplication → 显示主窗口。
    返回应用程序退出码。
    """
    # 先初始化日志系统，确保后续流程可记录日志
    setup_logging()

    # 加载全局配置（含版本迁移与损坏恢复）
    config_manager = ConfigManager()
    config_manager.load()

    app = QApplication(sys.argv)
    app.setApplicationName("赓笔")
    app.setOrganizationName("赓笔")

    window = MainWindow(config_manager)
    window.show()

    exit_code = app.exec()

    # 清理异步运行器，确保 pending 协程完成（取消并关闭后台事件循环）
    try:
        AsyncLoopRunner.instance().shutdown()
    except Exception as e:
        # 记录但不阻塞退出
        logging.getLogger(__name__).warning("AsyncLoopRunner 清理失败: %s", e)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
