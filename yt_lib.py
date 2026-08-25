import copy
import logging
import os
import threading
import traceback
import webbrowser
from pathlib import Path
from typing import Callable

from PySide6.QtWidgets import QMessageBox
from yt_dlp import YoutubeDL

from constant import YT_PLAYLISTS_URL
from utils import check_url_scheme, parser_url_scheme, find_http_status

logger = logging.getLogger("PLib")

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


def resolve_video_page_url(video: dict | None=None, video_id: str=None) -> str:
    if not video and not video_id:
        raise Exception("No video id provided")

    if isinstance(video_id, str):
        return f"https://www.youtube.com/watch?v={video_id}"

    return (
            video.get("webpage_url")
            or video.get("url")
            or (f"https://www.youtube.com/watch?v={video.get('id')}" if video.get("id") else None)
    )

def fetch_playlists_data(url_schema,
                         cookie_file_path: Path, opts, callback: Callable,
                         work_dir: Path, fetch_lock: threading.Lock,
                         main_filepath: Path, secret_key: str | None = None):
    check_url_scheme(main_filepath.absolute().as_posix(), work_dir)

    error = None

    if url_schema is not None:
        try:
            parser_url_scheme(url_schema, cookie_path=cookie_file_path, secret_key=secret_key)
        except Exception as e:
            logger.error("Unable to parse URL scheme: %s", e)

    playlists_data = None

    with fetch_lock:
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
        logger.error(f"Unable to fetch playlists: {error[0]}")
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
