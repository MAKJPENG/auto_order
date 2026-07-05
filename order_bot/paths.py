from __future__ import annotations

import os
import sys
from pathlib import Path


APP_DATA_DIR_NAME = "AutoOrderBot"


def app_data_dir() -> Path:
    if not getattr(sys, "frozen", False):
        return Path.cwd()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / APP_DATA_DIR_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DATA_DIR_NAME
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_DATA_DIR_NAME


def log_dir() -> Path:
    path = app_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path
