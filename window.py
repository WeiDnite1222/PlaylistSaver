import logging
import subprocess
from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QWidget,
    QLabel, QPushButton, QFrame, QScrollArea,
    QVBoxLayout, QHBoxLayout,
    QMessageBox, QLineEdit, QDialog, QApplication, )
from PySide6.QtCore import Qt, Signal, QObject

from constant import VERSION
from dialog import DownloadProgressDialog, PlayerDialog, SwitchProfileDialog, PlaylistWindow
from utils import download_file
from widgets import PlaylistItem
from yt_lib import PlaylistFetchException, resolve_video_page_url

logger = logging.getLogger("MainWindow")


class PlaylistMainWindow(QMainWindow):
    def __init__(self, app):
        super(PlaylistMainWindow, self).__init__()
        self.app = app
        self.child_windows = []
        self.setWindowTitle("PlaylistSaver")
        self.resize(700, 800)

        # Central widget
        self.central_widget = QWidget(self)
        self.setCentralWidget(self.central_widget)

        # Layouts
        self.main_layout = QHBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(12, 12, 12, 12)
        self.main_layout.setSpacing(12)

        self.right_layout = QVBoxLayout()
        self.right_layout.setContentsMargins(12, 12, 12, 12)
        self.right_layout.setSpacing(10)

        self.playlists_layout = QVBoxLayout()
        self.playlists_layout.setContentsMargins(10, 10, 10, 10)
        self.playlists_layout.setSpacing(8)

        # Frame
        self.playlists_scroll = QScrollArea(self)
        self.playlists_scroll.setWidgetResizable(True)
        self.playlists_scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.playlists_frame = QFrame(self.playlists_scroll)
        self.playlists_frame.setLayout(self.playlists_layout)
        self.playlists_scroll.setWidget(self.playlists_frame)

        self.playlists_scroll.setMinimumWidth(400)

        self.right_panel = QFrame(self.central_widget)
        self.right_panel.setObjectName("rightPanel")
        self.right_panel.setFixedWidth(190)
        self.right_panel.setLayout(self.right_layout)

        # Items
        self.profile_label = QLabel(f"Current Profile: {self.app.get_active_profile_name()}" ,)
        self.profile_label.setObjectName("profileLabel")
        self.profile_label.setWordWrap(True)

        self.loading_label = QLabel("Loading...")
        self.loading_label.setObjectName("loadingLabel")
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignBottom)

        self.switch_profile_btn = QPushButton("Switch Profile")
        self.helper_key_btn = QPushButton("Helper Secret Key")
        self.refresh_playlists_btn = QPushButton("Refresh playlists")
        self.save_all_playlists_btn = QPushButton("Save All Playlists")
        self.open_download_folder_btn = QPushButton("Open Download Folder")
        self.switch_profile_btn.setMinimumHeight(34)
        self.helper_key_btn.setMinimumHeight(34)
        self.refresh_playlists_btn.setMinimumHeight(34)
        self.save_all_playlists_btn.setMinimumHeight(34)

        self.version_label = QLabel(f"v{VERSION}")
        self.version_label.setObjectName("versionLabel")

        self.playlist_items_layout = QVBoxLayout()
        self.playlist_items_layout.setContentsMargins(0, 0, 0, 0)
        self.playlist_items_layout.setSpacing(8)

        self.playlists_layout.addLayout(self.playlist_items_layout)
        self.playlists_layout.addWidget(self.loading_label, 0, Qt.AlignmentFlag.AlignHCenter)

        # Bind item
        self.left_layout = QVBoxLayout()
        self.left_layout.setContentsMargins(0, 0, 0, 0)
        self.left_layout.setSpacing(10)
        self.left_layout.addWidget(self.playlists_scroll, 1)

        self.right_layout.addWidget(self.profile_label)
        self.right_layout.addWidget(self.switch_profile_btn)
        self.right_layout.addWidget(self.helper_key_btn)
        self.right_layout.addWidget(self.refresh_playlists_btn)
        self.right_layout.addWidget(self.save_all_playlists_btn)
        self.right_layout.addWidget(self.open_download_folder_btn)
        self.right_layout.addStretch(1)
        self.right_layout.addWidget(self.version_label, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter)

        self.main_layout.addLayout(self.left_layout, 1)
        self.main_layout.addWidget(self.right_panel)

        self.setStyleSheet("""
            QMainWindow {
                background-color: #202020;
            }

            QScrollArea, QFrame {
                background-color: #242424;
            }

            QLabel {
                background-color: transparent;
            }

            #rightPanel {
                background-color: #2b2b2b;
                border: 1px solid #3c3c3c;
                border-radius: 8px;
            }

            #profileLabel {
                color: #f0f0f0;
                font-weight: 600;
                padding-bottom: 6px;
            }

            #loadingLabel {
                color: #a8a8a8;
                padding: 4px;
            }

            QPushButton {
                background-color: #3a3a3a;
                color: #f0f0f0;
                border: 1px solid #505050;
                border-radius: 6px;
                padding: 7px 10px;
            }

            QPushButton:hover {
                background-color: #464646;
            }

            QPushButton:pressed {
                background-color: #303030;
            }
        """)

        # Bind signals function
        self.signals = self.PlaylistWorkerSignals()
        self.signals.playlists_ready.connect(self.build_ui)
        self.signals.error.connect(self.show_error)
        self.signals.playlist_fetch_error.connect(self.show_playlist_fetch_error)
        self.signals.download_status.connect(self.loading_label.setText)

        self.switch_profile_btn.clicked.connect(self.open_switch_profile_dialog)
        self.helper_key_btn.clicked.connect(self.show_helper_secret_key)
        self.refresh_playlists_btn.clicked.connect(self.app.reload_playlists)
        self.save_all_playlists_btn.clicked.connect(self.save_all_playlists)
        self.open_download_folder_btn.clicked.connect(self.app.open_download_folder)

    class PlaylistWorkerSignals(QObject):
        # Signals
        playlists_ready = Signal(dict)
        profile_delete = Signal(str)

        # playlist
        playlist_clicked = Signal(dict)
        video_clicked = Signal(str)

        # error
        error = Signal(str)
        playlist_fetch_error = Signal(object)
        download_status = Signal(str)

    def build_ui(self, playlists: dict[str, list]):
        self.clear_layout(self.playlist_items_layout)

        playlists = playlists.get("entries", [])

        playlists = [playlist for playlist in playlists if playlist is not None]  # ignore None item

        for playlist in playlists:
            plist_id = playlist.get("id", None)
            title = playlist.get("title", "Title not found")

            item = PlaylistItem(
                self.playlists_frame,
                playlist,
                title=title,
                thumbnail_size=self.app.THUMBNAIL_SIZE
            )

            if len(playlist.get("thumbnails", [])) > 0:
                thumbnail_url = playlist.get("thumbnails", [])[0].get('url', None)
                thumbnail_resolution = playlist.get("thumbnails", [])[0].get('resolution', None)

                thumbnail_path = Path(self.app.temp_path, "thumbnails",
                                      f"thumbnail_{plist_id}_{thumbnail_resolution}.jpg")

                result = download_file(thumbnail_url, thumbnail_path)

                if result or (thumbnail_path.exists() and thumbnail_path.is_file()):
                    item.set_thumbnail(thumbnail_path)

            item.clicked.connect(self.open_playlist_window)
            self.playlist_items_layout.addWidget(item)

        self.loading_label.setText("Done")

    def save_all_playlists(self):
        playlists = [item for item in self.app.playlists.get("entries", []) if item]
        if not playlists:
            QMessageBox.information(self, "Download", "No playlists loaded.")
            return
        self.progress_dialog = DownloadProgressDialog(
            "Save All Playlists", 0, self, producers=len(playlists)
        )
        self.app.download_all_playlists(
            self.progress_dialog.finish_future,
            self.progress_dialog.tasks_added.emit,
            self.progress_dialog.producer_finished.emit,
        )

    def on_download_done(self, result, video=None, playlist_name=None):
        if isinstance(result, Exception):
            self.signals.error.emit(f"Download failed: {result}")
            return
        try:
            path = result.result()
        except Exception as error:
            self.signals.error.emit(f"Download failed: {error}")
            return
        self.signals.download_status.emit(f"Downloaded: {path.name}")

    def clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            child_layout = item.layout()
            widget = item.widget()
            if child_layout:
                self.clear_layout(child_layout)
            if widget:
                widget.deleteLater()

    def open_playlist_window(self, playlist: dict):
        window = PlaylistWindow(self.app, playlist, parent=self,
                                open_player_callback=self.open_video_player)
        self.child_windows.append(window)

        window.destroyed.connect(
            lambda _obj=None, w=window: self.child_windows.remove(w) if w in self.child_windows else None
        )

        window.show()

    def show_error(self, message: str):
        QMessageBox.critical(self, "Error", message)

    def show_playlist_fetch_error(self, error: PlaylistFetchException):
        error.create_messagebox(self)

    def open_video_player(self, video: dict):
        video_url = resolve_video_page_url(video)
        if not video_url:
            QMessageBox.critical(self, "Error", "Unable to resolve video URL.")
            return

        dialog = PlayerDialog(self.app, video, parent=self)
        self.child_windows.append(dialog)
        dialog.destroyed.connect(
            lambda _obj=None, w=dialog: self.child_windows.remove(w) if w in self.child_windows else None)
        dialog.show()

    def open_switch_profile_dialog(self):
        dialog = SwitchProfileDialog(self.app, parent=self)
        dialog.profile_changed.connect(self.apply_profile_switch)
        dialog.exec()

    def show_helper_secret_key(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Helper Secret Key")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Copy this key into the PlaylistSaver Helper settings page:"))

        key_field = QLineEdit(self.app.secret_key or "")
        key_field.setReadOnly(True)
        key_field.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(key_field)

        buttons = QHBoxLayout()
        reveal_btn = QPushButton("Show")
        copy_btn = QPushButton("Copy")
        close_btn = QPushButton("Close")
        reveal_btn.clicked.connect(lambda: key_field.setEchoMode(QLineEdit.EchoMode.Normal))
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(key_field.text()))
        close_btn.clicked.connect(dialog.accept)
        buttons.addWidget(reveal_btn)
        buttons.addWidget(copy_btn)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)
        dialog.resize(540, 140)
        dialog.exec()

    def apply_profile_switch(self, profile_name: str):
        self.app.set_active_profile(profile_name)
        self.profile_label.setText(f"Current Profile: {profile_name}")

    def closeEvent(self, event):
        if not self.app.has_active_download_tasks():
            event.accept()
            return

        answer = QMessageBox.question(
            self,
            "Downloads in progress",
            "Downloads are still running. Close the application and cancel them?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.app.cancel_download_tasks()
            event.accept()
        else:
            event.ignore()
