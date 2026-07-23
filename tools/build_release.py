from __future__ import annotations

import argparse
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "自动下单机器人"
APP_INTERNAL_NAME = "AutoOrderBot"
WINDOWS_APP_ID = "8D8E9F3F-8D2C-4E96-BF8F-C57826A1D84A"
MAC_BUNDLE_ID = "com.anxuanfu.auto-order-bot"
BUILD_CACHE_DIR = ROOT / ".build_cache"
DEPS_MARKER = BUILD_CACHE_DIR / "requirements.sha256"
CHROMIUM_MARKER = BUILD_CACHE_DIR / "playwright-chromium.version"
REQUIRED_IMPORTS = ("playwright", "PyInstaller", "PIL", "tzdata")


def configure_stdio_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="backslashreplace")
            except Exception:
                pass


def log(message: str) -> None:
    try:
        print(message)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        safe_message = message.encode(encoding, errors="backslashreplace").decode(encoding, errors="replace")
        print(safe_message)


def main() -> int:
    configure_stdio_encoding()
    parser = argparse.ArgumentParser(description="Build installable packages for 自动下单机器人.")
    parser.add_argument(
        "--target",
        choices=["current", "windows", "mac", "all"],
        default="current",
        help="Target platform. Windows packages must be built on Windows; mac packages on macOS.",
    )
    parser.add_argument("--timestamp", help="Reuse an existing build timestamp folder, e.g. 20260705-151430.")
    parser.add_argument("--version", help="Override package version. Defaults to order_bot.__version__.")
    parser.add_argument("--skip-deps", action="store_true", help="Do not install build dependencies first.")
    parser.add_argument("--skip-browser-install", action="store_true", help="Do not run playwright install chromium.")
    parser.add_argument("--mac-dmg", action="store_true", help="Also create a macOS .dmg wrapper around the .pkg installer.")
    args = parser.parse_args()

    version = args.version or read_version()
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    build_root = ROOT / "build" / timestamp
    windows_dir = build_root / "Windows"
    mac_dir = build_root / "mac"
    windows_dir.mkdir(parents=True, exist_ok=True)
    mac_dir.mkdir(parents=True, exist_ok=True)

    targets = resolve_targets(args.target)
    write_not_requested_platform_notes(windows_dir, mac_dir, targets)

    buildable_targets = [target for target in targets if target_supported_here(target)]
    if buildable_targets and not args.skip_deps:
        install_build_dependencies()
    if buildable_targets and not args.skip_browser_install:
        bundled_browser_targets = [target for target in buildable_targets if should_bundle_playwright_browser(target)]
        if bundled_browser_targets:
            install_playwright_browser()
        else:
            print("Skip bundled Playwright Chromium; the packaged app installs it on first browser use.")
    if targets and not buildable_targets:
        print("No selected target can be built on this operating system; writing notes only.")

    print(f"Build root: {build_root}")
    print(f"Version: {version}")
    print(f"Targets: {', '.join(targets)}")

    for target in targets:
        if target == "windows":
            if platform.system() != "Windows":
                write_skipped(windows_dir, "Windows installer must be built on Windows. Run build_windows.ps1 there.")
                continue
            build_windows(windows_dir, version, timestamp)
        elif target == "mac":
            if platform.system() != "Darwin":
                write_skipped(mac_dir, "mac installer must be built on macOS. Run ./build_mac.sh there.")
                continue
            build_mac(mac_dir, version, timestamp, include_dmg=args.mac_dmg)

    print(f"Done. Output: {build_root}")
    return 0


def resolve_targets(target: str) -> list[str]:
    if target == "current":
        return ["windows"] if platform.system() == "Windows" else ["mac"] if platform.system() == "Darwin" else []
    if target == "all":
        return ["windows", "mac"]
    return [target]


def target_supported_here(target: str) -> bool:
    system = platform.system()
    return (target == "windows" and system == "Windows") or (target == "mac" and system == "Darwin")


def should_bundle_playwright_browser(target: str) -> bool:
    return False


def read_version() -> str:
    init_file = ROOT / "order_bot" / "__init__.py"
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', init_file.read_text(encoding="utf-8"))
    return match.group(1) if match else "0.1.0"


def install_build_dependencies() -> None:
    current_hash = requirements_hash()
    if DEPS_MARKER.exists() and DEPS_MARKER.read_text(encoding="utf-8").strip() == current_hash and required_imports_available():
        print("Python build dependencies unchanged; skip pip install.")
        return
    if not DEPS_MARKER.exists() and required_imports_available():
        print("Python build dependencies already available; write cache marker and skip pip install.")
        write_marker(DEPS_MARKER, current_hash)
        return

    run([sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")])
    run([sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements-build.txt")])
    write_marker(DEPS_MARKER, current_hash)


def install_playwright_browser() -> None:
    playwright_version = installed_package_version("playwright")
    cached_version = CHROMIUM_MARKER.read_text(encoding="utf-8").strip() if CHROMIUM_MARKER.exists() else ""
    if packaged_chromium_exists() and cached_version == playwright_version:
        print("Bundled Playwright Chromium unchanged; skip browser download.")
        return
    if packaged_chromium_exists() and not cached_version:
        print("Bundled Playwright Chromium already exists; write cache marker and skip browser download.")
        write_marker(CHROMIUM_MARKER, playwright_version)
        return
    env = os.environ.copy()
    env["PLAYWRIGHT_BROWSERS_PATH"] = "0"
    run([sys.executable, "-m", "playwright", "install", "chromium"], env=env)
    write_marker(CHROMIUM_MARKER, playwright_version)


def packaged_chromium_exists() -> bool:
    try:
        import playwright
    except Exception:
        return False
    browsers_dir = Path(playwright.__file__).resolve().parent / "driver" / "package" / ".local-browsers"
    if not browsers_dir.exists():
        return False
    executable_names = {
        "Windows": "chrome.exe",
        "Darwin": "Chromium",
    }
    executable_name = executable_names.get(platform.system(), "chrome")
    return any(path.name == executable_name for path in browsers_dir.rglob(executable_name))


def remove_packaged_playwright_browsers() -> None:
    try:
        import playwright
    except Exception:
        return
    browsers_dir = Path(playwright.__file__).resolve().parent / "driver" / "package" / ".local-browsers"
    if browsers_dir.exists():
        shutil.rmtree(browsers_dir)
        print(f"Removed Playwright browser cache before macOS packaging: {browsers_dir}")


def requirements_hash() -> str:
    digest = hashlib.sha256()
    for relative_path in ("requirements.txt", "requirements-build.txt"):
        path = ROOT / relative_path
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def required_imports_available() -> bool:
    for module_name in REQUIRED_IMPORTS:
        try:
            __import__(module_name)
        except Exception:
            return False
    return True


def installed_package_version(package_name: str) -> str:
    try:
        from importlib.metadata import version

        return version(package_name)
    except Exception:
        return "unknown"


def write_marker(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content + "\n", encoding="utf-8")


def build_windows(output_dir: Path, version: str, timestamp: str) -> None:
    assets_dir = output_dir / "assets"
    icon_paths = generate_icons(assets_dir)
    dist_dir = output_dir / "pyinstaller-dist"
    work_dir = output_dir / "pyinstaller-work"
    spec_dir = output_dir / "pyinstaller-spec"
    installer_dir = output_dir / "installer"
    installer_dir.mkdir(parents=True, exist_ok=True)
    remove_packaged_playwright_browsers()

    env = os.environ.copy()
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--windowed",
            "--name",
            APP_NAME,
            "--icon",
            str(icon_paths["ico"]),
            "--distpath",
            str(dist_dir),
            "--workpath",
            str(work_dir),
            "--specpath",
            str(spec_dir),
            "--collect-all",
            "playwright",
            "--collect-all",
            "tzdata",
            str(ROOT / "run_gui.py"),
        ],
        env=env,
    )

    app_dir = dist_dir / APP_NAME
    if not (app_dir / f"{APP_NAME}.exe").exists():
        raise RuntimeError(f"PyInstaller output not found: {app_dir}")

    portable_zip = output_dir / f"{APP_NAME}-便携版-{version}-{timestamp}"
    shutil.make_archive(str(portable_zip), "zip", root_dir=dist_dir, base_dir=APP_NAME)

    try:
        iscc = find_inno_setup()
    except RuntimeError as exc:
        write_skipped(
            installer_dir,
            f"{exc}\n\n"
            f"未生成安装包。你可以临时发送便携版：{portable_zip.with_suffix('.zip')}\n"
            "便携版需要解压整个文件夹后运行里面的 自动下单机器人.exe。",
        )
        write_windows_delivery_readme(output_dir, version, timestamp, installer_created=False)
        return

    iss_path = output_dir / f"{APP_INTERNAL_NAME}.iss"
    iss_path.write_text(make_inno_script(app_dir, installer_dir, version, timestamp), encoding="utf-8-sig")
    run([str(iscc), str(iss_path)])
    write_windows_delivery_readme(output_dir, version, timestamp, installer_created=True)


def build_mac(output_dir: Path, version: str, timestamp: str, *, include_dmg: bool = False) -> None:
    assets_dir = output_dir / "assets"
    icon_paths = generate_icons(assets_dir)
    dist_dir = output_dir / "pyinstaller-dist"
    work_dir = output_dir / "pyinstaller-work"
    spec_dir = output_dir / "pyinstaller-spec"
    remove_packaged_playwright_browsers()

    env = os.environ.copy()
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--windowed",
            "--name",
            APP_INTERNAL_NAME,
            "--icon",
            str(icon_paths["icns"]),
            "--osx-bundle-identifier",
            MAC_BUNDLE_ID,
            "--distpath",
            str(dist_dir),
            "--workpath",
            str(work_dir),
            "--specpath",
            str(spec_dir),
            "--collect-all",
            "playwright",
            "--collect-all",
            "tzdata",
            str(ROOT / "run_gui.py"),
        ],
        env=env,
    )

    app_path = dist_dir / f"{APP_INTERNAL_NAME}.app"
    if not app_path.exists():
        raise RuntimeError(f"PyInstaller app not found: {app_path}")
    display_app_path = dist_dir / f"{APP_NAME}.app"
    if display_app_path != app_path:
        if display_app_path.exists():
            shutil.rmtree(display_app_path)
        app_path.rename(display_app_path)
        app_path = display_app_path
    run([str(mac_app_executable(app_path)), "--self-test"])

    pkg_path = output_dir / f"{APP_NAME}-{version}-{timestamp}.pkg"
    run(
        [
            "pkgbuild",
            "--component",
            str(app_path),
            "--install-location",
            "/Applications",
            "--identifier",
            MAC_BUNDLE_ID,
            "--version",
            version,
            str(pkg_path),
        ]
    )

    if include_dmg and shutil.which("hdiutil"):
        dmg_path = output_dir / f"{APP_NAME}-{version}-{timestamp}.dmg"
        dmg_source_dir = output_dir / "dmg-source"
        dmg_source_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pkg_path, dmg_source_dir / pkg_path.name)
        try:
            run(
                [
                    "hdiutil",
                    "create",
                    "-volname",
                    APP_NAME,
                    "-srcfolder",
                    str(dmg_source_dir),
                    "-ov",
                    "-format",
                    "UDZO",
                    str(dmg_path),
                ]
            )
        except subprocess.CalledProcessError as exc:
            print(f"DMG creation failed; keeping pkg installer: {exc}")
    elif include_dmg:
        print("DMG creation requested but hdiutil is not available; keeping pkg installer only.")
    else:
        print("Skip DMG creation; macOS installer output is pkg only.")
    write_mac_delivery_readme(output_dir, version, timestamp, include_dmg=include_dmg)


def mac_app_executable(app_path: Path) -> Path:
    return app_path / "Contents" / "MacOS" / APP_INTERNAL_NAME


def generate_icons(assets_dir: Path) -> dict[str, Path]:
    from PIL import Image, ImageDraw

    assets_dir.mkdir(parents=True, exist_ok=True)
    png_path = assets_dir / "icon.png"
    ico_path = assets_dir / "icon.ico"
    icns_path = assets_dir / "icon.icns"

    base = make_icon_image(1024)
    base.save(png_path)
    base.save(ico_path, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    try:
        base.save(icns_path, format="ICNS")
    except Exception:
        iconset = assets_dir / "icon.iconset"
        iconset.mkdir(exist_ok=True)
        for size in [16, 32, 64, 128, 256, 512]:
            base.resize((size, size), Image.LANCZOS).save(iconset / f"icon_{size}x{size}.png")
            if size <= 512:
                base.resize((size * 2, size * 2), Image.LANCZOS).save(iconset / f"icon_{size}x{size}@2x.png")
        if shutil.which("iconutil"):
            run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns_path)])
        else:
            raise RuntimeError("Could not create .icns. Install Pillow with ICNS support or run on macOS with iconutil.")

    return {"png": png_path, "ico": ico_path, "icns": icns_path}


def make_icon_image(size: int):
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    radius = int(size * 0.2)
    draw.rounded_rectangle((0, 0, size, size), radius=radius, fill=(18, 112, 214, 255))
    draw.rounded_rectangle(
        (int(size * 0.18), int(size * 0.24), int(size * 0.82), int(size * 0.72)),
        radius=int(size * 0.08),
        fill=(255, 255, 255, 255),
    )
    draw.rectangle(
        (int(size * 0.45), int(size * 0.14), int(size * 0.55), int(size * 0.26)),
        fill=(255, 255, 255, 255),
    )
    draw.ellipse(
        (int(size * 0.41), int(size * 0.08), int(size * 0.59), int(size * 0.24)),
        fill=(255, 255, 255, 255),
    )
    draw.ellipse(
        (int(size * 0.31), int(size * 0.39), int(size * 0.41), int(size * 0.49)),
        fill=(18, 112, 214, 255),
    )
    draw.ellipse(
        (int(size * 0.59), int(size * 0.39), int(size * 0.69), int(size * 0.49)),
        fill=(18, 112, 214, 255),
    )
    draw.rounded_rectangle(
        (int(size * 0.32), int(size * 0.58), int(size * 0.68), int(size * 0.63)),
        radius=int(size * 0.025),
        fill=(18, 112, 214, 255),
    )
    cart_y = int(size * 0.8)
    draw.line(
        (int(size * 0.22), cart_y, int(size * 0.72), cart_y, int(size * 0.82), int(size * 0.64)),
        fill=(255, 255, 255, 255),
        width=max(8, int(size * 0.04)),
        joint="curve",
    )
    draw.ellipse(
        (int(size * 0.28), int(size * 0.82), int(size * 0.4), int(size * 0.94)),
        fill=(255, 255, 255, 255),
    )
    draw.ellipse(
        (int(size * 0.62), int(size * 0.82), int(size * 0.74), int(size * 0.94)),
        fill=(255, 255, 255, 255),
    )
    return image


def make_inno_script(app_dir: Path, installer_dir: Path, version: str, timestamp: str) -> str:
    version_info = numeric_version(version)
    return textwrap.dedent(
        f"""
        #define MyAppName "{APP_NAME}"
        #define MyAppVersion "{version}"
        #define MyAppPublisher "安轩福公司"
        #define MyAppExeName "{APP_NAME}.exe"

        [Setup]
        AppId={{{{{WINDOWS_APP_ID}}}
        AppName={{#MyAppName}}
        AppVersion={{#MyAppVersion}}
        AppVerName={{#MyAppName}} {{#MyAppVersion}}
        AppPublisher={{#MyAppPublisher}}
        DefaultDirName={{localappdata}}\\Programs\\{{#MyAppName}}
        DefaultGroupName={{#MyAppName}}
        UninstallDisplayIcon={{app}}\\{{#MyAppExeName}}
        OutputDir={escape_inno_path(installer_dir)}
        OutputBaseFilename={APP_NAME}-安装包-{version}-{timestamp}
        Compression=lzma
        SolidCompression=yes
        WizardStyle=modern
        PrivilegesRequired=lowest
        SetupLogging=yes
        CloseApplications=yes
        CloseApplicationsFilter={{#MyAppExeName}}
        RestartApplications=no
        VersionInfoVersion={version_info}
        VersionInfoProductName={{#MyAppName}}
        VersionInfoProductVersion={{#MyAppVersion}}
        VersionInfoCompany=安轩福公司

        [Languages]
        Name: "chinesesimp"; MessagesFile: "compiler:Default.isl"

        [Code]
        procedure StopRunningApp();
        var
          ResultCode: Integer;
        begin
          Exec(ExpandConstant('{{cmd}}'), '/C taskkill /IM "{{#MyAppExeName}}" /F /T >NUL 2>NUL', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
        end;

        function InitializeSetup(): Boolean;
        begin
          StopRunningApp();
          Result := True;
        end;

        [Files]
        Source: "{escape_inno_path(app_dir)}\\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs

        [Icons]
        Name: "{{group}}\\{{#MyAppName}}"; Filename: "{{app}}\\{{#MyAppExeName}}"; WorkingDir: "{{app}}"
        Name: "{{autodesktop}}\\{{#MyAppName}}"; Filename: "{{app}}\\{{#MyAppExeName}}"; WorkingDir: "{{app}}"; Tasks: desktopicon

        [Tasks]
        Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标："; Flags: unchecked

        [Run]
        Filename: "{{app}}\\{{#MyAppExeName}}"; WorkingDir: "{{app}}"; Description: "启动 {{#MyAppName}}"; Flags: nowait postinstall skipifsilent
        """
    ).strip()


def find_inno_setup() -> Path:
    candidates = [
        shutil.which("ISCC.exe"),
        shutil.which("iscc.exe"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return Path(candidate)
    raise RuntimeError(
        "Inno Setup 6 was not found. Install it first, then re-run build_windows.ps1. "
        "Expected ISCC.exe in PATH or the default Inno Setup install directory."
    )


def numeric_version(version: str) -> str:
    parts = re.findall(r"\d+", version)
    parts = (parts + ["0", "0", "0", "0"])[:4]
    return ".".join(str(min(int(part), 65535)) for part in parts)


def escape_inno_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "\\\\")


def write_skipped(output_dir: Path, message: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "SKIPPED.txt").write_text(message + "\n", encoding="utf-8")
    print(message)


def write_not_requested_platform_notes(windows_dir: Path, mac_dir: Path, targets: list[str]) -> None:
    if "windows" not in targets:
        write_note(
            windows_dir,
            "README-本次未打包Windows.txt",
            "本次没有选择 Windows 打包目标。\n"
            "如需生成 Windows 可安装程序，请在 Windows 电脑上运行 .\\build_windows.ps1。\n"
            "生成后直接发给别人的是 Windows\\installer 目录里的 .exe 安装包。",
        )
    if "mac" not in targets:
        write_note(
            mac_dir,
            "README-需要在Mac打包.txt",
            "本次没有选择 mac 打包目标。\n"
            "重要：macOS 安装包不能在 Windows 下直接生成，因为 PyInstaller 的 .app、Apple 的 pkgbuild/hdiutil 都需要在 macOS 上运行。\n"
            "如需生成 Mac 可安装程序，请把源码复制到 Mac 电脑后运行 sh build_mac.sh。\n"
            "生成后直接发给别人的是 mac 目录里的 .pkg，若同时生成 .dmg 也可以发送 .dmg。",
        )


def write_note(output_dir: Path, filename: str, message: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / filename).write_text(message.strip() + "\n", encoding="utf-8")


def write_windows_delivery_readme(output_dir: Path, version: str, timestamp: str, *, installer_created: bool) -> None:
    installer_name = f"{APP_NAME}-安装包-{version}-{timestamp}.exe"
    portable_name = f"{APP_NAME}-便携版-{version}-{timestamp}.zip"
    if installer_created:
        message = (
            f"发给别人安装：installer\\{installer_name}\n"
            f"备用便携版：{portable_name}\n\n"
            "其它目录说明：\n"
            "- assets：自动生成的图标文件。\n"
            "- pyinstaller-dist：PyInstaller 生成的可运行程序目录。\n"
            "- pyinstaller-work：PyInstaller 临时构建缓存。\n"
            "- pyinstaller-spec：PyInstaller 配置文件。\n"
            "- installer：最终 Windows 安装包输出目录。\n\n"
            "首次使用浏览器下单功能时，程序会自动下载 Playwright Chromium 到当前用户固定缓存目录，请保持网络可用；同一用户后续更新安装包会复用已有缓存。\n"
            "程序运行产生的日志、排期和失败截图会保存在程序当前运行目录；安装包快捷方式默认使用安装目录。\n"
            "邮箱登录信息保存在当前用户固定目录（%LOCALAPPDATA%\\AutoOrderBot\\email_accounts.json），更新安装包不会清除；只有在邮件界面退出登录或删除下拉邮箱才会删除。\n"
        )
    else:
        message = (
            f"当前没有生成 installer 安装包，因为本机未安装 Inno Setup 6。\n"
            f"临时发给别人使用：{portable_name}\n"
            "便携版必须先解压整个文件夹，再运行里面的 自动下单机器人.exe。\n\n"
            "如需真正安装包，请安装 Inno Setup 6 后重新执行 .\\build_windows.ps1。\n"
        )
    (output_dir / "发给别人看这里.txt").write_text(message, encoding="utf-8")


def write_mac_delivery_readme(output_dir: Path, version: str, timestamp: str, *, include_dmg: bool) -> None:
    pkg_name = f"{APP_NAME}-{version}-{timestamp}.pkg"
    dmg_name = f"{APP_NAME}-{version}-{timestamp}.dmg"
    dmg_note = (
        f"本次已选择生成 DMG：如果同目录生成了 {dmg_name}，也可以发送这个 .dmg。\n\n"
        if include_dmg
        else "本次未选择生成 DMG，默认只生成体积更小的 .pkg 安装包。\n\n"
    )
    message = (
        f"发给别人安装：{pkg_name}\n"
        f"{dmg_note}"
        "其它目录说明：\n"
        "- assets：自动生成的图标文件。\n"
        "- pyinstaller-dist：PyInstaller 生成的 .app 程序目录。\n"
        "- pyinstaller-work：PyInstaller 临时构建缓存。\n"
        "- pyinstaller-spec：PyInstaller 配置文件。\n\n"
        "首次使用浏览器下单功能时，程序会自动下载 Playwright Chromium 到当前用户固定缓存目录，请保持网络可用；同一用户后续更新安装包会复用已有缓存。\n"
        "程序运行产生的日志、排期和失败截图会保存在 ~/Library/Application Support/AutoOrderBot/logs，避免写入只读系统目录。\n"
        "邮箱登录信息保存在 ~/Library/Application Support/AutoOrderBot/email_accounts.json，更新安装包不会清除；只有在邮件界面退出登录或删除下拉邮箱才会删除。\n"
        "Mac 安装包使用固定 bundle/package identifier；安装新版 .pkg 时会按同一个应用覆盖升级旧版。\n"
    )
    (output_dir / "发给别人看这里.txt").write_text(message, encoding="utf-8")


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    log("+ " + " ".join(str(part) for part in command))
    subprocess.run(command, cwd=ROOT, env=env, check=True)


if __name__ == "__main__":
    raise SystemExit(main())
