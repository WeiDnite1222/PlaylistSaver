# Qt
import logging
import os
import secrets
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QSize

from yt_dlp import YoutubeDL

from constant import PROGRAM_DIR
from window import PlaylistMainWindow
from yt_lib import sanitize_filename, fetch_playlists_data, PlaylistFetchException, resolve_video_page_url
from utils import save_json, read_json
from pl_lib import messagebox

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class PlaylistSaver(QApplication):
    DEFAULT_PROFILE_NAME = "default"
    THUMBNAIL_SIZE = QSize(144, 81)
    mpv_processes = {}

    def __init__(self, work_dir: Path, url_schema=None, cookie_file: str | Path | None = None):
        super().__init__()
        self.setApplicationName("PlaylistSaver")

        self.url_schema = url_schema
        self.cookie_file = Path(cookie_file) if cookie_file else None

        # Path
        self.work_dir = work_dir
        self.save_path = Path( self.work_dir, "save")
        self.temp_path = Path( self.work_dir, "temp")
        self.profiles_meta_path = Path(self.save_path, "profiles.json")
        self.profiles_path = Path(self.save_path, "profiles")
        self.cookie_path = Path(self.profiles_path, self.DEFAULT_PROFILE_NAME, "cookies.txt")
        self.secret_key_path = Path(self.save_path, "helper-secret.key")
        self.secret_key = self.load_or_create_secret_key()

        # Profile
        self.active_profile_name = self.DEFAULT_PROFILE_NAME

        # Data
        self.playlists: dict[str, list] = {
            "entries": []
        }

        self.main_window = PlaylistMainWindow(
            self
        )

        # Lock
        self.fetch_playlist_lock = threading.Lock()
        self.download_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="video-download")
        self.download_tasks = set()
        self.download_tasks_lock = threading.Lock()
        self.download_cancel_event = threading.Event()
        self.aboutToQuit.connect(lambda: self.download_pool.shutdown(wait=False, cancel_futures=False))

        # flags
        self.use_custom_cookie_file_path = self.cookie_file is not None

        if self.use_custom_cookie_file_path:
            cookie_path = self.cookie_file

            if not cookie_path.exists() or not cookie_path.is_file():
                self.main_window.signals.error.emit(
                    "Cookie file does not exist or is not a file."
                )
            else:
                self.cookie_path = cookie_path

        self.init()
        self.main_window.show()

    def init(self):
        self.initialize_profiles()
        # Start background thread after root is ready for UI callbacks.
        self.reload_playlists()

    def reload_playlists(self):
        self.main_window.loading_label.setText("Loading playlists...")

        threading.Thread(
            target=fetch_playlists_data,
            args=(self.url_schema,
                  self.get_profile_cookie_path(self.active_profile_name),
                  self.get_ydl_opts(),
                  self.on_data_ready,
                  self.work_dir,
                  self.fetch_playlist_lock,
                  PROGRAM_DIR / "main.py",
                  self.secret_key,
                  ),
            daemon=True,
        ).start()

    #
    # Profile
    #
    def load_or_create_secret_key(self):
        try:
            if self.secret_key_path.exists():
                key = self.secret_key_path.read_text(encoding="utf-8").strip()
                if key:
                    return key

            self.secret_key_path.parent.mkdir(parents=True, exist_ok=True)
            key = secrets.token_urlsafe(32)
            self.secret_key_path.write_text(key, encoding="utf-8")
            return key
        except OSError as e:
            logger.error("Unable to load or create Helper secret key: %s", e)
            return None

    def initialize_profiles(self):
        try:
            self.profiles_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error("Unable to create profiles folder:", e)
            QMessageBox.critical(self.main_window, "Error",
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
        if self.use_custom_cookie_file_path:
            return self.cookie_file

        return Path(self.get_profile_dir(profile_name), "cookies.txt")

    def get_profile_dir(self, profile_name: str):
        return Path(self.profiles_path, sanitize_filename(profile_name))

    def get_profile_temp_dir(self, profile_name: str | None = None):
        profile_name = profile_name or self.active_profile_name
        return self.temp_path / "profiles" / sanitize_filename(profile_name)

    def get_playlist_cache_path(self, playlist_id: str):
        safe_id = sanitize_filename(playlist_id or "unknown")
        return self.get_profile_temp_dir() / "playlists" / f"{safe_id}.json"

    #
    # PlaylistData
    #
    def on_data_ready(self, data, error):
        if isinstance(error, PlaylistFetchException):
            self.main_window.signals.playlist_fetch_error.emit(error)
        if data is None:
            data = {"entries": []}

        self.playlists["entries"] = data.get("entries", [])
        self.main_window.signals.playlists_ready.emit(self.playlists)

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

        with self.fetch_playlist_lock:
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

    def get_download_dir(self, playlist_name: str | None = None):
        directory = self.temp_path / "downloads"
        if playlist_name:
            safe_playlist_name = sanitize_filename(playlist_name).strip(" .") or "Unnamed Playlist"
            directory = directory / "playlists" / safe_playlist_name
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _download_video_worker(self, video: dict, playlist_name: str | None = None):
        video_id = str(video.get("id") or "unknown")
        video_url = resolve_video_page_url(video, video_id)
        video_name = sanitize_filename(str(video.get("title") or "Unnamed Video")).strip(" .") or "Unnamed Video"
        output_base = self.get_download_dir(playlist_name) / f"{video_name}-{sanitize_filename(video_id)}"

        opts = self.get_ydl_opts()
        opts.update({
            "download": True,
            "skip_download": False,
            "quiet": True,
            "noplaylist": True,
            "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
            "merge_output_format": "mp4",
            "outtmpl": f"{output_base}.%(ext)s",
            "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}],
            "progress_hooks": [self._check_download_cancelled],
        })
        with YoutubeDL(opts) as ydl:
            ydl.download([video_url])
        return Path(f"{output_base}.mp4")

    def _check_download_cancelled(self, _status):
        if self.download_cancel_event.is_set():
            raise RuntimeError("Download cancelled because the application is closing.")

    def _submit_download_task(self, function, *args):
        future = self.download_pool.submit(function, *args)
        with self.download_tasks_lock:
            self.download_tasks.add(future)
        future.add_done_callback(self._forget_download_task)
        return future

    def _forget_download_task(self, future):
        with self.download_tasks_lock:
            self.download_tasks.discard(future)

    def has_active_download_tasks(self):
        with self.download_tasks_lock:
            return any(not future.done() for future in self.download_tasks)

    def cancel_download_tasks(self):
        self.download_cancel_event.set()
        with self.download_tasks_lock:
            for future in self.download_tasks:
                future.cancel()

    def download_video(self, video: dict, playlist_name: str | None = None,
                       callback=None, reset_cancel=True):
        if reset_cancel:
            self.download_cancel_event.clear()
        future = self._submit_download_task(self._download_video_worker, video, playlist_name)
        if callback:
            future.add_done_callback(
                lambda result: callback(result, video, playlist_name)
            )
        return future

    def download_playlist(self, playlist: dict, playlist_data: dict | None = None,
                          callback=None, queued_callback=None, producer_callback=None,
                          reset_cancel=True):
        def queue_videos():
            try:
                data = playlist_data or self.fetch_playlist_videos(playlist)
                entries = [entry for entry in data.get("entries", []) if entry]
                if self.download_cancel_event.is_set():
                    return
                if queued_callback:
                    queued_callback(len(entries))
                title = str(playlist.get("title") or playlist.get("id") or "Unnamed Playlist")
                for video in entries:
                    if self.download_cancel_event.is_set():
                        break
                    self.download_video(video, title, callback, reset_cancel=False)
            except Exception as error:
                if queued_callback:
                    queued_callback(1)
                if callback:
                    callback(error, None, playlist.get("title"))
            finally:
                if producer_callback:
                    producer_callback()

        if reset_cancel:
            self.download_cancel_event.clear()
        return self._submit_download_task(queue_videos)

    def download_all_playlists(self, callback=None, queued_callback=None,
                               producer_callback=None):
        self.download_cancel_event.clear()
        for playlist in (item for item in self.playlists.get("entries", []) if item):
            self.download_playlist(
                playlist, callback=callback, queued_callback=queued_callback,
                producer_callback=producer_callback, reset_cancel=False
            )

    def open_download_folder(self):
        download_dir = self.temp_path / "downloads"

        if not download_dir.is_dir():
            messagebox.error(self.main_window, title="Error", message="The download folder does not exist.")
            return

        webbrowser.open(download_dir.as_posix())
