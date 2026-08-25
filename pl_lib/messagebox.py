from PySide6.QtWidgets import QMessageBox, QMainWindow, QWidget, QDialog, QVBoxLayout, QLabel, QPlainTextEdit, \
    QDialogButtonBox, QSizePolicy


def create_messagebox(parent: QWidget | None, title: str, message: str,
                      icon: QMessageBox.Icon = QMessageBox.Icon.Information,
                      button: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok) -> QMessageBox:
    msg = QMessageBox(parent)
    msg.setWindowTitle(title)
    msg.setText(message)
    msg.setIcon(icon)
    msg.setStandardButtons(button)
    return msg


def error(parent: QWidget | None, title: str, message: str,
          button: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok) -> None:
    create_messagebox(
        parent,
        title=title,
        message=message,
        icon=QMessageBox.Icon.Critical,
        button=button
    ).exec()


def info(parent: QWidget | None, title: str, message: str,
         button: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok) -> None:
    create_messagebox(
        parent,
        title=title,
        message=message,
        icon=QMessageBox.Icon.Information,
        button=button
    ).exec()


def warning(parent: QWidget | None, title: str, message: str,
            button: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok) -> None:
    create_messagebox(
        parent,
        title=title,
        message=message,
        icon=QMessageBox.Icon.Warning,
        button=button
    ).exec()


def error_with_plain_text(
    parent: QWidget | None,
    title: str,
    message: str,
    content: str,
) -> None:
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.resize(720, 420)

    layout = QVBoxLayout(dialog)

    label = QLabel(message)
    label.setWordWrap(True)

    details_box = QPlainTextEdit()
    details_box.setReadOnly(True)
    details_box.setPlainText(content)
    details_box.setSizePolicy(
        QSizePolicy.Policy.Expanding,
        QSizePolicy.Policy.Expanding,
    )

    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
    buttons.accepted.connect(dialog.accept)

    layout.addWidget(label)
    layout.addWidget(details_box, 1)
    layout.addWidget(buttons)
    dialog.exec()


def ask_yes_no_with_plain_text(parent: QWidget | None, title: str, message: str, content: str):
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.resize(720, 420)

    layout = QVBoxLayout(dialog)

    label = QLabel(message)
    label.setWordWrap(True)
    label.setWordWrap(True)

    details_box = QPlainTextEdit()
    details_box.setReadOnly(True)
    details_box.setPlainText(content)
    details_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Yes |
        QDialogButtonBox.StandardButton.No
    )
    buttons.button(QDialogButtonBox.StandardButton.Yes).clicked.connect(dialog.accept)
    buttons.button(QDialogButtonBox.StandardButton.No).clicked.connect(dialog.reject)

    layout.addWidget(label)
    layout.addWidget(details_box, 1)
    layout.addWidget(buttons)

    return dialog.exec() == QDialog.DialogCode.Accepted
