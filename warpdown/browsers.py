"""Helpers for the "Browser extension" dialog: find the extension files, detect browsers, run the installer."""

from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

from .util import data_dir

PROJECT_URL = "https://github.com/marius-101/warpdown"
# Fill these in once the extension is published in the browser stores; the dialog then shows install buttons.
FIREFOX_ADDON_URL = ""      # e.g. https://addons.mozilla.org/firefox/addon/warpdown/
CHROME_STORE_URL = ""       # e.g. https://chromewebstore.google.com/detail/...
HOST_NAME = "org.warpdown.host"

FLATPAK_FIREFOX = "org.mozilla.firefox"
FLATPAK_CHROMIUM_FAMILY = ("com.google.Chrome", "org.chromium.Chromium", "com.brave.Browser",
                           "io.github.ungoogled_software.ungoogled_chromium")
NATIVE_CHROMIUM_FAMILY = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "brave-browser")
CHROMIUM_CONFIG_DIRS = ("google-chrome", "chromium", "BraveSoftware/Brave-Browser", "vivaldi", "microsoft-edge")


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def extension_dir() -> Path | None:
    p = project_root() / "extension"
    return p if (p / "manifest.json").exists() else None


def installer_script() -> Path | None:
    p = project_root() / "native" / "install.sh"
    return p if p.exists() else None


def _zip_extension(src: Path, out: Path) -> None:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(src.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(src).as_posix())


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


def firefox_command() -> list[str] | None:
    if (Path.home() / ".var" / "app" / FLATPAK_FIREFOX).exists() and shutil.which("flatpak"):
        return ["flatpak", "run", FLATPAK_FIREFOX]
    p = shutil.which("firefox")
    return [p] if p and not _is_snap_stub(p) else None


def chromium_command() -> list[str] | None:
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
        subprocess.Popen([*cmd, url], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def registration_status() -> list[tuple[str, bool]]:
    """Is the native-messaging host registered? One (label, registered) pair per browser family found."""
    home = Path.home()
    out = [("Firefox", (home / ".mozilla" / "native-messaging-hosts" / f"{HOST_NAME}.json").exists())]
    if (home / ".var" / "app" / FLATPAK_FIREFOX).exists():
        out.append(("Firefox (Flatpak)", (home / ".var" / "app" / FLATPAK_FIREFOX / ".mozilla" /
                                          "native-messaging-hosts" / f"{HOST_NAME}.json").exists()))
    cfg = home / ".config"
    present = [d for d in CHROMIUM_CONFIG_DIRS if (cfg / d).exists()]
    if present:
        done = any((cfg / d / "NativeMessagingHosts" / f"{HOST_NAME}.json").exists() for d in present)
        out.append(("Chrome / Chromium / Brave", done))
    return out


def run_installer(chrome_extension_id: str = "") -> tuple[bool, str]:
    """Run native/install.sh (registers the browser host). Only available when running from a source checkout."""
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
