import base64
import binascii
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from tkinter import messagebox, simpledialog
from typing import Callable

from PIL import Image, ImageTk
import customtkinter as ctk
import urllib
from urllib.parse import unquote
import click
import requests
import winreg
from yt_dlp import YoutubeDL

VERSION = "Prototype"
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

save_path = os.path.join(ROOT_DIR, "save")
temp_path = os.path.join(ROOT_DIR, "temp")
profiles_path = os.path.join(save_path, "profiles")
profiles_meta_path = os.path.join(save_path, "profiles.json")
DEFAULT_PROFILE_NAME = "default"
active_profile_name = DEFAULT_PROFILE_NAME
cookie_path = os.path.join(profiles_path, DEFAULT_PROFILE_NAME, "cookies.txt")

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

fetch_playlist_lock = threading.Lock()
mpv_processes = {}
download_playlists_lock = threading.Lock()

def key_exists(hive, sub_key):
    try:
        # Attempt to open the key for reading
        with winreg.OpenKey(hive, sub_key, 0, winreg.KEY_READ) as key:
            return True
    except FileNotFoundError:
        return False

def create_url_scheme():
    try:
        scheme = "PlaylistSaver"
        base = r"Software\Classes\\" + scheme

        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, base)

        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, f"URL:{scheme} Protocol")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")

        shell_key = winreg.CreateKey(key, "shell")
        open_key = winreg.CreateKey(shell_key, "open")
        command_key = winreg.CreateKey(open_key, "command")

        if getattr(sys, 'frozen', False):
            command = f"\"{sys.executable}\" \"%1\""
        else:
            command = f"\"{sys.executable}\" \"{os.path.abspath(__file__)}\" \"%1\""

        winreg.SetValueEx(command_key, None, 0, winreg.REG_SZ, command)

        logger.info("URL scheme registered.")

    except OSError as e:
        logger.error("Unable to create URL scheme:", e)

def check_url_scheme():
    try:
        if not key_exists(winreg.HKEY_LOCAL_MACHINE, "PlaylistSaver"):
            create_url_scheme()
    except FileNotFoundError:
        create_url_scheme()

def read_cookies():
    try:
        with open(cookie_path, "r", encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        logger.error("Cookies file not found.")
    except Exception as e:
        logger.error("An unexpected error occurred while reading cookies:", e)

def load_profiles_meta():
    meta = read_json(profiles_meta_path)
    if not meta:
        return {
            "active_profile": DEFAULT_PROFILE_NAME,
            "profiles": [DEFAULT_PROFILE_NAME],
        }

    profiles = meta.get("profiles") or [DEFAULT_PROFILE_NAME]
    active_profile = meta.get("active_profile") or profiles[0]

    if active_profile not in profiles:
        profiles.insert(0, active_profile)

    return {
        "active_profile": active_profile,
        "profiles": profiles,
    }

def save_profiles_meta(meta: dict):
    save_json(meta, profiles_meta_path)

def get_profile_dir(profile_name: str):
    return os.path.join(profiles_path, sanitize_filename(profile_name))

def get_profile_cookie_path(profile_name: str):
    return os.path.join(get_profile_dir(profile_name), "cookies.txt")

def get_active_profile_name():
    global active_profile_name

    meta = load_profiles_meta()
    active_profile_name = meta["active_profile"]
    return active_profile_name

def set_active_profile(profile_name: str):
    global active_profile_name, cookie_path

    meta = load_profiles_meta()
    profiles = meta["profiles"]

    if profile_name not in profiles:
        profiles.append(profile_name)

    meta["profiles"] = profiles
    meta["active_profile"] = profile_name
    save_profiles_meta(meta)

    active_profile_name = profile_name
    cookie_path = get_profile_cookie_path(profile_name)
    os.makedirs(os.path.dirname(cookie_path), exist_ok=True)

def ensure_profile_exists(profile_name: str):
    meta = load_profiles_meta()
    profiles = meta["profiles"]

    if profile_name not in profiles:
        profiles.append(profile_name)
        meta["profiles"] = profiles
        save_profiles_meta(meta)

    os.makedirs(get_profile_dir(profile_name), exist_ok=True)

def initialize_profiles():
    os.makedirs(profiles_path, exist_ok=True)
    active_name = get_active_profile_name()
    ensure_profile_exists(active_name)
    set_active_profile(active_name)

def get_cookie_owner():
    ydl_opts = {
        'cookiefile': cookie_path,
        'verbose': True,
    }

    with YoutubeDL(ydl_opts) as ydl:
        data = ydl.extract_info(
            "https://www.youtube.com/feed/subscriptions",
            download=False
        )

    print("Cookie owner: ", json.dumps(data, indent=2))

def save_cookies(cookies):
    try:
        os.makedirs(os.path.dirname(cookie_path), exist_ok=True)
        with open(cookie_path, "w", encoding='utf-8') as f:
            f.write(cookies)
    except Exception as e:
        logger.error("An unexpected error occurred while saving cookies:", e)

def cookie_string_to_netscape(cookie_str):
    lines = ["# Netscape HTTP Cookie File"]

    for pair in cookie_str.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue

        name, value = pair.split("=", 1)  # ← 關鍵修正

        lines.append("\t".join([
            ".youtube.com",
            "TRUE",
            "/",
            "TRUE",
            "9999999999",
            name.strip(),
            value.strip()
        ]))

    return "\n".join(lines)

def pause():
    input("Press enter to continue...")

def save_base64_netscape_cookie(cookies_encoded):
    try:
        cookies = base64.b64decode(cookies_encoded).decode("utf-8")
    except binascii.Error:
        print("Unable to decode base64 cookie:", cookies_encoded)
        return None

    print("==== COOKIE FILE ====")
    for line in cookies.split("\n"):
        print(line, "=>", len(line.split("\t")))
    print("=====================")

    save_cookies(cookies)

def parser_url_scheme(url):
    print("Parsing URL scheme:", url)

    url = urllib.parse.urlparse(unquote(sys.argv[1]))
    parameters = urllib.parse.parse_qs(url.query)

    func_map = {
        "base64cookies": save_base64_netscape_cookie,
    }

    for arg_name in parameters:
        match_func = func_map.get(arg_name, None)
        if match_func:
            match_func(parameters[arg_name][0])
        else:
            print("Unknown parameter:", arg_name, " Value:", parameters[arg_name])

def save_json(data: dict, dest):
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error("Unable to save json:", e)

def read_json(path):
    try:
        with open(path, "r", encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error("Unable to read json:", e)


def load_playlists():
    playlists_path = get_profile_playlists_cache_path()

    if not os.path.exists(playlists_path):
        logger.error("Playlists file not found.")
        return None

    return read_json(playlists_path)

def get_ydl_opts():
    return {
        'cookiefile': cookie_path,
        'verbose': True,
        'download': False,
        'js_runtimes': {"node": {}},
        'extract_flat': False,
        'lazy_playlist': False,
        "ignoreerrors": True
    }

def get_profile_temp_dir(profile_name: str | None = None):
    profile_name = profile_name or active_profile_name
    return os.path.join(temp_path, sanitize_filename(profile_name))

def get_profile_playlists_cache_path(profile_name: str | None = None):
    return os.path.join(get_profile_temp_dir(profile_name), "cache-playlists.json")

def sanitize_filename(value: str):
    invalid_chars = '<>:"/\\|?*'
    return "".join("_" if char in invalid_chars else char for char in value)

def get_playlist_cache_path(playlist_id: str):
    safe_id = sanitize_filename(playlist_id or "unknown")
    return os.path.join(get_profile_temp_dir(), "playlists", f"{safe_id}.json")

def get_playlist_batch_progress_path():
    return os.path.join(get_profile_temp_dir(), "playlist-download-progress.json")

def load_playlist_batch_progress():
    progress_path = get_playlist_batch_progress_path()
    if os.path.exists(progress_path):
        return read_json(progress_path)

    return {
        "profile": get_active_profile_name(),
        "completed_ids": [],
        "failed": [],
        "last_index": -1,
        "updated_at": None,
    }

def save_playlist_batch_progress(progress: dict):
    progress["profile"] = get_active_profile_name()
    progress["updated_at"] = int(time.time())
    save_json(progress, get_playlist_batch_progress_path())

def load_playlist_videos(playlist_id: str):
    cache_path = get_playlist_cache_path(playlist_id)
    if os.path.exists(cache_path):
        return read_json(cache_path)
    return None

def save_playlist_videos(playlist_id: str, data: dict):
    save_json(data, get_playlist_cache_path(playlist_id))

def fetch_playlist_videos(playlist: dict):
    playlist_id = playlist.get("id")
    playlist_url = playlist.get("url") or playlist.get("webpage_url")

    if not playlist_url and playlist_id and not str(playlist_id).startswith("VL"):
        playlist_url = f"https://www.youtube.com/playlist?list={playlist_id}"

    if not playlist_url:
        raise ValueError("Playlist URL not found.")

    cached_data = load_playlist_videos(playlist_id)
    if cached_data:
        return cached_data

    opts = get_ydl_opts()

    opts.update({"extract_flat": True,})

    with fetch_playlist_lock:
        with YoutubeDL(get_ydl_opts()) as ydl:
            data = ydl.extract_info(playlist_url, download=False)

    save_playlist_videos(playlist_id, data)
    return data

def resolve_video_page_url(video: dict):
    return (
        video.get("webpage_url")
        or video.get("url")
        or (f"https://www.youtube.com/watch?v={video.get('id')}" if video.get("id") else None)
    )

def resolve_mpv_path():
    local_candidates = [
        os.path.join(ROOT_DIR, "bin", "mpv.exe"),
        os.path.join(ROOT_DIR, "bin", "mpv.com"),
        os.path.join(ROOT_DIR, "bin", "mpv"),
    ]

    for candidate in local_candidates:
        if os.path.exists(candidate):
            return candidate

    # failback to PATH mpv (if available)
    return shutil.which("mpv")

def play_video_with_mpv(video_url: str, window: ctk.CTkToplevel, status_label: ctk.CTkLabel):
    mpv_path = resolve_mpv_path()

    if not mpv_path:
        window.after(0, lambda: status_label.configure(text="mpv not found in bin/ or PATH."))
        return

    try:
        process = subprocess.Popen(
            [mpv_path, video_url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        logger.error("Unable to start mpv: %s", e)
        window.after(0, lambda: status_label.configure(text=f"Unable to start mpv: {e}"))
        return

    mpv_processes[window] = process
    window.after(0, lambda: status_label.configure(text="Playing in mpv..."))

    process.wait()

    def on_finish():
        mpv_processes.pop(window, None)
        if window.winfo_exists():
            status_label.configure(text="Playback finished.")

    window.after(0, on_finish)

def download_file(url, dest):
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with requests.get(url, stream=True) as r:
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        return True
    except Exception as e:
        logger.error("Unable to download file:", e)
        return False

def background(url_schema, cookie_file: str, callback: Callable):
    global cookie_path
    check_url_scheme()
    initialize_profiles()

    if url_schema is not None:
        try:
            parser_url_scheme(url_schema)
        except Exception as e:
            logger.error("Unable to parse URL scheme:", e)
            input("Press enter to continue...")

    if cookie_file is not None:
        cookie_path = cookie_file

    data = load_playlists()

    if not data:
        # messagebox.showerror("Error", "No playlists found.")

        with fetch_playlist_lock:
            try:
                with YoutubeDL(get_ydl_opts()) as ydl:
                    data = ydl.extract_info("https://www.youtube.com/feed/playlists", download=False)
            except Exception as e:
                print("An error occurred:", e)
                pause()
                sys.exit(-1)

    # print("=== YOUTUBE DATA ===")
    # print(json.dumps(data, indent=4))
    # print("=== YOUTUBE DATA ===")

    save_json(data, get_profile_playlists_cache_path())

    callback(data)

@click.command()
@click.argument("url_schema", default=None, required=False)
@click.option("--cookie-file", "-cf", required=False, default=None, help="Cookies file",
              type=click.Path(exists=True))
def main(url_schema, cookie_file: str):
    initialize_profiles()
    current_playlists_data = {"entries": []}

    def on_data_ready(data):
        current_playlists_data["entries"] = data.get("entries", [])
        root.after(0, lambda: build_ui(data))

    # CTk
    root = ctk.CTk()
    root.geometry("600x800")
    root.title("PlaylistSaver {}".format(VERSION))

    # Layout
    playlist_frame = ctk.CTkScrollableFrame(root)
    playlist_frame.pack(fill="both", expand=True)

    operate_frame = ctk.CTkFrame(root)
    operate_frame.pack(fill="x", anchor="s")

    profile_label = ctk.CTkLabel(operate_frame, text=f"Current Profile: {get_active_profile_name()}")
    profile_label.pack(fill="x", padx=10, pady=(10, 5))

    switch_profile_btn = ctk.CTkButton(operate_frame, text="Switch Profile")
    switch_profile_btn.pack(fill="x", padx=10, pady=(0, 10))

    download_status_label = ctk.CTkLabel(operate_frame, text="Playlist batch download idle.")
    download_status_label.pack(fill="x", padx=10, pady=(0, 5))

    batch_download_btn = ctk.CTkButton(operate_frame, text="Batch Download Playlists")
    batch_download_btn.pack(fill="x", padx=10, pady=(0, 10))

    loading_label = ctk.CTkLabel(playlist_frame, text="Loading playlists...")
    loading_label.pack(pady=20)

    # Bind specified callback func
    def bind_click_recursive(widget, callback):
        widget.bind("<Button-1>", callback)
        for child in widget.winfo_children():
            bind_click_recursive(child, callback)

    def refresh_profile_label():
        profile_label.configure(text=f"Current Profile: {get_active_profile_name()}")

    def update_download_status(message: str):
        root.after(0, lambda: download_status_label.configure(text=message))

    def reload_playlists(current_url_schema=None, current_cookie_file=None):
        for widget in playlist_frame.winfo_children():
            widget.destroy()

        ctk.CTkLabel(
            playlist_frame,
            text=f"Loading playlists for {get_active_profile_name()}...",
        ).pack(pady=20)

        threading.Thread(
            target=background,
            args=(current_url_schema, current_cookie_file, on_data_ready),
            daemon=True,
        ).start()

    def switch_to_profile(profile_name: str, window: ctk.CTkToplevel | None = None):
        set_active_profile(profile_name)
        refresh_profile_label()
        if window and window.winfo_exists():
            window.destroy()
        reload_playlists()

    def open_switch_profile_window():
        profile_window = ctk.CTkToplevel(root)
        profile_window.title("Switch Profile")
        profile_window.geometry("420x420")

        ctk.CTkLabel(
            profile_window,
            text=f"Active: {get_active_profile_name()}",
        ).pack(anchor="w", padx=15, pady=(15, 10))

        list_frame = ctk.CTkScrollableFrame(profile_window)
        list_frame.pack(fill="both", expand=True, padx=15, pady=(0, 10))

        def render_profiles():
            for widget in list_frame.winfo_children():
                widget.destroy()

            meta = load_profiles_meta()
            active_name = meta["active_profile"]

            for profile_name in meta["profiles"]:
                profile_item = ctk.CTkFrame(list_frame, border_width=1, border_color="#666666")
                profile_item.pack(fill="x", padx=5, pady=5)

                ctk.CTkLabel(
                    profile_item,
                    text=profile_name,
                ).pack(side="left", padx=10, pady=10)

                button_text = "Current" if profile_name == active_name else "Switch"
                button_state = "disabled" if profile_name == active_name else "normal"

                ctk.CTkButton(
                    profile_item,
                    text=button_text,
                    width=90,
                    state=button_state,
                    command=lambda current_profile=profile_name: switch_to_profile(current_profile, profile_window),
                ).pack(side="right", padx=10, pady=10)

        def create_profile():
            new_profile_name = simpledialog.askstring(
                "Create Profile",
                "Profile name:",
                parent=profile_window,
            )

            if not new_profile_name:
                return

            new_profile_name = new_profile_name.strip()
            if not new_profile_name:
                messagebox.showerror("Error", "Profile name cannot be empty.")
                return

            ensure_profile_exists(new_profile_name)
            render_profiles()

        ctk.CTkButton(
            profile_window,
            text="Create Profile",
            command=create_profile,
        ).pack(fill="x", padx=15, pady=(0, 15))

        render_profiles()

    switch_profile_btn.configure(command=open_switch_profile_window)

    def start_batch_download():
        playlists = current_playlists_data.get("entries") or []

        if not playlists:
            messagebox.showerror("Error", "No playlists loaded yet.")
            return

        batch_size = simpledialog.askinteger(
            "Batch Download",
            "Batch size:",
            parent=root,
            initialvalue=5,
            minvalue=1,
        )

        if batch_size is None:
            return

        progress = load_playlist_batch_progress()
        completed_ids = set(progress.get("completed_ids", []))
        pending_playlists = [playlist for playlist in playlists if playlist.get("id") not in completed_ids]

        if not pending_playlists:
            if not messagebox.askyesno("Batch Download", "All playlists are already cached. Download again from scratch?"):
                return

            progress = {
                "profile": get_active_profile_name(),
                "completed_ids": [],
                "failed": [],
                "last_index": -1,
                "updated_at": None,
            }
            save_playlist_batch_progress(progress)
            pending_playlists = playlists

        batch_download_btn.configure(state="disabled")
        update_download_status(f"Starting batch download ({len(pending_playlists)} playlists pending)...")

        def worker():
            with download_playlists_lock:
                progress_data = load_playlist_batch_progress()
                completed = set(progress_data.get("completed_ids", []))
                failed_entries = progress_data.get("failed", [])

                for batch_start in range(0, len(playlists), batch_size):
                    batch = playlists[batch_start:batch_start + batch_size]
                    batch_index = (batch_start // batch_size) + 1
                    total_batches = (len(playlists) + batch_size - 1) // batch_size
                    update_download_status(f"Downloading batch {batch_index}/{total_batches}...")

                    for index_in_batch, playlist in enumerate(batch, start=batch_start):
                        playlist_id = playlist.get("id", "unknown")
                        playlist_title = playlist.get("title", playlist_id)

                        if playlist_id in completed:
                            progress_data["last_index"] = index_in_batch
                            save_playlist_batch_progress(progress_data)
                            continue

                        update_download_status(f"Saving playlist {index_in_batch + 1}/{len(playlists)}: {playlist_title}")

                        try:
                            fetch_playlist_videos(playlist)
                            completed.add(playlist_id)
                            progress_data["completed_ids"] = list(completed)
                            progress_data["failed"] = [item for item in failed_entries if item.get("id") != playlist_id]
                        except Exception as e:
                            logger.error("Batch download failed for %s: %s", playlist_title, e)
                            failed_entries = [item for item in failed_entries if item.get("id") != playlist_id]
                            failed_entries.append({
                                "id": playlist_id,
                                "title": playlist_title,
                                "error": str(e),
                            })
                            progress_data["failed"] = failed_entries
                        finally:
                            progress_data["last_index"] = index_in_batch
                            save_playlist_batch_progress(progress_data)

                failed_count = len(progress_data.get("failed", []))
                finished_message = (
                    f"Batch download finished. Success: {len(progress_data.get('completed_ids', []))}, "
                    f"Failed: {failed_count}"
                )
                update_download_status(finished_message)
                root.after(0, lambda: batch_download_btn.configure(state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    batch_download_btn.configure(command=start_batch_download)

    # Start background thread after root is ready for UI callbacks.
    reload_playlists(url_schema, cookie_file)

    def close_video_window(video_window):
        process = mpv_processes.pop(video_window, None)
        if process and process.poll() is None:
            process.terminate()
        if video_window.winfo_exists():
            video_window.destroy()

    def open_video_player(video: dict):
        video_url = resolve_video_page_url(video)
        video_title = video.get("title", "Video not found")

        if not video_url:
            messagebox.showerror("Error", "Unable to resolve video URL.")
            return

        player_window = ctk.CTkToplevel(root)
        player_window.title(video_title)
        player_window.geometry("420x180")

        ctk.CTkLabel(
            player_window,
            text=video_title,
            wraplength=360,
            justify="left",
        ).pack(anchor="w", padx=15, pady=(15, 10))

        status_label = ctk.CTkLabel(player_window, text="Launching mpv...")
        status_label.pack(anchor="w", padx=15, pady=(0, 10))

        ctk.CTkLabel(
            player_window,
            text=video_url,
            wraplength=360,
            justify="left",
        ).pack(anchor="w", padx=15, pady=(0, 15))

        close_button = ctk.CTkButton(
            player_window,
            text="Close Player",
            command=lambda: close_video_window(player_window),
        )
        close_button.pack(fill="x", padx=15, pady=(0, 15))

        player_window.protocol("WM_DELETE_WINDOW", lambda: close_video_window(player_window))

        threading.Thread(
            target=play_video_with_mpv,
            args=(video_url, player_window, status_label),
            daemon=True,
        ).start()

    def open_playlist_window(playlist: dict):
        playlist_title = playlist.get("title", "Playlist not found")
        # playlist_id = playlist.get("id", "unknown")

        playlist_window = ctk.CTkToplevel(root)
        playlist_window.title(playlist_title)
        playlist_window.geometry("700x600")

        header_label = ctk.CTkLabel(playlist_window, text=playlist_title)
        header_label.pack(anchor="w", padx=15, pady=(15, 10))

        info_label = ctk.CTkLabel(playlist_window, text="Loading playlist videos...")
        info_label.pack(anchor="w", padx=15, pady=(0, 10))

        video_frame = ctk.CTkScrollableFrame(playlist_window)
        video_frame.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        def render_videos(playlist_data):
            for widget in video_frame.winfo_children():
                widget.destroy()

            entries = playlist_data.get("entries") or []
            info_label.configure(text=f"{len(entries)} videos")

            if not entries:
                ctk.CTkLabel(video_frame, text="No videos found.").pack(anchor="w", padx=10, pady=10)
                return

            entries = [entry for entry in entries if entry is not None]

            for index, video in enumerate(entries, start=1):
                video_title = video.get("title", "Video not found")
                duration = video.get("duration_string") or "Unknown duration"

                video_item = ctk.CTkFrame(video_frame, border_width=1, border_color="#666666")
                video_item.pack(fill="x", padx=5, pady=5)

                ctk.CTkLabel(
                    video_item,
                    text=f"{index}. {video_title}",
                    wraplength=600,
                    justify="left",
                ).pack(anchor="w", padx=10, pady=(10, 5))

                ctk.CTkLabel(
                    video_item,
                    text=duration,
                    text_color="#aaaaaa",
                ).pack(anchor="w", padx=10, pady=(0, 10))

                # Open video player (mpv) when video_frame is clicked
                bind_click_recursive(video_item, lambda _event, current_video=video: open_video_player(current_video))

        def worker():
            try:
                playlist_data = fetch_playlist_videos(playlist)
            except Exception as e:
                logger.error("Unable to load playlist videos: %s", e)
                playlist_window.after(0, lambda: info_label.configure(text=f"Unable to load playlist: {e}"))
                return

            playlist_window.after(0, lambda: render_videos(playlist_data))

        threading.Thread(target=worker, daemon=True).start()

    # Load playlist when background thread finished playlists data fetch process
    def build_ui(playlists_data):
        for widget in playlist_frame.winfo_children():
            widget.destroy()

        playlists = playlists_data.get("entries", [])

        playlists = [playlist for playlist in playlists if playlist is not None] # ignore None item

        for playlist in playlists:
            title = playlist.get("title", "Title not found")
            id = playlist.get("id", "ID not found")
            if len(playlist.get("thumbnails", [])) > 0:
                thumbnail_url = playlist.get("thumbnails", [])[0].get('url', None)
                thumbnail_resolution = playlist.get("thumbnails", [])[0].get('resolution', None)

                thumbnail_path = os.path.join(ROOT_DIR, "temp", "thumbnails",
                                              f"thumbnail_{id}_{thumbnail_resolution}.jpg")

                result = download_file(thumbnail_url, thumbnail_path)

                if result:
                    image = Image.open(thumbnail_path)
                    thumbnail = ctk.CTkImage(image, size=(60, 30))
                else:
                    thumbnail = None
            else:
                thumbnail = None

            playlist_item = ctk.CTkFrame(playlist_frame, border_width=1, border_color="#666666",
                                         height=120)

            title_label = ctk.CTkLabel(playlist_item, text=title)
            title_label.pack(anchor="w", padx=10, pady=10)

            # Don't apply thumbnail if not available
            if thumbnail:
                thumbnail_label = ctk.CTkLabel(playlist_item, image=thumbnail)
                thumbnail_label.pack(anchor="e", padx=5, pady=5)

            bind_click_recursive(playlist_item, lambda _event, current_playlist=playlist: open_playlist_window(current_playlist))
            playlist_item.pack(padx=5, pady=5, expand=True, fill="x")

    root.mainloop()

if __name__ == "__main__":
    main()
