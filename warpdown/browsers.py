"""Helpers for the "Browser extension" dialog: find the extension files, detect browsers, run the installer."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

from . import osutil
from .osutil import IS_WINDOWS
from .util import data_dir

PROJECT_URL = "https://github.com/marius-101/warpdown"
# Fill these in once the extension is published in the browser stores; the dialog then shows install buttons.
FIREFOX_ADDON_URL = ""      # e.g. https://addons.mozilla.org/firefox/addon/warpdown/ (set if a public listing is added)
# The Mozilla-signed add-on, attached to every GitHub release as "warpdown.xpi" (Firefox offers to install it).
FIREFOX_XPI_URL = PROJECT_URL + "/releases/latest/download/warpdown.xpi"
CHROME_STORE_URL = ""       # e.g. https://chromewebstore.google.com/detail/...
HOST_NAME = "org.warpdown.host"

FLATPAK_FIREFOX = "org.mozilla.firefox"
FLATPAK_CHROMIUM_FAMILY = ("com.google.Chrome", "org.chromium.Chromium", "com.brave.Browser",
                           "io.github.ungoogled_software.ungoogled_chromium")
NATIVE_CHROMIUM_FAMILY = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "brave-browser")
CHROMIUM_CONFIG_DIRS = ("google-chrome", "chromium", "BraveSoftware/Brave-Browser", "vivaldi", "microsoft-edge")


def project_root() -> Path:
    return osutil.app_dir()


def extension_dir() -> Path | None:
    p = project_root() / "extension"
    return p if (p / "manifest.json").exists() else None


def can_register() -> bool:
    return IS_WINDOWS or osutil.FROZEN or installer_script() is not None


def installer_script() -> Path | None:
    p = project_root() / "native" / "install.sh"
    return p if p.exists() else None


def firefox_manifest(text: str) -> str:
    """The manifest as Firefox wants it: `background.service_worker` is Chrome's key (Firefox uses `scripts`),
    and Mozilla's validator warns about it, so the Firefox package leaves it out."""
    m = json.loads(text)
    m.get("background", {}).pop("service_worker", None)
    return json.dumps(m, indent=2, ensure_ascii=False) + "\n"


def _zip_extension(src: Path, out: Path) -> None:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(src.rglob("*")):
            if not f.is_file():
                continue
            name = f.relative_to(src).as_posix()
            if name == "manifest.json":
                z.writestr(name, firefox_manifest(f.read_text(encoding="utf-8")))
            else:
                z.write(f, name)


def build_xpi() -> Path | None:
    """Package the extension as warpdown.xpi (Firefox needs a single file). None if the extension isn't bundled."""
    src = extension_dir()
    if src is None:
        return None
    for out_dir in (project_root() / "dist", data_dir()):
        try:
            out_dir.mkdir(exist_ok=True)
            out = out_dir / "warpdown.xpi"
            _zip_extension(src, out)
            return out
        except OSError:
            continue
    return None


def _is_snap_stub(path: str) -> bool:
    """Ubuntu ships /usr/bin/firefox as a stub that only works if the snap is installed."""
    try:
        return "requires the firefox snap" in Path(path).read_text(errors="ignore")[:600] \
            and not Path("/snap/bin/firefox").exists()
    except OSError:
        return False


def _windows_program(*relative: str) -> str | None:
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(var)
        for rel in relative:
            if base and (Path(base) / rel).is_file():
                return str(Path(base) / rel)
    return None


def firefox_command() -> list[str] | None:
    if IS_WINDOWS:
        p = shutil.which("firefox") or _windows_program("Mozilla Firefox/firefox.exe")
        return [p] if p else None
    if (Path.home() / ".var" / "app" / FLATPAK_FIREFOX).exists() and shutil.which("flatpak"):
        return ["flatpak", "run", FLATPAK_FIREFOX]
    p = shutil.which("firefox")
    return [p] if p and not _is_snap_stub(p) else None


def chromium_command() -> list[str] | None:
    if IS_WINDOWS:
        p = _windows_program("Google/Chrome/Application/chrome.exe", "Microsoft/Edge/Application/msedge.exe",
                             "BraveSoftware/Brave-Browser/Application/brave.exe")
        return [p] if p else None
    for name in NATIVE_CHROMIUM_FAMILY:
        p = shutil.which(name)
        if p:
            return [p]
    if shutil.which("flatpak"):
        for app in FLATPAK_CHROMIUM_FAMILY:
            if (Path.home() / ".var" / "app" / app).exists():
                return ["flatpak", "run", app]
    return None


def open_in_browser(cmd: list[str], url: str) -> bool:
    try:
        subprocess.Popen([*cmd, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **osutil.detached())
        return True
    except OSError:
        return False


# ---- Windows: browsers find the native host through registry keys that point at a manifest file ----
WINDOWS_KEYS = {"Firefox": r"Software\Mozilla\NativeMessagingHosts",
                "Chrome": r"Software\Google\Chrome\NativeMessagingHosts",
                "Edge": r"Software\Microsoft\Edge\NativeMessagingHosts",
                "Brave": r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts"}
FF_IDS = ["warpdown@marius-101.github.io", "warpdown@warpdown.local"]


def host_executable() -> str:
    exe = osutil.helper("warpdown-host")
    if exe:
        return exe
    wrapper = data_dir() / "native" / "warpdown-host.cmd"          # running from source on Windows
    wrapper.parent.mkdir(exist_ok=True)
    import sys
    wrapper.write_text(f'@echo off\r\nset PYTHONPATH={project_root()}\r\n"{sys.executable}" '
                       f'"{project_root() / "native" / "warpdown_host.py"}"\r\n')
    return str(wrapper)


def register_windows(chrome_extension_id: str = "") -> tuple[bool, str]:
    """Write the host manifests and the registry keys (current user only, no administrator rights needed)."""
    import winreg
    folder = data_dir() / "native"
    folder.mkdir(exist_ok=True)
    host = host_executable()
    base = {"name": HOST_NAME, "description": "Warpdown native host", "path": host, "type": "stdio"}
    ff = folder / f"{HOST_NAME}.firefox.json"
    ff.write_text(json.dumps({**base, "allowed_extensions": FF_IDS}, indent=1))
    done = ["Firefox"]
    winreg.SetValue(winreg.HKEY_CURRENT_USER, WINDOWS_KEYS["Firefox"] + "\\" + HOST_NAME, winreg.REG_SZ, str(ff))
    cid = chrome_extension_id.strip() or _saved_chrome_id()
    if cid:
        ch = folder / f"{HOST_NAME}.chrome.json"
        ch.write_text(json.dumps({**base, "allowed_origins": [f"chrome-extension://{cid}/"]}, indent=1))
        for name in ("Chrome", "Edge", "Brave"):
            winreg.SetValue(winreg.HKEY_CURRENT_USER, WINDOWS_KEYS[name] + "\\" + HOST_NAME, winreg.REG_SZ, str(ch))
            done.append(name)
        (folder / "chrome_id.txt").write_text(cid)
    return True, "Registered for: " + ", ".join(done)


SYSTEM_FIREFOX_DIRS = ("/usr/lib/mozilla/native-messaging-hosts", "/usr/lib64/mozilla/native-messaging-hosts",
                       "/usr/local/lib/mozilla/native-messaging-hosts")


def _manifest(host: str, kind: str, chrome_id: str = "") -> str:
    base = {"name": HOST_NAME, "description": "Warpdown native host", "path": host, "type": "stdio"}
    if kind == "firefox":
        return json.dumps({**base, "allowed_extensions": FF_IDS}, indent=1)
    return json.dumps({**base, "allowed_origins": [f"chrome-extension://{chrome_id}/"]}, indent=1)


def register_unix(chrome_extension_id: str = "") -> tuple[bool, str]:
    """Linux, for the installed program (no install.sh): write the host manifests for the browsers of this user.
    In a Flatpak the browser must start the host through `flatpak run`, so a small wrapper script is used."""
    home = Path.home()
    flatpak_id = os.environ.get("FLATPAK_ID", "")
    if flatpak_id:
        wrapper = data_dir() / "warpdown-host-flatpak"
        wrapper.write_text(f'#!/bin/sh\nexec flatpak run --command=/app/warpdown/warpdown-host {flatpak_id} "$@"\n')
        wrapper.chmod(0o755)
        host = str(wrapper)
    else:
        host = host_executable()
    done = []

    def write(folder: Path, kind: str, cid: str = "", path: str = "") -> None:
        folder.mkdir(parents=True, exist_ok=True)
        text = _manifest(path or host, kind, cid)
        (folder / f"{HOST_NAME}.json").write_text(text)

    write(home / ".mozilla" / "native-messaging-hosts", "firefox")
    done.append("Firefox")
    if (home / ".var" / "app" / FLATPAK_FIREFOX).exists() and not flatpak_id:
        spawn = home / ".var" / "app" / FLATPAK_FIREFOX / ".mozilla" / "warpdown-host-flatpak"
        spawn.write_text(f'#!/bin/sh\nexec flatpak-spawn --host "{host}"\n')
        spawn.chmod(0o755)
        write(spawn.parent / "native-messaging-hosts", "firefox", path=str(spawn))
        done.append("Firefox (Flatpak)")
    cid = chrome_extension_id.strip() or _saved_chrome_id()
    if cid:
        for d in CHROMIUM_CONFIG_DIRS:
            if (home / ".config" / d).exists():
                write(home / ".config" / d / "NativeMessagingHosts", "chrome", cid)
                done.append(d.split("/")[-1])
        (data_dir() / "native").mkdir(exist_ok=True)
        (data_dir() / "native" / "chrome_id.txt").write_text(cid)
    return True, "Registered for: " + ", ".join(done)


def _saved_chrome_id() -> str:
    try:
        return (data_dir() / "native" / "chrome_id.txt").read_text().strip()
    except OSError:
        return ""


def _windows_status() -> list[tuple[str, bool]]:
    import winreg
    out = []
    for name, key in WINDOWS_KEYS.items():
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key + "\\" + HOST_NAME):
                ok = True
        except OSError:
            ok = False
        if ok or name == "Firefox":
            out.append((name, ok))
    return out


def registration_status() -> list[tuple[str, bool]]:
    """Is the native-messaging host registered? One (label, registered) pair per browser family found."""
    if IS_WINDOWS:
        return _windows_status()
    home = Path.home()
    out = [("Firefox", (home / ".mozilla" / "native-messaging-hosts" / f"{HOST_NAME}.json").exists())]
    if (home / ".var" / "app" / FLATPAK_FIREFOX).exists():
        out.append(("Firefox (Flatpak)", (home / ".var" / "app" / FLATPAK_FIREFOX / ".mozilla" /
                                          "native-messaging-hosts" / f"{HOST_NAME}.json").exists()))
    out = [(n, ok or any(Path(d, f"{HOST_NAME}.json").exists() for d in SYSTEM_FIREFOX_DIRS)) if n == "Firefox" else (n, ok)
           for n, ok in out]                                    # a .deb installs the Firefox manifest system-wide
    cfg = home / ".config"
    present = [d for d in CHROMIUM_CONFIG_DIRS if (cfg / d).exists()]
    if present:
        done = any((cfg / d / "NativeMessagingHosts" / f"{HOST_NAME}.json").exists() for d in present)
        out.append(("Chrome / Chromium / Brave", done))
    return out


def run_installer(chrome_extension_id: str = "") -> tuple[bool, str]:
    """Run native/install.sh (registers the browser host). Only available when running from a source checkout."""
    if IS_WINDOWS:
        try:
            return register_windows(chrome_extension_id)
        except OSError as e:
            return False, str(e)
    if osutil.FROZEN or (installer_script() is None and not IS_WINDOWS and os.environ.get("FLATPAK_ID")):
        try:
            return register_unix(chrome_extension_id)
        except OSError as e:
            return False, str(e)
    script = installer_script()
    if script is None:
        return False, "The installer script isn't bundled with this installation."
    env = dict(os.environ)
    if chrome_extension_id:
        env["WARPDOWN_CHROME_EXTENSION_ID"] = chrome_extension_id.strip()
    try:
        r = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    text = (r.stdout + r.stderr).strip()
    return r.returncode == 0, text[-400:]
