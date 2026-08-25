import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtWidgets import QLabel, QFrame, QHBoxLayout, QSizePolicy, QPushButton, QVBoxLayout
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QPixmap

from yt_lib import resolve_video_page_url


class PlaylistItem(QFrame):
    clicked = Signal(dict)

    def __init__(self,
                 parent,
                 data,
                 title: str = "",
                 thumbnail_path: str | Path | None = None,
                 thumbnail_size: QSize = None):
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
        self.thumbnail_label.setFixedSize(thumbnail_size)
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

    def __init__(self, parent, video: dict,
                 index: int = 0, thumbnail_path: str | Path | None = None,
                 thumbnail_size: QSize = None,
                 download_video_callback: Callable = None):
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

        self.download_button = QPushButton("Download Video", self)
        self.download_button.setObjectName("downloadButton")

        self.thumbnail_label = QLabel(self)
        self.thumbnail_label.setObjectName("thumbnailLabel")
        self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail_label.setFixedSize(thumbnail_size)
        self.thumbnail_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.thumbnail_label.hide()

        # bind
        self.text_layout = QVBoxLayout()
        self.text_layout.setContentsMargins(0, 0, 0, 0)
        self.text_layout.setSpacing(6)
        self.text_layout.addWidget(self.title_label, 1)
        self.text_layout.addWidget(self.duration_label)
        self.text_layout.addWidget(self.download_button, 1,
                                   Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft)

        self.layout.addLayout(self.text_layout, 1)
        self.layout.addWidget(self.thumbnail_label)

        if callable(download_video_callback):
            self.download_button.clicked.connect(download_video_callback)

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
