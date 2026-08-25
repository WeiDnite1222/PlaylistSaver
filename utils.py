import base64
import binascii
import hashlib
import hmac
import json
import platform
import shutil
import urllib
try:
    import winreg
except ImportError:  # Windows-only standard library module
    winreg = None
from pathlib import Path
import sys
import os
import logging
import requests

logger = logging.getLogger("PlaylistSaver.Utils")
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler(sys.stdout))

def set_info(target_logger):
    global logger
    logger = target_logger

def key_exists(hive, sub_key):
    if winreg is None:
        return False

    try:
        # Attempt to open the key for reading
        with winreg.OpenKey(hive, sub_key, 0, winreg.KEY_READ) as key:
            return True
    except FileNotFoundError:
        return False

def create_url_scheme(main_file_path, work_dir):
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
            command = f"\"{sys.executable}\" --work-dir \"{work_dir}\" \"%1\""
        else:
            command = f"\"{sys.executable}\" \"{main_file_path}\" --work-dir \"{work_dir}\" \"%1\""

        winreg.SetValueEx(command_key, None, 0, winreg.REG_SZ, command)

        logger.info("URL scheme registered.")

    except OSError as e:
        logger.error("Unable to create URL scheme: %s", e)

def check_url_scheme(executable_path, work_dir):
    if winreg is None:
        return

    try:
        if not key_exists(winreg.HKEY_CURRENT_USER, r"Software\Classes\PlaylistSaver"):
            create_url_scheme(executable_path, work_dir)
    except FileNotFoundError:
        create_url_scheme(executable_path, work_dir)

def read_cookies(cookie_path):
    try:
        with open(cookie_path, "r", encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        logger.error("Cookies file not found.")
    except Exception as e:
        logger.error("An unexpected error occurred while reading cookies: %s", e)

def save_cookies(cookies, cookie_path):
    try:
        os.makedirs(os.path.dirname(cookie_path), exist_ok=True)
        with open(cookie_path, "w", encoding='utf-8') as f:
            f.write(cookies)
    except Exception as e:
        logger.error("An unexpected error occurred while saving cookies: %s", e)

def save_base64_netscape_cookie(cookies_encoded, cookie_path):
    try:
        cookies = base64.b64decode(cookies_encoded).decode("utf-8")
    except binascii.Error:
        logger.error("Unable to decode base64 cookie: %s", cookies_encoded)
        return

    save_cookies(cookies, cookie_path)

def _decode_urlsafe(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def decrypt_cookie_payload(payload: str, secret_key: str) -> str:
    raw = _decode_urlsafe(payload)
    if len(raw) < 49 or raw[0] != 1:
        raise ValueError("Unsupported encrypted cookie payload.")

    master_key = _decode_urlsafe(secret_key)
    if len(master_key) != 32:
        raise ValueError("Invalid PlaylistSaver secret key.")

    signed_data, supplied_tag = raw[:-32], raw[-32:]
    nonce, ciphertext = raw[1:17], raw[17:-32]
    encryption_key = hmac.new(master_key, b"PlaylistSaver cookie encryption", hashlib.sha256).digest()
    authentication_key = hmac.new(master_key, b"PlaylistSaver cookie authentication", hashlib.sha256).digest()
    expected_tag = hmac.new(authentication_key, signed_data, hashlib.sha256).digest()
    if not hmac.compare_digest(supplied_tag, expected_tag):
        raise ValueError("Cookie payload authentication failed. Check the Helper secret key.")

    plaintext = bytearray(len(ciphertext))
    for offset in range(0, len(ciphertext), 32):
        counter = (offset // 32).to_bytes(4, "big")
        stream = hmac.new(encryption_key, nonce + counter, hashlib.sha256).digest()
        block = ciphertext[offset:offset + 32]
        plaintext[offset:offset + len(block)] = bytes(a ^ b for a, b in zip(block, stream))

    return plaintext.decode("utf-8")


def parser_url_scheme(url, cookie_path: Path, secret_key: str | None = None):
    logger.debug("Parsing URL scheme: %s", url)

    # parse_qs performs percent-decoding itself. Decoding the whole URL first
    # would turn an encoded Base64 "+" into a query-string space.
    url = urllib.parse.urlparse(url)
    parameters = urllib.parse.parse_qs(url.query)

    if "encryptedcookies" in parameters:
        if not secret_key:
            raise ValueError("PlaylistSaver secret key is unavailable.")
        cookies = decrypt_cookie_payload(parameters["encryptedcookies"][0], secret_key)
        save_cookies(cookies, cookie_path)
        return

    func_map = {
        "base64cookies": {
            "func": save_base64_netscape_cookie,
            "args": [parameters["base64cookies"][0], cookie_path]
        },
    }

    for arg_name in parameters:
        match_func = func_map.get(arg_name, {}).get("func")
        if match_func:
            match_func(*func_map[arg_name].get("args", []))
        else:
            logger.warning("Unknown parameter: %s Value: %s", arg_name, parameters[arg_name])

def save_json(data: dict, dest):
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error("Unable to save json: %s", e)

def read_json(path):
    try:
        with open(path, "r", encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error("Unable to read json: %s", e)

def find_mpv_path(program_dir: Path):
    local_candidates = [
    ]

    platform_name = platform.system().lower()
    machine = platform.machine().lower()

    if platform_name == "windows"  and ("amd64" in machine or "x86_64" in machine):
        local_candidates.append(Path(program_dir, "bin", "mpv", "nt", "x64", "mpv.exe"))
    elif platform_name == "windows" and ("arm64" in machine or "aarch64" in machine):
        local_candidates.append(Path(program_dir, "bin", "mpv", "nt", "arm", "mpv.exe"))
    elif platform_name == "windows" and machine in {"x86", "i386", "i686"}:
        local_candidates.append(Path(program_dir, "bin", "mpv", "nt", "x86", "mpv.exe"))

    if platform_name == "darwin" and ("amd64" in machine or "x86_64" in machine):
        local_candidates.append(Path(program_dir, "bin", "mpv", "darwin", "x64", "mpv"))
    elif platform_name == "darwin" and ("arm64" in machine or "aarch64" in machine):
        local_candidates.append(Path(program_dir, "bin", "mpv", "darwin", "arm", "mpv"))

    if platform_name == "linux" and ("amd64" in machine or "x86_64" in machine):
        local_candidates.append(Path(program_dir, "bin", "mpv", "linux", "x64", "mpv"))
    elif platform_name == "linux" and ("arm64" in machine or "aarch64" in machine):
        local_candidates.append(Path(program_dir, "bin", "mpv", "linux", "arm", "mpv"))
    elif platform_name == "linux" and machine in {"x86", "i386", "i686"}:
        local_candidates.append(Path(program_dir, "bin", "mpv", "linux", "x86", "mpv"))

    for candidate in local_candidates:
        if candidate.exists() and candidate.is_file():
            if os.name != "nt" and not os.access(candidate, os.X_OK):
                try:
                    candidate.chmod(candidate.stat().st_mode | 0o111)
                except OSError as exc:
                    logger.warning("Unable to make bundled mpv executable: %s", exc)
                    continue
            return candidate

    # failback to PATH mpv (if available)
    return shutil.which("mpv")

def find_http_status(exc: Exception):
    seen = set()
    stack = [exc]

    while stack:
        cur = stack.pop()
        if cur is None or id(cur) in seen:
            continue
        seen.add(id(cur))

        status = getattr(cur, "status", None) or getattr(cur, "code", None)
        if status is not None:
            return status

        stack.extend([
            getattr(cur, "__cause__", None),
            getattr(cur, "__context__", None),
        ])

    return None


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


def download_file(url, dest: Path):
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=30) as r:
            r.raise_for_status()
            with dest.open(mode="wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        return True
    except Exception as e:
        logger.error("Unable to download file: %s", e)
        return False
