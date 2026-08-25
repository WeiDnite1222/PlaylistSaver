import logging
import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import Signal, QObject, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QLabel, QPushButton, QProgressDialog,
    QVBoxLayout, QFrame, QHBoxLayout, QMessageBox, QInputDialog, QScrollArea
)

from constant import PROGRAM_DIR
from pl_lib.messagebox import ask_yes_no_with_plain_text
from utils import find_mpv_path, download_file
from widgets import VideoItem
from yt_lib import resolve_video_page_url


logger = logging.getLogger("PlayerDialog")
logger.setLevel(logging.INFO)


class DownloadProgressDialog(QProgressDialog):
    tasks_added = Signal(int)
    task_finished = Signal(str, bool)
    producer_finished = Signal()

    def __init__(self, title: str, total: int, parent=None, producers: int = 0):
        super().__init__("Preparing downloads...", "Hide", 0, total, parent)
        self.completed_count = 0
        self.failed_count = 0
        self.failed_videos = []
        self.remaining_producers = producers
        self.summary_shown = False
        self.setWindowTitle(title)
        self.setFixedWidth(540)
        self.setMinimumDuration(0)
        self.setAutoClose(False)
        self.setAutoReset(False)
        if total == 0:
            self.setRange(0, 0)
        else:
            self.setValue(0)
        self.tasks_added.connect(self.add_tasks)
        self.task_finished.connect(self.update_task)
        self.producer_finished.connect(self.finish_producer)

        status_label = self.findChild(QLabel)
        if status_label is not None:
            status_label.setWordWrap(True)
            status_label.setMaximumWidth(500)
        self.show()

    def add_tasks(self, count: int):
        if self.maximum() == 0:
            self.setRange(0, count)
        else:
            self.setMaximum(self.maximum() + count)
        self.setValue(self.completed_count)
        self.setLabelText(f"Downloaded {self.completed_count}/{self.maximum()} videos")

    def finish_future(self, result, video=None, playlist_name=None):
        video_title = (video or {}).get("title") or "Unknown video"
        video_id = (video or {}).get("id") or "unknown"
        display_name = f"{video_title} [{video_id}]"
        if playlist_name:
            display_name = f"{playlist_name} / {display_name}"
        if isinstance(result, Exception):
            self.task_finished.emit(f"{display_name}\n{result}", False)
            return
        try:
            path = result.result()
        except Exception as error:
            self.task_finished.emit(f"{display_name}\n{error}", False)
            return
        self.task_finished.emit(path.name, True)

    def update_task(self, message: str, succeeded: bool):
        self.completed_count += 1
        if not succeeded:
            self.failed_count += 1
            self.failed_videos.append(message)
        self.setValue(self.completed_count)
        status = f"Downloaded {self.completed_count}/{self.maximum()} videos"
        if self.failed_count:
            status += f" ({self.failed_count} failed)"
        self.setLabelText(f"{status}\n{message}")
        self.show_failure_summary_if_finished()

    def finish_producer(self):
        self.remaining_producers = max(0, self.remaining_producers - 1)
        self.show_failure_summary_if_finished()

    def show_failure_summary_if_finished(self):
        if self.summary_shown or self.remaining_producers > 0:
            return
        if self.maximum() == 0 or self.completed_count < self.maximum():
            return
        self.summary_shown = True
        if not self.failed_videos:
            return

        def show_summary():
            should_close = ask_yes_no_with_plain_text(
                self.parentWidget(),
                "Download failures",
                f"{len(self.failed_videos)} video(s) could not be downloaded. "
                "Close the download progress window?",
                "\n\n".join(self.failed_videos),
            )
            if should_close:
                self.hide()

        QTimer.singleShot(0, show_summary)

class PlayerDialog(QDialog):
    status_changed = Signal(str)

    def __init__(self, app, video: dict, /, parent):
        QDialog.__init__(self, parent)
        self.app = app
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

        self.app.mpv_processes[self] = self.process
        self.status_changed.emit("Playing in mpv...")
        self.process.wait()
        self.app.mpv_processes.pop(self, None)
        self.status_changed.emit("Playback finished.")

    def closeEvent(self, event):
        process = self.app.mpv_processes.pop(self, None)
        if process and process.poll() is None:
            process.terminate()
        event.accept()


class SwitchProfileDialog(QDialog):
    profile_changed = Signal(str)

    def __init__(self, app, /, parent):
        QDialog.__init__(self, parent)
        self.app = app
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
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)

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
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply != QMessageBox.StandardButton.Yes:
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


class PlaylistWindow(QDialog):
    class LoadSignals(QObject):
        playlist_ready = Signal(dict)
        error = Signal(str)
        download_status = Signal(str)

    def __init__(self, app, playlist: dict, /, parent=None,
                 open_player_callback=None):
        QDialog.__init__(self, parent)
        self.app = app
        self.playlist = playlist
        self.setWindowTitle(playlist.get("title", "Playlist"))
        self.resize(760, 640)
        self.signals = self.LoadSignals()
        self.signals.playlist_ready.connect(self.render_videos)
        self.signals.error.connect(self.show_error)

        self.open_player_callback = open_player_callback

        # Layout
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(16, 16, 16, 16)
        self.layout.setSpacing(12)

        self.header_label = QLabel(playlist.get("title", "Playlist"), self)
        self.header_label.setObjectName("playlistTitle")
        self.header_label.setWordWrap(True)

        self.info_label = QLabel("Loading playlist videos...", self)
        self.info_label.setObjectName("playlistInfo")
        self.signals.download_status.connect(self.info_label.setText)

        self.content_layout = QHBoxLayout()
        self.content_layout.setSpacing(12)

        # Scroll area and frame for video items
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)

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
        self.save_playlist_btn = QPushButton("Save Playlist")
        self.reload_playlist_btn.setMinimumHeight(34)
        self.reload_playlist_btn.clicked.connect(self.render_playlist)
        self.save_playlist_btn.clicked.connect(self.save_playlist)

        self.control_layout.addWidget(self.reload_playlist_btn)
        self.control_layout.addWidget(self.save_playlist_btn)
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

    def render_playlist(self):
        self.reload_playlist_btn.setEnabled(False)
        self.info_label.setText("Loading playlist videos...")
        self.app.main_window.clear_layout(self.list_layout)

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
        self.playlist_data = playlist_data
        self.app.main_window.clear_layout(self.list_layout)
        self.reload_playlist_btn.setEnabled(True)

        entries = [entry for entry in playlist_data.get("entries", []) if entry is not None]
        self.info_label.setText(f"{len(entries)} videos")

        if not entries:
            self.list_layout.addWidget(QLabel("No videos found.", self.list_frame))
            self.list_layout.addStretch(1)
            return

        for index, video in enumerate(entries, start=1):
            thumbnail_path = self.get_video_thumbnail_path(video)
            item = VideoItem(
                self.list_frame, video, index, thumbnail_path,
                thumbnail_size=self.app.THUMBNAIL_SIZE,
                download_video_callback=lambda _checked=False, current=video: self.download_video(current),
            )
            item.clicked.connect(self.open_player_callback)
            self.list_layout.addWidget(item)

        self.list_layout.addStretch(1)

    def save_playlist(self):
        data = getattr(self, "playlist_data", None)
        total = len([entry for entry in data.get("entries", []) if entry]) if data else 0
        self.progress_dialog = DownloadProgressDialog(
            "Save Playlist", total, self.app.main_window, producers=1
        )
        queued_callback = None if data else self.progress_dialog.tasks_added.emit
        self.app.download_playlist(
            self.playlist, data, self.progress_dialog.finish_future, queued_callback,
            self.progress_dialog.producer_finished.emit,
        )

    def download_video(self, video: dict):
        self.progress_dialog = DownloadProgressDialog("Download Video", 1, self.app.main_window)
        self.app.download_video(video, callback=self.progress_dialog.finish_future)

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
        thumbnail_path = Path(self.app.temp_path, "thumbnails",
                              f"thumbnail_{video_id}_{thumbnail_resolution}.jpg")

        if thumbnail_path.exists():
            return thumbnail_path

        if download_file(thumbnail_url, thumbnail_path):
            return thumbnail_path

        return None
