"""Entry point for the portable xworldconfig application."""
import sys

from PySide6.QtWidgets import QApplication

from xworldconfig.gui.main_window import MainWindow
from xworldconfig.gui.theme import apply_theme


def main() -> int:
    app = QApplication(sys.argv)
    apply_theme(app)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
