import pytest
from PySide6.QtCore import QSettings, Qt

from warpdown import browsers, gui
from warpdown.manager import DownloadManager
from warpdown.util import CATEGORIES, category_folder


class FakeDesktop:
    opened: list[str] = []

    @staticmethod
    def openUrl(url):
        FakeDesktop.opened.append(url.toLocalFile() or url.toString())
        return True


@pytest.fixture()
def win(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    qapp.setOrganizationName("warpdown-test2")
    qapp.setApplicationName("warpdown-test2")
    QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, str(tmp_path / "conf"))
    QSettings().clear()
    root = tmp_path / "dl"
    QSettings().setValue("download_dir", str(root))
    monkeypatch.setattr(gui, "QDesktopServices", FakeDesktop)
    FakeDesktop.opened = []
    m = DownloadManager()
    w = gui.MainWindow(m)
    w.root = root
    yield w
    m.shutdown()


def row(w, name):
    return next(w.cats.item(i) for i in range(w.cats.count()) if w.cats.item(i).text() == name)


def test_category_folder_mapping(tmp_path):
    assert category_folder(tmp_path, "All Downloads") == tmp_path
    for c in (*CATEGORIES, "Other"):
        assert category_folder(tmp_path, c) == tmp_path / c
    assert category_folder(tmp_path, "Unfinished") is None and category_folder(tmp_path, "Finished") is None


def test_double_click_opens_category_folders(win):
    (win.root / "Video").mkdir(parents=True)
    win.open_category_folder(row(win, "Video"))
    assert FakeDesktop.opened == [str(win.root / "Video")]

    win.open_category_folder(row(win, "Music"))                     # folder doesn't exist yet: explain, don't open
    assert FakeDesktop.opened == [str(win.root / "Video")] and "Music" in win.status.currentMessage()

    for name in ("Unfinished", "Finished"):                         # filters, not folders
        win.open_category_folder(row(win, name))
    assert len(FakeDesktop.opened) == 1 and "filter" in win.status.currentMessage()

    win.open_category_folder(row(win, "All Downloads"))             # the main folder is created on demand
    assert FakeDesktop.opened[-1] == str(win.root) and win.root.is_dir()


def test_open_folder_button_uses_selection_else_root(win):
    d = win.root / "Video"
    d.mkdir(parents=True)
    win.open_folder()                                               # nothing selected -> main folder
    assert FakeDesktop.opened[-1] == str(win.root)
    it = win.mgr.add("http://x/a.mp4", str(d), filename="a.mp4", start=False)
    win.model.rebuild()
    win.table.selectRow(0)
    assert win._selected_ids() == [it.id]
    win.open_folder()                                               # selected download -> its own folder
    assert FakeDesktop.opened[-1] == str(d)


def test_toolbar_order_settings_last(win):
    names = [a.text() for a in win.toolbar.actions() if a.text()]
    assert names == ["Add URL", "Batch", "Resume", "Pause", "Delete", "Open Folder", "Schedule", "Statistics", "Browser", "Settings"]


# ---------------- browser helpers ----------------
def test_build_xpi_contains_the_extension(tmp_path, monkeypatch):
    import zipfile
    root = tmp_path / "proj"
    (root / "extension" / "icons").mkdir(parents=True)
    (root / "extension" / "manifest.json").write_text("{}")
    (root / "extension" / "icons" / "16.png").write_bytes(b"png")
    monkeypatch.setattr(browsers, "project_root", lambda: root)
    xpi = browsers.build_xpi()
    assert xpi == root / "dist" / "warpdown.xpi"
    assert sorted(zipfile.ZipFile(xpi).namelist()) == ["icons/16.png", "manifest.json"]
    monkeypatch.setattr(browsers, "project_root", lambda: tmp_path / "nothing")
    assert browsers.build_xpi() is None


def test_browser_detection(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    real_ff = tmp_path / "firefox"                                   # a stand-in for a genuine Firefox launcher
    real_ff.write_text("#!/bin/sh\nexec firefox-bin \"$@\"\n")
    which = {"flatpak": "/usr/bin/flatpak", "firefox": str(real_ff), "google-chrome": "/usr/bin/google-chrome"}
    monkeypatch.setattr(browsers.shutil, "which", lambda n: which.get(n))
    assert browsers.firefox_command() == [str(real_ff)]
    assert browsers.chromium_command() == ["/usr/bin/google-chrome"]          # native preferred

    (tmp_path / ".var/app/org.mozilla.firefox").mkdir(parents=True)
    assert browsers.firefox_command() == ["flatpak", "run", "org.mozilla.firefox"]   # Flatpak Firefox wins

    stub = tmp_path / "firefox-stub"
    stub.write_text("#!/bin/sh\necho 'Command requires the firefox snap to be installed'")
    shutil_which = {"firefox": str(stub)}
    monkeypatch.setattr(browsers.shutil, "which", lambda n: shutil_which.get(n))
    (tmp_path / ".var/app/org.mozilla.firefox").rmdir()
    assert browsers.firefox_command() is None                                   # Ubuntu's snap stub is not Firefox

    monkeypatch.setattr(browsers.shutil, "which", lambda n: "/usr/bin/flatpak" if n == "flatpak" else None)
    (tmp_path / ".var/app/com.brave.Browser").mkdir(parents=True)
    assert browsers.chromium_command() == ["flatpak", "run", "com.brave.Browser"]


def test_registration_status(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert browsers.registration_status() == [("Firefox", False)]
    mf = tmp_path / ".mozilla/native-messaging-hosts"
    mf.mkdir(parents=True)
    (mf / "org.warpdown.host.json").write_text("{}")
    (tmp_path / ".config/google-chrome").mkdir(parents=True)
    st = dict(browsers.registration_status())
    assert st["Firefox"] is True and st["Chrome / Chromium / Brave"] is False
    nm = tmp_path / ".config/google-chrome/NativeMessagingHosts"
    nm.mkdir()
    (nm / "org.warpdown.host.json").write_text("{}")
    assert dict(browsers.registration_status())["Chrome / Chromium / Brave"] is True


def test_installer_missing_is_reported(monkeypatch):
    monkeypatch.setattr(browsers, "installer_script", lambda: None)
    ok, msg = browsers.run_installer()
    assert not ok and "isn't bundled" in msg


def test_extension_dialog_actions(qapp, monkeypatch):
    calls = {}
    monkeypatch.setattr(browsers, "firefox_command", lambda: ["ff"])
    monkeypatch.setattr(browsers, "chromium_command", lambda: ["ch"])
    monkeypatch.setattr(browsers, "open_in_browser", lambda cmd, url: calls.setdefault("open", []).append((cmd, url)) or True)
    monkeypatch.setattr(browsers, "run_installer", lambda cid="": calls.setdefault("install", []).append(cid) or (True, "ok"))
    d = gui.ExtensionDialog()
    d.btn_ff_install.click()                                                      # the one-click, signed install
    assert calls["open"] == [(["ff"], browsers.FIREFOX_XPI_URL)]
    calls["open"].clear()
    d.btn_ff.click(); d.btn_ch.click()
    assert calls["open"] == [(["ff"], "about:debugging#/runtime/this-firefox"), (["ch"], "chrome://extensions")]

    d.chrome_id.setText("not an id")
    d.btn_reg.click()
    assert "install" not in calls and "doesn't look like" in d.result.text()    # bad IDs are rejected
    d.chrome_id.setText("a" * 32)
    d.btn_reg.click()
    assert calls["install"] == ["a" * 32] and d.result.text().startswith("Done")
    d.btn_fix.click()
    assert calls["install"][-1] == ""

    monkeypatch.setattr(browsers, "firefox_command", lambda: None)
    d.btn_ff.click()
    assert "wasn't found" in d.result.text()
    d.btn_ff_install.click()
    assert "wasn't found" in d.result.text()


def test_signed_addon_url_points_at_the_latest_release_asset():
    assert browsers.FIREFOX_XPI_URL == "https://github.com/marius-101/warpdown/releases/latest/download/warpdown.xpi"


# ---------------- Firefox add-on package ----------------
def _manifest():
    import json
    from pathlib import Path
    return json.loads((Path(__file__).resolve().parent.parent / "extension" / "manifest.json").read_text())


def test_manifest_is_ready_for_addons_mozilla_org():
    import re
    m = _manifest()
    g = m["browser_specific_settings"]["gecko"]
    assert re.fullmatch(r"[A-Za-z0-9._-]+@[A-Za-z0-9.-]+", g["id"]) and g["id"] == "warpdown@marius-101.github.io"
    assert g["data_collection_permissions"] == {"required": ["none"]}      # mandatory for new add-ons since Nov 2025
    assert int(g["strict_min_version"].split(".")[0]) >= 140                # first Firefox that understands it
    assert len(m["description"]) <= 132 and m["manifest_version"] == 3
    assert {"48", "128"} <= set(m["icons"])
    assert "scripts" in m["background"]                                     # Firefox uses background scripts


def test_manifest_files_exist():
    from pathlib import Path
    ext = Path(__file__).resolve().parent.parent / "extension"
    m = _manifest()
    refs = {m["action"]["default_popup"], *m["icons"].values(), *m["action"]["default_icon"].values(),
            *m["background"]["scripts"], *(f for cs in m["content_scripts"] for f in cs["js"])}
    assert [r for r in refs if not (ext / r).exists()] == []


def test_firefox_package_omits_chrome_only_key_but_source_keeps_it(tmp_path, monkeypatch):
    import json
    import zipfile
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    monkeypatch.setattr(browsers, "project_root", lambda: root)
    monkeypatch.setattr(browsers, "data_dir", lambda: tmp_path)
    out = tmp_path / "pkg.xpi"
    browsers._zip_extension(root / "extension", out)
    z = zipfile.ZipFile(out)
    fx = json.loads(z.read("manifest.json"))
    assert "service_worker" not in fx["background"] and fx["background"]["scripts"] == ["background.js"]
    assert "service_worker" in _manifest()["background"]                    # the Chrome build still needs it
    assert {"background.js", "content.js", "popup.html", "popup.js", "icons/128.png"} <= set(z.namelist())


def test_installer_trusts_the_published_and_the_development_id():
    from pathlib import Path
    s = (Path(__file__).resolve().parent.parent / "native" / "install.sh").read_text()
    assert 'FF_ID="warpdown@marius-101.github.io"' in s and 'FF_ID_DEV="warpdown@warpdown.local"' in s
    assert s.count('"allowed_extensions":["$FF_ID","$FF_ID_DEV"]') == 2        # regular + Flatpak Firefox
