import copy
import datetime
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import traceback
import webbrowser
from pathlib import Path
from yt_dlp.networking.exceptions import HTTPError
from typing import Callable
import click
import requests
from yt_dlp import YoutubeDL
from utils import save_json, read_json, check_url_scheme, parser_url_scheme, find_mpv_path, find_http_status

# Qt
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QDialog,
    QLabel, QPushButton, QFrame, QScrollArea,
    QVBoxLayout, QHBoxLayout, QGridLayout,
    QMessageBox, QInputDialog, QSizePolicy, QFileDialog,
)
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QThread, QSize, QUrl
from PySide6.QtGui import QPixmap, QFont, QImage, QIcon, QDesktopServices

VERSION = "Alpha-2-QtTest"
PROGRAM_DIR = Path(__file__).parent

work_dir = None

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

YT_PLAYLISTS_URL = "https://www.youtube.com/feed/playlists"

def sanitize_filename(value: str):
    invalid_chars = '<>:"/\\|?*'
    return "".join("_" if char in invalid_chars else char for char in value)


def fetch_video_details(video_url: str, opts):
    opts = copy.deepcopy(opts)
    opts.update({"extract_flat": True, })

    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
    except Exception as e:
        logger.error(f"Unable to fetch video details: {e}\nURL:{video_url}")
        return None

    return info


def resolve_video_page_url(video: dict):
    return (
            video.get("webpage_url")
            or video.get("url")
            or (f"https://www.youtube.com/watch?v={video.get('id')}" if video.get("id") else None)
    )


def download_file(url, dest: Path):
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True) as r:
            with dest.open(mode="wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        return True
    except Exception as e:
        logger.error("Unable to download file: %s", e)
        return False


fetch_playlist_lock = threading.Lock()

class PlaylistFetchException(Exception):
    def __init__(self, url, title, reason, enable_option=False, options=None, no_cancel=False):
        super().__init__()
        self.url = url
        self.title = title
        self.reason = reason
        self.qt_options = {
            "enable": enable_option,
            "options": options,
            "noCancelOption": no_cancel,
        }

    def create_messagebox(self, master):
        box = QMessageBox(master)
        box.setWindowTitle(self.title)
        box.setText(self.reason or self.title)
        box.setIcon(QMessageBox.Icon.Critical)

        is_custom_option_enabled = self.qt_options["enable"] is True and type(self.qt_options["options"]) is list

        if is_custom_option_enabled:
            for option in self.qt_options["options"]:
                label = option.get("label", None)
                action_func = option.get("action", None)
                role = option.get("role", QMessageBox.ButtonRole.AcceptRole)

                # Some type check
                if label is None or (action_func is None or not callable(action_func)):
                    logger.warning(f"Skipping {option} ({action_func}) because its label or action is not set yet or not callable.")
                    continue

                if not isinstance(role, QMessageBox.ButtonRole):
                    logger.warning(f"Skipping {option} ({role}) because its role type is not QMessageBox.ButtonRole.")
                    continue

                btn = box.addButton(label, role)
                option["button"] = btn

        if not self.qt_options["noCancelOption"]:
            box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        elif not is_custom_option_enabled:
            box.addButton(QMessageBox.StandardButton.Ok)

        box.exec()

        clicked = box.clickedButton()

        # If the option's button is clicked, Call the target action function
        if is_custom_option_enabled:
            for option in self.qt_options["options"]:
                btn = option.get("button")
                action_func = option.get("action")

                if clicked == btn:
                    return action_func()

        return None

def fetch_playlists_data(url_schema, cookie_file_path: Path, opts, callback: Callable):
    check_url_scheme(os.path.abspath(__file__), work_dir)

    error = None

    if url_schema is not None:
        try:
            parser_url_scheme(url_schema, cookie_path=cookie_file_path)
        except Exception as e:
            logger.error("Unable to parse URL scheme: %s", e)
            input("Press enter to continue...")

    playlists_data = None

    with fetch_playlist_lock:
        opts = copy.deepcopy(opts)
        opts.update({
            'extract_flat': True,
            'skip_download': True,
        })

        try:
            with YoutubeDL(opts) as ydl:
                playlists_data = ydl.extract_info(YT_PLAYLISTS_URL, download=False)
        except Exception as e:
            status_code = find_http_status(e)
            if status_code == 401:
                error = "Cookie are expired. Reopen the tool again in browser!", None
            elif status_code == 403:
                error = "Server forbidden. Did you logged in?", None
            elif status_code == 429:
                error = "Too many requests. Try again later.", None
            elif status_code == 503:
                error = "Server unavailable. Try again later.", None
            elif status_code == 522:
                error = "Connection timed out.", None
            else:
                error = (f"Unexpected error: {e} (Type: {type(e)})\n"
                         f"Traceback: {traceback.format_exc()}", None)

    if playlists_data is None:
        logger.error(f"Unable to fetch playlists: {error}")
        playlists_data = {"entries": []}

    pf_exec = None
    if error is not None:
        reason = error[0] if isinstance(error, tuple) else error
        pf_exec = PlaylistFetchException(
            url=YT_PLAYLISTS_URL,
            title="Playlists Fetch Error",
            reason=reason,
            enable_option=True,
            options=[
                {"label": "Reopen browser",
                 "action": lambda: webbrowser.open(YT_PLAYLISTS_URL)},
            ],
        )

    callback(playlists_data, pf_exec)


class PlaylistSaver(QApplication):
    DEFAULT_PROFILE_NAME = "default"
    THUMBNAIL_SIZE = QSize(144, 81)
    mpv_processes = {}

    def __init__(self, url_schema=None, cookie_file: str | None = None):
        super().__init__()
        self.setApplicationName("PlaylistSaver")

        self.url_schema = url_schema
        self.cookie_file = cookie_file

        # Path
        self.save_path = Path(work_dir, "save")
        self.temp_path = Path(work_dir, "temp")
        self.profiles_meta_path = Path(self.save_path, "profiles.json")
        self.profiles_path = Path(self.save_path, "profiles")
        self.cookie_path = Path(self.profiles_path, self.DEFAULT_PROFILE_NAME, "cookies.txt")

        # Profile
        self.active_profile_name = self.DEFAULT_PROFILE_NAME

        # Data
        self.playlists: dict[str, list] = {
            "entries": []
        }

        self.window = self.MainWindow(self)

        self.init()
        self.window.show()

    class MainWindow(QMainWindow):
        def __init__(self, parent):
            super().__init__()
            self.parent = parent
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
            self.playlists_scroll.setFrameShape(QFrame.NoFrame)

            self.playlists_frame = QFrame(self.playlists_scroll)
            self.playlists_frame.setLayout(self.playlists_layout)
            self.playlists_scroll.setWidget(self.playlists_frame)

            self.playlists_scroll.setMinimumWidth(400)

            self.right_panel = QFrame(self.central_widget)
            self.right_panel.setObjectName("rightPanel")
            self.right_panel.setFixedWidth(190)
            self.right_panel.setLayout(self.right_layout)

            # Items
            self.profile_label = QLabel(f"Current Profile: {self.parent.get_active_profile_name()}",)
            self.profile_label.setObjectName("profileLabel")
            self.profile_label.setWordWrap(True)

            self.loading_label = QLabel("Loading...")
            self.loading_label.setObjectName("loadingLabel")
            self.loading_label.setAlignment(Qt.AlignCenter)

            self.switch_profile_btn = QPushButton("Switch Profile")
            self.refresh_playlists_btn = QPushButton("Refresh playlists")
            self.switch_profile_btn.setMinimumHeight(34)
            self.refresh_playlists_btn.setMinimumHeight(34)

            self.version_label = QLabel(f"v{VERSION}")
            self.version_label.setObjectName("versionLabel")

            self.playlist_items_layout = QVBoxLayout()
            self.playlist_items_layout.setContentsMargins(0, 0, 0, 0)
            self.playlist_items_layout.setSpacing(8)

            self.playlists_layout.addWidget(self.loading_label, 0, Qt.AlignHCenter)
            self.playlists_layout.addLayout(self.playlist_items_layout)

            # Bind item
            self.left_layout = QVBoxLayout()
            self.left_layout.setContentsMargins(0, 0, 0, 0)
            self.left_layout.setSpacing(10)
            self.left_layout.addWidget(self.playlists_scroll, 1)

            self.right_layout.addWidget(self.profile_label)
            self.right_layout.addWidget(self.switch_profile_btn)
            self.right_layout.addWidget(self.refresh_playlists_btn)
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

            self.switch_profile_btn.clicked.connect(self.open_switch_profile_dialog)
            self.refresh_playlists_btn.clicked.connect(self.parent.reload_playlists)

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

        def build_ui(self, playlists: dict[str, list]):
            self.clear_layout(self.playlist_items_layout)

            playlists = playlists.get("entries", [])

            playlists = [playlist for playlist in playlists if playlist is not None]  # ignore None item

            for playlist in playlists:
                plist_id = playlist.get("id", None)
                title = playlist.get("title", "Title not found")

                item = self.PlaylistItem(
                    self.playlists_frame,
                    playlist,
                    title=title,
                )

                if len(playlist.get("thumbnails", [])) > 0:
                    thumbnail_url = playlist.get("thumbnails", [])[0].get('url', None)
                    thumbnail_resolution = playlist.get("thumbnails", [])[0].get('resolution', None)

                    thumbnail_path = Path(work_dir, "temp", "thumbnails",
                                          f"thumbnail_{plist_id}_{thumbnail_resolution}.jpg")

                    result = download_file(thumbnail_url, thumbnail_path)

                    if result or (thumbnail_path.exists() and thumbnail_path.is_file()):
                        item.set_thumbnail(thumbnail_path)

                item.clicked.connect(self.open_playlist_window)
                self.playlist_items_layout.addWidget(item)

            self.loading_label.setText("Done")

        @staticmethod
        def clear_layout(layout):
            while layout.count():
                item = layout.takeAt(0)
                child_layout = item.layout()
                widget = item.widget()
                if child_layout:
                    PlaylistSaver.MainWindow.clear_layout(child_layout)
                if widget:
                    widget.deleteLater()

        def open_playlist_window(self, playlist: dict):
            window = self.PlaylistWindow(self, playlist)
            self.child_windows.append(window)
            window.destroyed.connect(
                lambda _obj=None, w=window: self.child_windows.remove(w) if w in self.child_windows else None)
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

            dialog = self.PlayerDialog(self, video)
            self.child_windows.append(dialog)
            dialog.destroyed.connect(
                lambda _obj=None, w=dialog: self.child_windows.remove(w) if w in self.child_windows else None)
            dialog.show()

        def open_switch_profile_dialog(self):
            dialog = self.SwitchProfileDialog(self)
            dialog.profile_changed.connect(self.apply_profile_switch)
            dialog.exec()

        def apply_profile_switch(self, profile_name: str):
            self.parent.set_active_profile(profile_name)
            self.profile_label.setText(f"Current Profile: {profile_name}")

        class PlaylistItem(QFrame):
            clicked = Signal(dict)

            def __init__(self, parent, data, title: str = "", thumbnail_path: str | Path | None = None):
                super().__init__(parent)
                self.setObjectName("playlistItem")
                self.original_thumbnail = None

                self.layout = QHBoxLayout(self)
                self.layout.setContentsMargins(12, 10, 12, 10)
                self.layout.setSpacing(12)

                self.setMinimumHeight(92)
                self.setMaximumHeight(120)
                self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

                # item
                self.title_label = QLabel(title, self)
                self.title_label.setObjectName("playlistTitle")
                self.title_label.setWordWrap(True)
                self.title_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                self.title_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

                self.thumbnail_label = QLabel(self)
                self.thumbnail_label.setObjectName("thumbnailLabel")
                self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.thumbnail_label.setFixedSize(PlaylistSaver.THUMBNAIL_SIZE)
                self.thumbnail_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

                # bind
                self.layout.addWidget(self.title_label, 1)
                self.layout.addWidget(self.thumbnail_label)

                if thumbnail_path:
                    self.set_thumbnail(thumbnail_path)

                self.setStyleSheet("""
                    QFrame#playlistItem {
                        background-color: #2b2b2b;
                        border: 1px solid #3c3c3c;
                        border-radius: 8px;
                    }

                    QFrame#playlistItem:hover {
                        background-color: #4f0202;
                        border-color: #555555;
                    }

                    QLabel {
                        background-color: transparent;
                        border: none;
                        color: #f0f0f0;
                    }

                    #playlistTitle {
                        font-weight: 600;
                    }

                    #thumbnailLabel {
                        background-color: #1f1f1f;
                        border: 1px solid #3a3a3a;
                        border-radius: 6px;
                    }
                """)

                # Playlist data
                self.data = data

            @property
            def app(self):
                return self.parent()

            def set_thumbnail(self, thumbnail_path: str | Path):
                pixmap = QPixmap(str(thumbnail_path))
                if pixmap.isNull():
                    self.thumbnail_label.hide()
                    return

                self.original_thumbnail = pixmap
                self.thumbnail_label.show()
                self.update_thumbnail_size()

            def update_thumbnail_size(self):
                if not self.original_thumbnail:
                    return

                scaled = self.original_thumbnail.scaled(
                    self.thumbnail_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.thumbnail_label.setPixmap(scaled)

            def resizeEvent(self, event):
                super().resizeEvent(event)
                self.update_thumbnail_size()

            def mousePressEvent(self, event):
                if event.button() == Qt.MouseButton.LeftButton:
                    self.clicked.emit(self.data)
                super().mousePressEvent(event)

        class VideoItem(QFrame):
            clicked = Signal(dict)

            def __init__(self, parent, video: dict, index: int = 0, thumbnail_path: str | Path | None = None):
                super().__init__(parent)
                self.setObjectName("videoItem")
                self.original_thumbnail = None
                self.video = video
                self.id = video.get("id")
                self.url = resolve_video_page_url(video)

                title = video.get("title", "Unnamed Video")
                if index:
                    title = f"{index}. {title}"

                if video.get("duration"):
                    duration = str(datetime.timedelta(seconds=video.get("duration")))
                else:
                    duration = "Fetching duration..."

                self.layout = QHBoxLayout(self)
                self.layout.setContentsMargins(12, 10, 12, 10)
                self.layout.setSpacing(12)

                self.setMinimumHeight(92)
                self.setMaximumHeight(120)
                self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

                # item
                self.title_label = QLabel(title, self)
                self.title_label.setObjectName("videoTitle")
                self.title_label.setWordWrap(True)
                self.title_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                self.title_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

                self.duration_label = QLabel(duration, self)
                self.duration_label.setObjectName("durationLabel")
                self.duration_label.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft)

                self.download_button = QPushButton("Download", self)
                self.download_button.setObjectName("downloadButton")

                self.thumbnail_label = QLabel(self)
                self.thumbnail_label.setObjectName("thumbnailLabel")
                self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.thumbnail_label.setFixedSize(PlaylistSaver.THUMBNAIL_SIZE)
                self.thumbnail_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                self.thumbnail_label.hide()

                # bind
                self.text_layout = QVBoxLayout()
                self.text_layout.setContentsMargins(0, 0, 0, 0)
                self.text_layout.setSpacing(6)
                self.text_layout.addWidget(self.title_label, 1)
                self.text_layout.addWidget(self.duration_label)
                self.text_layout.addWidget(self.download_button, 1,
                                           Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)

                self.layout.addLayout(self.text_layout, 1)
                self.layout.addWidget(self.thumbnail_label)

                if thumbnail_path:
                    self.set_thumbnail(thumbnail_path)

                self.setStyleSheet("""
                    QFrame#videoItem {
                        background-color: #2b2b2b;
                        border: 1px solid #3c3c3c;
                        border-radius: 8px;
                    }

                    QFrame#videoItem:hover {
                        background-color: #333333;
                        border-color: #555555;
                    }

                    QLabel {
                        background-color: transparent;
                        border: none;
                        color: #f0f0f0;
                    }

                    #videoTitle {
                        font-weight: 600;
                    }

                    #thumbnailLabel {
                        background-color: #1f1f1f;
                        border: 1px solid #3a3a3a;
                        border-radius: 6px;
                    }
                    
                    #downloadButton {
                        max-height: 30px;
                    }
                """)

                self.setCursor(Qt.CursorShape.PointingHandCursor)

            def set_thumbnail(self, thumbnail_path: str | Path):
                pixmap = QPixmap(str(thumbnail_path))
                if pixmap.isNull():
                    self.thumbnail_label.hide()
                    return

                self.original_thumbnail = pixmap
                self.thumbnail_label.show()
                self.update_thumbnail_size()

            def update_thumbnail_size(self):
                if not self.original_thumbnail:
                    return

                scaled = self.original_thumbnail.scaled(
                    self.thumbnail_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.thumbnail_label.setPixmap(scaled)

            def resizeEvent(self, event):
                super().resizeEvent(event)
                self.update_thumbnail_size()

            def mousePressEvent(self, event):
                if event.button() == Qt.MouseButton.LeftButton:
                    self.clicked.emit(self.video)
                super().mousePressEvent(event)

            def set_duration(self, duration: str):
                self.duration_label.setText(duration)

        class PlaylistWindow(QDialog):
            class LoadSignals(QObject):
                playlist_ready = Signal(dict)
                error = Signal(str)

            def __init__(self, parent, playlist: dict):
                QDialog.__init__(self, parent)
                self.parent = parent
                self.playlist = playlist
                self.setWindowTitle(playlist.get("title", "Playlist"))
                self.resize(760, 640)
                self.signals = self.LoadSignals()
                self.signals.playlist_ready.connect(self.render_videos)
                self.signals.error.connect(self.show_error)

                # Layout
                self.layout = QVBoxLayout(self)
                self.layout.setContentsMargins(16, 16, 16, 16)
                self.layout.setSpacing(12)

                self.header_label = QLabel(playlist.get("title", "Playlist"), self)
                self.header_label.setObjectName("playlistTitle")
                self.header_label.setWordWrap(True)

                self.info_label = QLabel("Loading playlist videos...", self)
                self.info_label.setObjectName("playlistInfo")

                self.content_layout = QHBoxLayout()
                self.content_layout.setSpacing(12)

                # Scroll area and frame for video items
                self.scroll_area = QScrollArea(self)
                self.scroll_area.setWidgetResizable(True)
                self.scroll_area.setFrameShape(QFrame.NoFrame)

                self.list_frame = QFrame(self.scroll_area)
                self.list_frame.setObjectName("playlistFrame")
                self.list_layout = QVBoxLayout(self.list_frame)
                self.list_layout.setContentsMargins(8, 8, 8, 8)
                self.list_layout.setSpacing(8)
                self.scroll_area.setWidget(self.list_frame)

                self.control_panel = QFrame(self)
                self.control_panel.setObjectName("playlistControls")
                self.control_panel.setFixedWidth(150)
                self.control_layout = QVBoxLayout(self.control_panel)
                self.control_layout.setContentsMargins(12, 12, 12, 12)
                self.control_layout.setSpacing(10)

                self.reload_playlist_btn = QPushButton("Reload")
                self.reload_playlist_btn.setMinimumHeight(34)
                self.reload_playlist_btn.clicked.connect(self.render_playlist)

                self.control_layout.addWidget(self.reload_playlist_btn)
                self.control_layout.addStretch(1)

                self.content_layout.addWidget(self.scroll_area, 1)
                self.content_layout.addWidget(self.control_panel)

                self.layout.addWidget(self.header_label)
                self.layout.addWidget(self.info_label)
                self.layout.addLayout(self.content_layout, 1)

                self.render_playlist()

                self.setStyleSheet("""
                    QDialog {
                        background-color: #202020;
                    }

                    QScrollArea, #playlistFrame {
                        background-color: #242424;
                    }

                    #playlistTitle {
                        color: #f0f0f0;
                        font-size: 20px;
                        font-weight: 700;
                    }

                    #playlistInfo {
                        color: #a8a8a8;
                    }

                    #playlistControls {
                        background-color: #2b2b2b;
                        border: 1px solid #3c3c3c;
                        border-radius: 8px;
                    }

                    QLabel {
                        background-color: transparent;
                        color: #f0f0f0;
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

                    QPushButton:disabled {
                        color: #999999;
                        background-color: #303030;
                    }
                """)

            @property
            def app(self):
                return self.parent.parent

            def render_playlist(self):
                self.reload_playlist_btn.setEnabled(False)
                self.info_label.setText("Loading playlist videos...")
                PlaylistSaver.MainWindow.clear_layout(self.list_layout)

                threading.Thread(target=self.load_playlist_worker, daemon=True).start()

            def load_playlist_worker(self):
                try:
                    playlist_data = self.app.fetch_playlist_videos(self.playlist)
                except Exception as e:
                    logger.error("Unable to load playlist videos: %s", e)
                    self.signals.error.emit(str(e))
                    return

                if playlist_data is None:
                    playlist_data = {"entries": []}

                self.signals.playlist_ready.emit(playlist_data)

            def render_videos(self, playlist_data: dict):
                PlaylistSaver.MainWindow.clear_layout(self.list_layout)
                self.reload_playlist_btn.setEnabled(True)

                entries = [entry for entry in playlist_data.get("entries", []) if entry is not None]
                self.info_label.setText(f"{len(entries)} videos")

                if not entries:
                    self.list_layout.addWidget(QLabel("No videos found.", self.list_frame))
                    self.list_layout.addStretch(1)
                    return

                for index, video in enumerate(entries, start=1):
                    thumbnail_path = self.get_video_thumbnail_path(video)
                    item = PlaylistSaver.MainWindow.VideoItem(self.list_frame, video, index, thumbnail_path)
                    item.clicked.connect(self.parent.open_video_player)
                    self.list_layout.addWidget(item)

                self.list_layout.addStretch(1)

            def show_error(self, message: str):
                self.reload_playlist_btn.setEnabled(True)
                self.info_label.setText("Unable to load playlist.")
                QMessageBox.critical(self, "Error", f"Unable to load playlist: {message}")

            def get_video_thumbnail_path(self, video: dict):
                thumbnails = video.get("thumbnails") or []
                if not thumbnails:
                    return None

                thumbnail = thumbnails[0]
                thumbnail_url = thumbnail.get("url")
                if not thumbnail_url:
                    return None

                thumbnail_resolution = thumbnail.get("resolution", "unknown")
                video_id = video.get("id") or "unknown"
                thumbnail_path = Path(work_dir, "temp", "thumbnails",
                                      f"thumbnail_{video_id}_{thumbnail_resolution}.jpg")

                if thumbnail_path.exists():
                    return thumbnail_path

                if download_file(thumbnail_url, thumbnail_path):
                    return thumbnail_path

                return None

        class PlayerDialog(QDialog):
            status_changed = Signal(str)

            def __init__(self, parent, video: dict):
                QDialog.__init__(self, parent)
                self.parent = parent
                self.video = video
                self.video_url = resolve_video_page_url(video)
                self.process = None
                self.setWindowTitle(video.get("title", "Video"))
                self.resize(440, 190)

                self.status_changed.connect(self.set_status)

                self.layout = QVBoxLayout(self)
                self.layout.setContentsMargins(16, 16, 16, 16)
                self.layout.setSpacing(10)

                self.title_label = QLabel(video.get("title", "Video"), self)
                self.title_label.setWordWrap(True)

                self.status_label = QLabel("Launching mpv...", self)
                self.url_label = QLabel(self.video_url or "", self)
                self.url_label.setWordWrap(True)

                self.close_btn = QPushButton("Close Player", self)
                self.close_btn.clicked.connect(self.close)

                self.layout.addWidget(self.title_label)
                self.layout.addWidget(self.status_label)
                self.layout.addWidget(self.url_label)
                self.layout.addWidget(self.close_btn)

                self.setStyleSheet("""
                    QDialog {
                        background-color: #202020;
                    }

                    QLabel {
                        background-color: transparent;
                        color: #f0f0f0;
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
                """)

                threading.Thread(target=self.run_mpv, daemon=True).start()

            def set_status(self, message: str):
                self.status_label.setText(message)

            def run_mpv(self):
                mpv_path = find_mpv_path(PROGRAM_DIR)
                if not mpv_path:
                    self.status_changed.emit("mpv not found in bin/ or PATH.")
                    return

                try:
                    self.process = subprocess.Popen(
                        [
                            mpv_path,
                            # "--force-window=immediate",
                            "--focus-on=open",
                            self.video_url,
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                except Exception as e:
                    logger.error("Unable to start mpv: %s", e)
                    self.status_changed.emit(f"Unable to start mpv: {e}")
                    return

                self.parent.parent.mpv_processes[self] = self.process
                self.status_changed.emit("Playing in mpv...")
                self.process.wait()
                self.parent.parent.mpv_processes.pop(self, None)
                self.status_changed.emit("Playback finished.")

            def closeEvent(self, event):
                process = self.parent.parent.mpv_processes.pop(self, None)
                if process and process.poll() is None:
                    process.terminate()
                event.accept()

        class SwitchProfileDialog(QDialog):
            profile_changed = Signal(str)

            def __init__(self, parent):
                QDialog.__init__(self, parent)
                self.parent = parent
                self.setWindowTitle("Switch Profile")
                self.resize(420, 420)

                # Layout
                self.layout = QVBoxLayout(self)
                self.layout.setContentsMargins(16, 16, 16, 16)
                self.layout.setSpacing(12)

                # Items
                self.current_profile_label = QLabel(f"Current: {self.app.get_active_profile_name()}")
                self.current_profile_label.setWordWrap(True)
                self.current_profile_label.setObjectName("currentProfile")

                # Scroll are and frame (for profile item)
                self.scroll_area = QScrollArea(self)
                self.scroll_area.setWidgetResizable(True)
                self.scroll_area.setFrameShape(QFrame.NoFrame)

                self.list_frame = QFrame(self.scroll_area)
                self.list_frame.setObjectName("profileList")
                self.list_layout = QVBoxLayout(self.list_frame)
                self.list_layout.setContentsMargins(8, 8, 8, 8)
                self.list_layout.setSpacing(8)
                self.scroll_area.setWidget(self.list_frame)

                self.create_profile_btn = QPushButton("Create Profile")
                self.create_profile_btn.setMinimumHeight(34)
                self.create_profile_btn.clicked.connect(self.create_profile)

                self.layout.addWidget(self.current_profile_label)
                self.layout.addWidget(self.scroll_area, 1)
                self.layout.addWidget(self.create_profile_btn)

                self.render_profiles()

                self.setStyleSheet("""
                    QDialog {
                        background-color: #202020;
                    }

                    QScrollArea, #profileList {
                        background-color: #242424;
                    }

                    #currentProfile {
                        background-color: transparent;
                        color: #f0f0f0;
                        font-weight: 600;
                        padding-bottom: 4px;
                    }

                    QFrame#profileRow {
                        background-color: #2b2b2b;
                        border: 1px solid #3c3c3c;
                        border-radius: 8px;
                    }

                    QLabel {
                        background-color: transparent;
                        color: #f0f0f0;
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

                    QPushButton:disabled {
                        color: #999999;
                        background-color: #303030;
                    }
                """)

            @property
            def app(self):
                return self.parent.parent

            def clear_profiles(self):
                while self.list_layout.count():
                    item = self.list_layout.takeAt(0)
                    widget = item.widget()
                    if widget:
                        widget.deleteLater()

            class ProfileRow(QFrame):
                def __init__(self, parent, profile_name: str, active_profile: str,
                             switch_profile, delete_profile):
                    QFrame.__init__(self, parent)
                    self.setObjectName("profileRow")

                    self.row_layout = QHBoxLayout(self)
                    self.row_layout.setContentsMargins(10, 8, 10, 8)
                    self.row_layout.setSpacing(8)

                    self.name_label = QLabel(profile_name)
                    self.name_label.setWordWrap(True)

                    self.switch_btn = QPushButton("Current" if profile_name == active_profile else "Switch")
                    self.switch_btn.setMinimumWidth(88)
                    self.switch_btn.setEnabled(profile_name != active_profile)
                    self.switch_btn.clicked.connect(lambda _checked=False, name=profile_name: switch_profile(name))

                    self.delete_btn = QPushButton("Delete")
                    self.delete_btn.setMinimumWidth(88)
                    self.delete_btn.setDisabled(profile_name == "Default")
                    self.delete_btn.clicked.connect(lambda _checked=False, name=profile_name: delete_profile(name))

                    self.row_layout.addWidget(self.name_label, 1)
                    self.row_layout.addWidget(self.switch_btn)
                    self.row_layout.addWidget(self.delete_btn)

            def render_profiles(self):
                self.clear_profiles()

                meta = self.app.load_profiles_meta()
                active_profile = meta["active_profile"]

                for profile_name in meta["profiles"]:
                    row = self.ProfileRow(self, profile_name, active_profile, self.switch_profile, self.delete_profile)
                    self.list_layout.addWidget(row)

                self.list_layout.addStretch(1)

            def switch_profile(self, profile_name: str):
                self.profile_changed.emit(profile_name)
                self.accept()

            def delete_profile(self, profile_name: str):
                meta = self.app.load_profiles_meta()

                if profile_name == meta["active_profile"]:
                    QMessageBox.critical(self, "Error", "Cannot delete the active profile.")
                    return

                if profile_name == self.app.DEFAULT_PROFILE_NAME:
                    QMessageBox.critical(self, "Error", "Cannot delete the default profile.")
                    return

                reply = QMessageBox.question(
                    self,
                    "Delete Profile",
                    f"Delete profile '{profile_name}'?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )

                if reply != QMessageBox.Yes:
                    return

                meta["profiles"] = [
                    name for name in meta["profiles"]
                    if name != profile_name
                ]

                self.app.save_profiles_meta(meta)
                self.render_profiles()

            def create_profile(self):
                profile_name, ok = QInputDialog.getText(self, "Create Profile", "Profile name:")

                if not ok:
                    return

                profile_name = profile_name.strip()
                if not profile_name:
                    QMessageBox.critical(self, "Error", "Profile name cannot be empty.")
                    return

                self.app.ensure_profile_exists(profile_name)
                self.render_profiles()

    def init(self):
        self.initialize_profiles()
        # Start background thread after root is ready for UI callbacks.
        self.reload_playlists()

    def reload_playlists(self):
        self.window.loading_label.setText("Loading playlists...")

        threading.Thread(
            target=fetch_playlists_data,
            args=(self.url_schema, self.get_profile_cookie_path(self.active_profile_name), self.get_ydl_opts(), self.on_data_ready),
            daemon=True,
        ).start()

    #
    # Profile
    #
    def initialize_profiles(self):
        try:
            self.profiles_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error("Unable to create profiles folder:", e)
            QMessageBox.critical(self.window, "Error",
                                 "Unable to create profiles folder.\n"
                                 f"At path: {self.profiles_path.as_posix()}\n"
                                 f"Reason: {e}\n"
                                 f"Solution: Try to remove profiles folder manually and try again.")

        active_name = self.get_active_profile_name()
        self.ensure_profile_exists(active_name)
        self.set_active_profile(active_name)

    def ensure_profile_exists(self, profile_name: str):
        meta = self.load_profiles_meta()
        profiles = meta["profiles"]

        if profile_name not in profiles:
            profiles.append(profile_name)
            meta["profiles"] = profiles
            self.save_profiles_meta(meta)

        self.get_profile_dir(profile_name).parent.mkdir(parents=True, exist_ok=True)

    def get_active_profile_name(self):
        meta = self.load_profiles_meta()
        self.active_profile_name = meta.get("active_profile", None)
        return self.active_profile_name

    def load_profiles_meta(self):
        meta = read_json(self.profiles_meta_path)
        if not meta:
            return {
                "active_profile": self.DEFAULT_PROFILE_NAME,
                "profiles": [self.DEFAULT_PROFILE_NAME],
            }

        profiles = meta.get("profiles") or [self.DEFAULT_PROFILE_NAME]
        active_profile = meta.get("active_profile") or profiles[0]

        if active_profile not in profiles:
            profiles.insert(0, active_profile)

        return {
            "active_profile": active_profile,
            "profiles": profiles,
        }

    def set_active_profile(self, profile_name: str):
        meta = self.load_profiles_meta()
        profiles = meta["profiles"]

        if profile_name not in profiles:
            profiles.append(profile_name)

        meta["profiles"] = profiles
        meta["active_profile"] = profile_name
        self.save_profiles_meta(meta)

        self.active_profile_name = profile_name
        self.cookie_path = self.get_profile_cookie_path(profile_name)
        self.cookie_path.parent.mkdir(parents=True, exist_ok=True)

    def save_profiles_meta(self, meta: dict):
        save_json(meta, self.profiles_meta_path)

    def delete_profile(self, profile_name: str):
        meta = self.load_profiles_meta()

        if not profile_name in meta["profiles"]:
            return

        meta["profiles"].remove(profile_name)

        self.save_profiles_meta(meta)

    #
    # Path
    #
    def get_profile_cookie_path(self, profile_name: str):
        return Path(self.get_profile_dir(profile_name), "cookies.txt")

    def get_profile_dir(self, profile_name: str):
        return Path(self.profiles_path, sanitize_filename(profile_name))

    def get_profile_temp_dir(self, profile_name: str | None = None):
        profile_name = profile_name or self.active_profile_name
        return self.temp_path / sanitize_filename(profile_name)

    def get_playlist_cache_path(self, playlist_id: str):
        safe_id = sanitize_filename(playlist_id or "unknown")
        return self.get_profile_temp_dir() / "playlists" / f"{safe_id}.json"

    #
    # PlaylistData
    #
    def on_data_ready(self, data, error):
        if isinstance(error, PlaylistFetchException):
            self.window.signals.playlist_fetch_error.emit(error)
        if data is None:
            data = {"entries": []}

        self.playlists["entries"] = data.get("entries", [])
        self.window.signals.playlists_ready.emit(self.playlists)

    def load_playlist_videos(self, playlist_id: str):
        cache_path = self.get_playlist_cache_path(playlist_id)
        if os.path.exists(cache_path):
            return read_json(cache_path)
        return None

    def fetch_playlist_videos(self, playlist: dict):
        playlist_id = playlist.get("id")
        playlist_url = playlist.get("url") or playlist.get("webpage_url")

        if not playlist_url and playlist_id and not str(playlist_id).startswith("VL"):
            playlist_url = f"https://www.youtube.com/playlist?list={playlist_id}"

        if not playlist_url:
            raise ValueError("Playlist URL not found.")

        cached_data = self.load_playlist_videos(playlist_id)
        if cached_data:
            return cached_data

        opts = self.get_ydl_opts()

        opts.update({"extract_flat": True,
                     "skip_download": True,
                     "quiet": True, })

        with fetch_playlist_lock:
            with YoutubeDL(opts) as ydl:
                data = ydl.extract_info(playlist_url, download=False)

        self.save_playlist_videos(playlist_id, data)
        return data

    def save_playlist_videos(self, playlist_id: str, data: dict):
        save_json(data, self.get_playlist_cache_path(playlist_id))

    def get_ydl_opts(self):
        return {
            'cookiefile': self.get_profile_cookie_path(self.active_profile_name),
            'verbose': True,
            'download': False,
            'js_runtimes': {"node": {}},
            'extract_flat': False,
            'lazy_playlist': False,
            "ignoreerrors": False
        }


@click.command()
@click.argument("url_schema", default=None, required=False)
@click.option("--cookie-file", "-cf", required=False, default=None, help="Cookies file",
              type=click.Path(exists=True))
@click.option("--new-work-dir", "-wd", required=False, default=None, help="New working directory",
              type=click.Path(exists=True))
def main(url_schema, cookie_file: str, new_work_dir: str):
    global work_dir

    icon_path = PROGRAM_DIR / "assets" / "icon.ico"

    if new_work_dir:
        work_dir = Path(new_work_dir)

    if work_dir is None:
        app = QApplication(sys.argv)

        if icon_path.exists():
            app.setWindowIcon(QIcon(icon_path.as_posix()))

        folder = QFileDialog.getExistingDirectory(
            None, "Select a folder as the new working directory"
        )

        if folder:
            work_dir = Path(folder)
        else:
            QMessageBox.warning(
                None,
                "Warning",
                "PlaylistSaver requires a working directory to store files. Please specify a working directory"
                " using argument \"--new-work-dir\".",
            )
            sys.exit(1)

        app.shutdown()

    app = PlaylistSaver(
        url_schema,
        cookie_file,
    )
    if icon_path.exists():
        app.setWindowIcon(QIcon(icon_path.as_posix()))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
