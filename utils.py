import base64
import binascii
import json
import urllib
import winreg
from pathlib import Path
from urllib.parse import unquote
import sys
import os
import logging

logger = logging.getLogger("PlaylistSaver.Utils")
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler(sys.stdout))

def set_info(target_logger):
    global logger
    logger = target_logger

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

def read_cookies(cookie_path):
    try:
        with open(cookie_path, "r", encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        logger.error("Cookies file not found.")
    except Exception as e:
        logger.error("An unexpected error occurred while reading cookies:", e)

def save_cookies(cookies, cookie_path):
    try:
        os.makedirs(os.path.dirname(cookie_path), exist_ok=True)
        with open(cookie_path, "w", encoding='utf-8') as f:
            f.write(cookies)
    except Exception as e:
        logger.error("An unexpected error occurred while saving cookies:", e)

def save_base64_netscape_cookie(cookies_encoded, cookie_path):
    try:
        cookies = base64.b64decode(cookies_encoded).decode("utf-8")
    except binascii.Error:
        logger.error("Unable to decode base64 cookie:", cookies_encoded)
        return

    logger.debug("==== COOKIE FILE ====")
    for line in cookies.split("\n"):
        print(line, "=>", len(line.split("\t")))
    logger.debug("=====================")

    save_cookies(cookies, cookie_path)

def parser_url_scheme(url, work_dir: Path):
    logger.debug("Parsing URL scheme:", url)

    url = urllib.parse.urlparse(unquote(sys.argv[1]))
    parameters = urllib.parse.parse_qs(url.query)

    func_map = {
        "base64cookies": {
            "func": save_base64_netscape_cookie,
            "args": [parameters["base64cookies"][0], work_dir]
        },
    }

    for arg_name in parameters:
        match_func = func_map.get(arg_name, {}).get("func")
        if match_func:
            match_func(*func_map[arg_name].get("args", []))
        else:
            logger.warning("Unknown parameter:", arg_name, " Value:", parameters[arg_name])

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