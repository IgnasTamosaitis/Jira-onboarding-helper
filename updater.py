"""Auto-update from GitHub Releases and uninstall helpers."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import unquote

import requests

GITHUB_REPO        = "IgnasTamosaitis/Jira-onboarding-helper"
_LATEST_RELEASE_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"
_REQUEST_HEADERS    = {
    "Cache-Control": "no-cache",
    "User-Agent": "Jira-Reminders-Updater",
}

IS_FROZEN    = bool(getattr(sys, "frozen", False))
APP_DIR      = (
    Path(sys.executable).resolve().parent
    if IS_FROZEN
    else Path(__file__).resolve().parent
)
VERSION_FILE = APP_DIR / "version.txt"
_STAGING_DIR = (
    Path(tempfile.gettempdir()) / "JiraRemindersUpdate"
    if IS_FROZEN
    else APP_DIR / "_update_staging"
)


def current_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return "0.0.0"


def _parse_ver(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.lstrip("v").split(".") if x.isdigit())


class UpdateCheckError(RuntimeError):
    """Raised when the latest release cannot be determined reliably."""


def _latest_release_tag() -> str:
    """Resolve GitHub's public latest-release redirect without using its API."""
    try:
        response = requests.get(
            _LATEST_RELEASE_URL,
            timeout=10,
            allow_redirects=False,
            headers=_REQUEST_HEADERS,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise UpdateCheckError(
            "GitHub could not be reached. Check your network connection and try again."
        ) from exc

    location = response.headers.get("Location", "")
    match = re.search(r"/releases/tag/([^/?#]+)", location)
    if not match:
        raise UpdateCheckError(
            "GitHub returned an unexpected latest-release response. Try again later."
        )
    return unquote(match.group(1))


def check_for_update() -> dict | None:
    """Return release info dict if a newer version exists on GitHub, else None."""
    try:
        tag = _latest_release_tag()
        if _parse_ver(tag) > _parse_ver(current_version()):
            version = tag.lstrip("v")
            result = {
                "version": tag,
                "notes": "",
            }
            if IS_FROZEN:
                installer_name = f"Jira-Reminders-{version}.msi"
                result.update({
                    "installer_url": (
                        f"https://github.com/{GITHUB_REPO}/releases/download/"
                        f"{tag}/{installer_name}"
                    ),
                    "installer_name": installer_name,
                })
            else:
                result["zipball_url"] = (
                    f"https://github.com/{GITHUB_REPO}/archive/refs/tags/{tag}.zip"
                )
            return result
    except UpdateCheckError:
        raise
    except (TypeError, ValueError) as exc:
        raise UpdateCheckError(
            "GitHub returned an invalid release version. Try again later."
        ) from exc
    return None


def _force_remove(path: Path) -> None:
    """Remove a directory tree, resetting permissions first if needed."""
    if not path.exists():
        return
    for p in path.rglob("*"):
        try:
            p.chmod(0o777)
        except Exception:
            pass
    shutil.rmtree(path, ignore_errors=True)
    if path.exists():
        # Last resort: let PowerShell do it
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Remove-Item -Path '{path}' -Recurse -Force -ErrorAction SilentlyContinue"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )


def _launch_ps1(src: Path, new_version: str) -> None:
    """Write and launch the PS1 that copies files and restarts the app."""
    launcher = APP_DIR / "start_reminders.vbs"
    log      = APP_DIR / "update.log"
    python_exe = Path(sys.executable)

    def q(p: Path) -> str:
        return f"'{p}'"

    def log_line(msg: str) -> str:
        return f"Add-Content -Path {q(log)} -Value \"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') {msg}\""

    ps_lines = [
        "$ErrorActionPreference = 'Continue'",
        log_line(f"update started: {new_version}"),
        "Start-Sleep -Seconds 3",
        log_line("fixing attributes"),
        f"Get-ChildItem -Path {q(src)} -Recurse | ForEach-Object {{ try {{ $_.Attributes = 'Normal' }} catch {{}} }}",
        log_line("copying .py files"),
        f"Get-ChildItem -Path {q(src)} -Filter '*.py' | Copy-Item -Destination {q(APP_DIR)} -Force",
        log_line("stamping version"),
        f"Set-Content -Path {q(VERSION_FILE)} -Value '{new_version}'",
        log_line("copying templates"),
        f"if (Test-Path {q(src / 'templates')}) {{",
        f"    robocopy {q(src / 'templates')} {q(APP_DIR / 'templates')} /E /IS /IT /IM | Out-Null",
        f"}}",
        log_line("launching app"),
        f"& C:\\Windows\\System32\\wscript.exe {q(launcher)}",
        f"Start-Sleep -Seconds 2",
        log_line("running pip install"),
        f"if (Test-Path {q(src / 'requirements.txt')}) {{",
        f"    Copy-Item -Path {q(src / 'requirements.txt')} -Destination {q(APP_DIR / 'requirements.txt')} -Force",
        f"    & {q(python_exe)} -m pip install -r {q(APP_DIR / 'requirements.txt')} --quiet",
        f"}}",
        log_line("cleaning up staging"),
        f"Remove-Item -Path {q(_STAGING_DIR)} -Recurse -Force -ErrorAction SilentlyContinue",
        log_line("done"),
    ]
    ps1 = _STAGING_DIR / "_apply_update.ps1"
    ps1.write_text("\n".join(ps_lines), encoding="utf-8")

    # VBS wrapper is required — subprocess.Popen with DETACHED_PROCESS fails
    # silently when launched from a consoleless pythonw process.
    ps1_escaped = str(ps1).replace('"', '""')
    vbs = _STAGING_DIR / "_run_update.vbs"
    vbs.write_text(
        'Set WshShell = CreateObject("WScript.Shell")\n'
        f'WshShell.Run "powershell -NoProfile -ExecutionPolicy Bypass'
        f' -WindowStyle Hidden -File ""{ps1_escaped}""", 0, False\n',
        encoding="utf-8",
    )
    subprocess.Popen(
        ["wscript.exe", str(vbs)],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
    )


def _launch_msi_update(msi_path: Path) -> None:
    """Install an MSI update after this frozen process exits, then relaunch it."""
    if not IS_FROZEN:
        raise RuntimeError("MSI updates are only available in the installed app.")

    ps1 = _STAGING_DIR / "_apply_msi_update.ps1"
    vbs = _STAGING_DIR / "_run_msi_update.vbs"
    app_exe = Path(sys.executable).resolve()

    def ps_quote(value: Path) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    ps1.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f"$msi = {ps_quote(msi_path)}\n"
        f"$app = {ps_quote(app_exe)}\n"
        "Start-Sleep -Seconds 3\n"
        "$args = @('/i', $msi, '/passive', '/norestart')\n"
        "$p = Start-Process -FilePath 'msiexec.exe' -ArgumentList $args -Wait -PassThru\n"
        "if ($p.ExitCode -notin @(0, 1641, 3010)) {\n"
        "    Add-Type -AssemblyName PresentationFramework\n"
        "    [System.Windows.MessageBox]::Show("
        "'Jira Reminders update failed with Windows Installer code ' + $p.ExitCode, "
        "'Update failed') | Out-Null\n"
        "    exit $p.ExitCode\n"
        "}\n"
        "Start-Sleep -Seconds 2\n"
        "Start-Process -FilePath $app\n"
        f"Remove-Item -LiteralPath {ps_quote(_STAGING_DIR)} -Recurse -Force "
        "-ErrorAction SilentlyContinue\n",
        encoding="utf-8",
    )
    ps1_escaped = str(ps1).replace('"', '""')
    vbs.write_text(
        'Set WshShell = CreateObject("WScript.Shell")\n'
        f'WshShell.Run "powershell -NoProfile -ExecutionPolicy Bypass'
        f' -WindowStyle Hidden -File ""{ps1_escaped}""", 0, False\n',
        encoding="utf-8",
    )
    subprocess.Popen(
        ["wscript.exe", str(vbs)],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
        # WScript and PowerShell pass this on to the restarted executable.
        # It must unpack a fresh runtime: the old _MEI directory is removed
        # when the current one-file application exits.
        env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
    )


def download_and_apply(release: dict, progress_cb=None) -> None:
    """Download and schedule the appropriate source or MSI update."""
    _force_remove(_STAGING_DIR)
    _STAGING_DIR.mkdir()

    if IS_FROZEN:
        installer_url = release.get("installer_url", "")
        if not installer_url:
            raise RuntimeError("This release does not contain a Windows installer.")
        installer_name = release.get("installer_name") or "JiraRemindersUpdate.msi"
        msi_path = _STAGING_DIR / Path(installer_name).name
        if progress_cb:
            progress_cb("Downloading installer…")
        r = requests.get(installer_url, stream=True, timeout=120)
        r.raise_for_status()
        with open(msi_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=16384):
                f.write(chunk)
        _launch_msi_update(msi_path)
        return

    zip_path = _STAGING_DIR / "update.zip"

    if progress_cb:
        progress_cb("Downloading…")
    r = requests.get(release["zipball_url"], stream=True, timeout=120)
    r.raise_for_status()
    with open(zip_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=16384):
            f.write(chunk)

    if progress_cb:
        progress_cb("Extracting…")
    extract_dir = _STAGING_DIR / "extracted"
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)

    top_dirs = [d for d in extract_dir.iterdir() if d.is_dir()]
    if not top_dirs:
        raise RuntimeError("Unexpected zip layout — no top-level directory found.")

    _launch_ps1(src=top_dirs[0], new_version=release["version"].lstrip("v"))


def simulate_local_update(new_version: str = "test") -> None:
    """Stage from the current directory and run the update PS1 — for local testing only."""
    if IS_FROZEN:
        raise RuntimeError("Local source-update simulation is unavailable in an MSI install.")
    _force_remove(_STAGING_DIR)
    _STAGING_DIR.mkdir()
    src = _STAGING_DIR / "extracted" / "local-test"
    src.mkdir(parents=True)

    for f in APP_DIR.glob("*.py"):
        shutil.copy2(f, src / f.name)
    if (APP_DIR / "requirements.txt").exists():
        shutil.copy2(APP_DIR / "requirements.txt", src / "requirements.txt")
    templates_src = APP_DIR / "templates"
    if templates_src.exists():
        shutil.copytree(templates_src, src / "templates")

    _launch_ps1(src=src, new_version=new_version)


def _write_shortcut(shortcut: Path) -> None:
    launcher = APP_DIR / "start_reminders.vbs"
    ps = (
        f'$sh = New-Object -ComObject WScript.Shell; '
        f'$sc = $sh.CreateShortcut("{shortcut}"); '
        f'$sc.TargetPath = "C:\\\\WINDOWS\\\\system32\\\\wscript.exe"; '
        f'$sc.Arguments = \'"{launcher}"\'; '
        f'$sc.WorkingDirectory = "{APP_DIR}"; '
        f'$sc.Save()'
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def _ensure_shortcut(shortcut: Path) -> None:
    """Create or repair a shortcut so it points to the current install location."""
    launcher = APP_DIR / "start_reminders.vbs"
    expected_args = f'"{launcher}"'
    try:
        if shortcut.exists():
            ps_check = (
                f'$sh = New-Object -ComObject WScript.Shell; '
                f'$sc = $sh.CreateShortcut("{shortcut}"); '
                f'$sc.Arguments'
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_check],
                capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.stdout.strip() == expected_args:
                return  # already correct
        _write_shortcut(shortcut)
    except Exception as e:
        print(f"[updater] ensure_shortcut({shortcut.name}) failed: {e}")


def ensure_startup_shortcut() -> None:
    if IS_FROZEN:
        return  # owned by Windows Installer
    startup = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"
    _ensure_shortcut(startup / "JiraReminders.lnk")


def ensure_desktop_shortcut() -> None:
    if IS_FROZEN:
        return  # owned by Windows Installer
    import ctypes.wintypes
    buf = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
    ctypes.windll.shell32.SHGetFolderPathW(0, 0x0000, 0, 0, buf)  # CSIDL_DESKTOP
    desktop = Path(buf.value)
    _ensure_shortcut(desktop / "Jira Reminders.lnk")


def remove_startup_shortcut() -> bool:
    """Delete the Windows Startup shortcut. Returns True if it existed."""
    startup   = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"
    shortcut  = startup / "JiraReminders.lnk"
    if shortcut.exists():
        shortcut.unlink()
        return True
    return False


def launch_installed_uninstaller() -> bool:
    """Launch the MSI uninstall UI for the current user's installed app."""
    if not IS_FROZEN or os.name != "nt":
        return False
    try:
        import winreg
    except ImportError:
        return False

    registry_locations = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, path in registry_locations:
        try:
            with winreg.OpenKey(hive, path) as root:
                for index in range(winreg.QueryInfoKey(root)[0]):
                    subkey_name = winreg.EnumKey(root, index)
                    try:
                        with winreg.OpenKey(root, subkey_name) as subkey:
                            display_name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                            if str(display_name).strip().casefold() != "jira reminders":
                                continue
                            uninstall = str(winreg.QueryValueEx(subkey, "UninstallString")[0])
                            product_code = re.search(
                                r"\{[0-9A-Fa-f-]{36}\}",
                                uninstall,
                            )
                            if product_code:
                                subprocess.Popen(
                                    ["msiexec.exe", "/x", product_code.group(0)],
                                    creationflags=subprocess.CREATE_NO_WINDOW,
                                )
                            else:
                                subprocess.Popen(uninstall)
                            return True
                    except OSError:
                        continue
        except OSError:
            continue
    return False
