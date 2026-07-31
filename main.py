"""Entry point for the portable xworldconfig application."""
import sys

from PySide6.QtWidgets import QApplication

from xworldconfig.gui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
