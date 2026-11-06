"""Start the CopyAssistant desktop application."""

import logging
import sys
from logging.handlers import RotatingFileHandler

from PySide6.QtWidgets import QApplication, QMessageBox

from config import APP_NAME, LOG_FILENAME, VERSION, SettingsStore, data_dir
from credentials import Credentials
from main_window import MainWindow


def main() -> int:
    """Load settings and run the Qt event loop."""
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(VERSION)
    directory = data_dir()
    store = SettingsStore(directory)
    try:
        logfile = directory / LOG_FILENAME
        logfile.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            handlers=[RotatingFileHandler(logfile, maxBytes=262144, backupCount=2, encoding="utf-8")],
        )
        settings = store.load()
    except (OSError, ValueError, KeyError, TypeError):
        QMessageBox.critical(
            None, APP_NAME, f"无法读取设置或打开日志。请检查目录中的文件与权限。\n{directory}"
        )
        return 1
    logging.info("CopyAssistant %s started.", VERSION)
    window = MainWindow(app.clipboard(), settings, store, Credentials())
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
