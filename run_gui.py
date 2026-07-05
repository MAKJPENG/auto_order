from __future__ import annotations

import os
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path


APP_DATA_DIR_NAME = "AutoOrderBot"


def main() -> int:
    try:
        from order_bot.gui import main as gui_main

        return gui_main()
    except SystemExit:
        raise
    except Exception as exc:
        log_path = _write_startup_crash_log(exc)
        _show_startup_error(exc, log_path)
        return 1


def _write_startup_crash_log(exc: Exception) -> Path:
    log_dir = _startup_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = log_dir / f"startup-crash-{timestamp}.txt"
    log_path.write_text(
        "".join(
            [
                f"Python: {sys.version}\n",
                f"Executable: {sys.executable}\n",
                f"Platform: {sys.platform}\n",
                f"Arguments: {sys.argv!r}\n\n",
                "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            ]
        ),
        encoding="utf-8",
        errors="replace",
    )
    return log_path


def _startup_log_dir() -> Path:
    override = os.environ.get("AUTO_ORDER_BOT_USER_DATA_DIR")
    if override:
        return Path(override).expanduser() / "logs"
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / APP_DATA_DIR_NAME / "logs"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DATA_DIR_NAME / "logs"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_DATA_DIR_NAME / "logs"


def _show_startup_error(exc: Exception, log_path: Path) -> None:
    message = f"自动下单机器人启动失败：{exc}\n\n错误日志：{log_path}"
    if sys.platform == "darwin":
        _show_macos_alert(message)
        return
    try:
        from tkinter import Tk, messagebox

        root = Tk()
        root.withdraw()
        messagebox.showerror("自动下单机器人启动失败", message)
        root.destroy()
    except Exception:
        print(message, file=sys.stderr)


def _show_macos_alert(message: str) -> None:
    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                'display alert "自动下单机器人启动失败" message '
                + _applescript_string(message)
                + ' as critical buttons {"确定"} default button "确定"',
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        print(message, file=sys.stderr)


def _applescript_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


if __name__ == "__main__":
    raise SystemExit(main())
