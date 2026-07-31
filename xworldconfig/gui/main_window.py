"""Main application window: the edition/region/category/object-type tree and
toolbar actions."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMainWindow


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("xworldconfig")
        self.resize(1000, 700)
        self.setCentralWidget(QLabel("Scenery tree goes here.", alignment=Qt.AlignCenter))
