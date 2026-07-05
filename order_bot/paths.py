from __future__ import annotations

import os
from pathlib import Path

def app_data_dir() -> Path:
    override = os.environ.get("AUTO_ORDER_BOT_DATA_DIR")
    return Path(override).expanduser() if override else Path.cwd()


def log_dir() -> Path:
    path = app_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path
