"""Entry point for the portable xworldconfig application."""
import sys

from PySide6.QtWidgets import QApplication

from xworldconfig.gui.main_window import MainWindow
from xworldconfig.gui.theme import apply_theme

# xworldconfig.gui.dialogs.ProgressDialog/DriftChoiceDialog run a nested
# QDialog.exec() loop driven by a background-thread signal (a checkbox
# toggle or bulk apply). That combination of a nested Qt event loop plus
# cross-thread queued signal delivery was observed to consume several
# thousand stack frames through PySide6's C++/Python call boundary alone -
# well within the default 1000-frame Python recursion limit's reach even
# though there is no actual runaway recursion, confirmed by testing that a
# higher limit alone resolves it with an otherwise-shallow real call stack.
sys.setrecursionlimit(10000)


def main() -> int:
    app = QApplication(sys.argv)
    apply_theme(app)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
