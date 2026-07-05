from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DATA_DIR_NAME = "AutoOrderBot"


def app_data_dir() -> Path:
    override = os.environ.get("AUTO_ORDER_BOT_DATA_DIR")
    return Path(override).expanduser() if override else Path.cwd()


def user_data_dir() -> Path:
    override = os.environ.get("AUTO_ORDER_BOT_USER_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / APP_DATA_DIR_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DATA_DIR_NAME
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_DATA_DIR_NAME


def browser_cache_dir() -> Path:
    override = os.environ.get("AUTO_ORDER_BOT_BROWSER_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    return user_data_dir() / "playwright-browsers"


def log_dir() -> Path:
    path = app_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path
