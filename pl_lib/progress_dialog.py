from PySide6.QtCore import Qt, Slot, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class ProgressRow(QWidget):
    def __init__(self, name: str, parent=None):
        super().__init__(parent)

        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

        self.name_label = QLabel(name, self)
        self.name_label.setWordWrap(True)
        self.name_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        self.status_label = QLabel("Preparing...", self)
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)

        # Flags
        self.finished = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        layout.addWidget(self.name_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress_bar)


class ProgressDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Downloads")
        self.resize(500, 300)
        self.setMinimumSize(360, 220)

        self.rows: dict[str, ProgressRow] = {}

        self.scroll_area = QScrollArea(self)
        self.download_widget = QWidget(self.scroll_area)
        self.download_layout = QVBoxLayout(self.download_widget)
        self.download_layout.setContentsMargins(4, 4, 4, 4)
        self.download_layout.setSpacing(8)
        self.download_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.scroll_area.setWidget(self.download_widget)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self.close_button = QPushButton("Close", self)
        self.close_button.clicked.connect(self.hide)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addWidget(self.scroll_area, 1)
        layout.addWidget(
            self.close_button,
            0,
            Qt.AlignmentFlag.AlignRight,
        )

        self.close_timer = QTimer(self)
        self.close_timer.setSingleShot(True)
        self.close_timer.timeout.connect(self.cleanup)

    @Slot(str, str)
    def add_task(self, task_id: str, name: str):
        # Prevent new tasks from being deleted by cleanup timer
        if task_id in self.rows:
            return

        row = ProgressRow(name, self.download_widget)
        self.rows[task_id] = row

        self.download_layout.addWidget(row)

        if not self.isVisible():
            self.show()

    @Slot(str, str, float)
    def update_task(
        self,
        task_id: str,
        status: str,
        percent: float,
    ):
        row = self.rows.get(task_id)
        if row is None:
            # Prevent started、progress missing due to timing issues
            self.add_task(task_id, task_id)
            row = self.rows[task_id]

        value = max(0, min(100, round(percent)))
        row.status_label.setText(status)
        row.progress_bar.setValue(value)

    @Slot(str)
    def finish_task(self, task_id: str):
        row = self.rows.get(task_id)
        if row is None:
            return

        row.status_label.setText("Completed")
        row.progress_bar.setValue(100)
        row.finished = True

        if self.all_tasks_finished():
            self.close_timer.start(600)

    def all_tasks_finished(self):
        return bool(self.rows) and all(
            row.finished
            for row in self.rows.values()
        )

    def cleanup(self):
        """
        If you want to use this function with a timer. use self.close_timer.start(TIMEOUT)
        :return:
        """
        for task_id in list(self.rows):
            self.remove_task(task_id)

        self.hide()

    @Slot(str, str)
    def fail_task(self, task_id: str, error: str):
        row = self.rows.get(task_id)
        if row is None:
            self.add_task(task_id, task_id)
            row = self.rows[task_id]

        row.status_label.setText(f"Failed: {error}")
        row.progress_bar.setStyleSheet(
            "QProgressBar::chunk { background-color: #c62828; }"
        )
        # Move to top
        self.download_layout.removeWidget(row)
        self.download_layout.insertWidget(0, row)

    @Slot(str)
    def remove_task(self, task_id: str):
        row = self.rows.pop(task_id, None)
        if row is not None:
            self.download_layout.removeWidget(row)
            row.setParent(None)
            row.deleteLater()
