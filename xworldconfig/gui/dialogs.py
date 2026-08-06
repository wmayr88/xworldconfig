"""Two modal dialogs used by every operation that actually writes DSF files
(a checkbox toggle, a cross-region bulk action, or resolving a startup
drift check).

ProgressDialog is pure feedback: no buttons, and it cannot be closed by the
user (no window-close button, Escape is ignored) - only the caller closing
it programmatically, once the background work finishes, ends it. Since it's
a write in progress, there is nothing safe to let the user interrupt.

DriftChoiceDialog is the one place a real decision is required (apply the
existing configuration to updated tiles, or reset to fully enabled), so it
keeps its two buttons, but is otherwise just as undismissable - leaving it
unanswered would itself be a form of the "UI and files disagree" state this
whole mechanism exists to avoid."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

from xworldconfig.dsf.inventory import TileCount
from xworldconfig.gui.formatting import render_progress_bar


class _UndismissableDialog(QDialog):
    def __init__(self, parent, title: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setModal(True)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override signature
        event.ignore()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override signature
        if event.key() == Qt.Key_Escape:
            event.ignore()
            return
        super().keyPressEvent(event)

    def force_close(self) -> None:
        """The only way this dialog ever closes - called by the caller once
        the background work it's reporting on has actually finished."""
        super().done(QDialog.Accepted)


class ProgressDialog(_UndismissableDialog):
    def __init__(self, parent, title: str, description: str):
        super().__init__(parent, title)
        layout = QVBoxLayout(self)
        self._description_label = QLabel(description)
        layout.addWidget(self._description_label)
        self._progress_label = QLabel()
        self._progress_label.setStyleSheet("font-family: monospace;")
        layout.addWidget(self._progress_label)
        self.set_progress(0, 0)
        self.resize(560, 120)

    def set_description(self, description: str) -> None:
        self._description_label.setText(description)

    def set_progress(self, done: int, total: int) -> None:
        self._progress_label.setText(render_progress_bar(done, total))


class DriftChoiceDialog(_UndismissableDialog):
    def __init__(self, parent, drifted_folder_names: list[str]):
        super().__init__(parent, "Scenery changed since last run")
        layout = QVBoxLayout(self)

        message = QLabel(
            f"{len(drifted_folder_names)} folder(s) with disabled object types have changed on "
            "disk since xworldconfig last wrote them - most likely a simHeaven update:\n\n"
            + "\n".join(f"  • {name}" for name in drifted_folder_names)
            + "\n\nApply your existing disabled-type choices to the updated files, or reset "
            "these folders to fully enabled and reconfigure them from scratch."
        )
        message.setWordWrap(True)
        layout.addWidget(message)

        button_row = QHBoxLayout()
        self.apply_button = QPushButton("Apply existing configuration")
        self.reset_button = QPushButton("Reset to enabled")
        button_row.addWidget(self.apply_button)
        button_row.addWidget(self.reset_button)
        layout.addLayout(button_row)

        self._choice: str | None = None
        self.apply_button.clicked.connect(self._choose_apply)
        self.reset_button.clicked.connect(self._choose_reset)
        self.resize(560, 240)

    def _choose_apply(self) -> None:
        self._choice = "apply"
        self.force_close()

    def _choose_reset(self) -> None:
        self._choice = "reset"
        self.force_close()

    def exec_and_get_choice(self) -> str:
        self.exec()
        assert self._choice is not None
        return self._choice


class TileBreakdownDialog(QDialog):
    """Testing/debugging aid: which tiles a type's instances actually live
    in, and how many per tile. Purely informational (no write involved), so
    unlike the other two dialogs in this module it's freely dismissable."""

    def __init__(self, parent, type_label: str, tile_counts: list[TileCount]):
        super().__init__(parent)
        self.setWindowTitle(f"Tiles containing '{type_label}'")
        layout = QVBoxLayout(self)

        if not tile_counts:
            layout.addWidget(QLabel("No tiles currently contain this type."))
        else:
            text = QPlainTextEdit()
            text.setReadOnly(True)
            text.setStyleSheet("font-family: monospace;")
            text.setPlainText(
                "\n".join(
                    f"{tc.tile.name:<16} active={tc.active_count:>10,}  disabled={tc.disabled_count:>8,}"
                    for tc in tile_counts
                )
            )
            layout.addWidget(text)

            total_active = sum(tc.active_count for tc in tile_counts)
            total_disabled = sum(tc.disabled_count for tc in tile_counts)
            layout.addWidget(
                QLabel(f"{len(tile_counts)} tile(s) - {total_active:,} active, {total_disabled:,} disabled")
            )

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)
        self.resize(560, 420)
