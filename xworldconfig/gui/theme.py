"""Applies a Fusion-based style plus a small stylesheet so controls (buttons
especially) render with a visible border/hover/pressed state in both light
and dark desktop themes, rather than depending on the host Qt theme's
toolbar-button styling (which on some Linux desktops renders as flat,
borderless text with no visible affordance)."""
from PySide6.QtWidgets import QApplication

_STYLESHEET = """
QPushButton {
    background-color: palette(button);
    color: palette(button-text);
    border: 1px solid palette(mid);
    border-radius: 4px;
    padding: 5px 14px;
}
QPushButton:hover {
    border: 1px solid palette(highlight);
}
QPushButton:pressed {
    background-color: palette(dark);
}
QPushButton:disabled {
    color: palette(disabled-text);
    border: 1px solid palette(disabled-text);
}
QLineEdit {
    border: 1px solid palette(mid);
    border-radius: 4px;
    padding: 4px 6px;
    background-color: palette(base);
}
QLineEdit:focus {
    border: 1px solid palette(highlight);
}
QTreeWidget {
    border: 1px solid palette(mid);
}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    app.setStyleSheet(_STYLESHEET)
