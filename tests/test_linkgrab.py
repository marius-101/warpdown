import sys
from pathlib import Path
import os
import stat
import time

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QDialog

from warpdown import app as appmod
from warpdown import gui, ipc, linkgrab as g
from warpdown.manager import COMPLETED, PAUSED, DownloadManager

RAW = [
    {"url": "https://x.org/a/Report%20Q1.pdf#p2", "text": "  Q1   report ", "source": "link"},
    {"url": "https://x.org/a/Report%20Q1.pdf", "text": "", "source": "link"},           # same file, no fragment
    {"url": "https://x.org/about", "text": "About us", "source": "link"},                # a web page
    {"url": "https://x.org/img/cat.png", "text": "a cat", "source": "image"},
    {"url": "https://x.org/v/clip.mp4", "source": "media"},
    {"url": "https://x.org/music/song.mp3", "text": "Song", "source": "link"},
    {"url": "https://x.org/dl?id=7", "text": "Download tool", "source": "link"},          # no file type
    {"url": "javascript:void(0)"}, {"url": "mailto:a@b.c"}, {"url": "ftp://x.org/f.zip"}, {"url": ""}, "junk", None,
]


def test_classify_dedupe_and_drop_unusable():
    links = g.build_links(RAW)
    by_name = {l.name: l for l in links}
    assert set(by_name) == {"Report Q1.pdf", "about", "cat.png", "clip.mp4", "song.mp3", "dl"}
    assert by_name["Report Q1.pdf"].text == "Q1 report" and by_name["Report Q1.pdf"].category == "Documents"
    assert by_name["cat.png"].category == "Images" and by_name["clip.mp4"].category == "Video"
    assert by_name["song.mp3"].category == "Music" and by_name["about"].category == g.KIND_PAGES
    assert by_name["dl"].category == g.KIND_PAGES                         # no extension: treated as a page
    assert len(links) == 6                                                # duplicates and non-http(s) are gone
    assert g.category_counts(links)["Documents"] == 1
    assert len(g.build_links([{"url": f"http://x/{i}.bin"} for i in range(g.MAX_LINKS + 50)])) == g.MAX_LINKS


def test_filters():
    links = g.build_links(RAW)
    names = lambda **kw: sorted(l.name for l in links if g.matches(l, **kw))
    assert names() == ["Report Q1.pdf", "cat.png", "clip.mp4", "song.mp3"]       # web pages hidden by default
    assert names(hide_pages=False) == sorted(l.name for l in links)
    assert names(kind=g.KIND_PAGES) == ["about", "dl"]
    assert names(kind="Images") == ["cat.png"] and names(kind="Video") == ["clip.mp4"]
    assert names(text="q1 REPORT") == ["Report Q1.pdf"]                          # words, any order, any case; link text counts
    assert names(text="x.org song") == ["song.mp3"] and names(text="nonexistent") == []
    assert g.same_site("https://X.org/a", "x.org") and not g.same_site("https://cdn.x.org/a", "x.org")


# ------------------------------------------------------------------ the dialog
@pytest.fixture()
def env(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    qapp.setOrganizationName("warpdown-test5")
    qapp.setApplicationName("warpdown-test5")
    QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, str(tmp_path / "conf"))
    QSettings().clear()
    QSettings().setValue("download_dir", str(tmp_path / "dl"))
    m = DownloadManager()
    w = gui.MainWindow(m)
    yield w, m, tmp_path
    m.shutdown()


def test_dialog_filters_selection_and_counter(env):
    w, m, tmp = env
    d = gui.LinksDialog(w, g.build_links(RAW), "Example page", existing={"https://x.org/music/song.mp3"})
    ok = d.buttons.button(gui.QDialogButtonBox.Ok)
    assert [l.name for l in d.selected_links()] == ["Report Q1.pdf", "cat.png", "clip.mp4"]   # pre-ticked; known one is not
    assert all("#" not in l.url for l in d.selected_links())                                  # fragments are stripped
    assert d.count.text() == "3 selected of 4 shown" and ok.text() == "Download 3 files"
    assert [d.kind.itemText(i) for i in range(d.kind.count())] == [
        "All files", "Video (1)", "Music (1)", "Documents (1)", "Images (1)", "Web pages (2)"]

    d.kind.setCurrentIndex(d.kind.findData("Images"))                  # filter: only images
    assert [l.name for l in d.selected_links()] == ["cat.png"] and ok.text() == "Download 1 file"
    d.kind.setCurrentIndex(0)
    d.hide_pages.setChecked(False)                                      # show the page-like links too
    assert d.count.text().endswith("of 6 shown")
    d._set_all(Qt.Unchecked)
    assert d.selected_links() == [] and not ok.isEnabled()              # nothing selected: cannot download
    d.filter.setText("mp4")
    d._set_all(Qt.Checked)
    assert [l.name for l in d.selected_links()] == ["clip.mp4"]
    d._invert()
    assert d.selected_links() == []
    d.filter.setText("")                                                # everything is shown again...
    assert d.selected_links() == []                                     # ...and nothing is ticked (clip.mp4 was inverted off)
    d.kind.setCurrentIndex(d.kind.findData("Video"))
    d.tree.topLevelItem(0).setCheckState(0, Qt.Checked)                 # a hidden tick must not count: ticked row 0 is a PDF
    assert d.selected_links() == []


def test_long_lists_are_not_pre_ticked(env):
    w, m, tmp = env
    many = g.build_links([{"url": f"https://x.org/f{i}.zip", "source": "link"} for i in range(150)])
    d = gui.LinksDialog(w, many, "Big page")
    assert d.selected_links() == [] and d.count.text() == "0 selected of 150 shown"
    d._set_all(Qt.Checked)
    assert len(d.selected_links()) == 150


def test_links_dialog_adds_the_picked_downloads_in_one_batch(env, monkeypatch):
    w, m, tmp = env
    added_signals = []
    m.added.connect(added_signals.append)

    def fake_exec(self):
        self.start_now.setChecked(False)                                # keep the test offline: items start paused
        self.sort.setChecked(True)
        return QDialog.Accepted
    monkeypatch.setattr(gui.LinksDialog, "exec", fake_exec)
    headers = {"cookie": "sid=1", "cookie_origin": "x.org", "referer": "", "user_agent": "UA"}
    w.links_dialog(RAW, headers, "https://x.org/page", "Example page")
    items = list(m.items.values())
    assert sorted(i.url for i in items) == sorted(
        ["https://x.org/a/Report%20Q1.pdf", "https://x.org/img/cat.png", "https://x.org/v/clip.mp4",
         "https://x.org/music/song.mp3"])                                # all shown, file-like links are pre-ticked
    assert all(i.status == PAUSED and i.sort and i.directory == str(tmp / "dl") for i in items)
    assert all(i.headers["referer"] == "https://x.org/page" and i.headers["cookie"] == "sid=1" for i in items)
    assert len(added_signals) == 1                                      # one refresh for the whole batch
    assert "Added 4 downloads" in w.status.currentMessage()
    assert json_on_disk(tmp)                                            # saved once, to disk


def json_on_disk(tmp):
    import json
    return len(json.loads((tmp / "data" / "warpdown" / "downloads.json").read_text())) == 4


def test_no_links_found_tells_the_user(env, monkeypatch):
    w, m, tmp = env
    shown = []
    monkeypatch.setattr(gui.QMessageBox, "information", staticmethod(lambda *a: shown.append(a[2])))
    w.links_dialog([{"url": "javascript:void(0)"}], {}, "", "")
    assert shown and "No downloadable links" in shown[0] and not m.items


# ------------------------------------------------------------------ cookies, privacy, routing
def test_page_cookies_are_only_sent_to_the_page_site(env):
    w, m, tmp = env
    hdrs = {"cookie": "sid=1", "cookie_origin": "x.org", "referer": "https://x.org/p", "user_agent": "UA"}
    same = m.add("https://x.org/a.zip", str(tmp), headers=hdrs, start=False)
    other = m.add("https://cdn.example.net/a.zip", str(tmp), headers=hdrs, start=False)
    plain = m.add("https://y.org/a.zip", str(tmp), headers={"cookie": "k=v"}, start=False)       # a normal single download
    assert m._browser_headers(same)["Cookie"] == "sid=1"
    assert "Cookie" not in m._browser_headers(other) and m._browser_headers(other)["Referer"] == "https://x.org/p"
    assert m._browser_headers(plain)["Cookie"] == "k=v"                  # unchanged behaviour for single downloads
    same.url = "https://cdn.example.net/redirected.zip"                  # after a redirect to another host
    assert "Cookie" not in m._browser_headers(same)


@pytest.mark.skipif(sys.platform.startswith('win'), reason='POSIX file modes')
def test_download_list_file_is_private_and_forgets_cookies_of_finished_downloads(env):
    import json
    w, m, tmp = env
    done = m.add("https://x.org/done.zip", str(tmp), headers={"cookie": "secret=1", "referer": "r"}, start=False)
    todo = m.add("https://x.org/todo.zip", str(tmp), headers={"cookie": "secret=2"}, start=False)
    done.status = COMPLETED
    m.save()
    f = tmp / "data" / "warpdown" / "downloads.json"
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600                      # only you can read it
    rows = {r["url"]: r for r in json.loads(f.read_text())}
    assert "cookie" not in rows["https://x.org/done.zip"]["headers"] and rows["https://x.org/done.zip"]["headers"]["referer"] == "r"
    assert rows["https://x.org/todo.zip"]["headers"]["cookie"] == "secret=2"   # still needed to resume


class FakeWin:
    def __init__(self):
        self.calls = []

    def show_raise(self): self.calls.append(("raise",))
    def add_dialog(self, *a): self.calls.append(("add", a))
    def links_dialog(self, *a): self.calls.append(("links", a))


def test_messages_from_the_extension_are_routed():
    w = FakeWin()
    appmod.dispatch(w, {"action": "links", "links": [{"url": "http://x/a"}], "headers": {"cookie": "c"}, "page": "P", "title": "T"})
    assert w.calls == [("raise",), ("links", ([{"url": "http://x/a"}], {"cookie": "c"}, "P", "T"))]
    w.calls.clear()
    appmod.dispatch(w, {"action": "links", "links": []})                  # nothing found: just raise the window
    appmod.dispatch(w, {"action": "show"})
    assert w.calls == [("raise",), ("raise",)]
    w.calls.clear()
    appmod.dispatch(w, {"action": "add", "url": "http://x/f.zip", "headers": {}, "filename": "f", "subs": [], "mode": ""})
    assert w.calls[1][0] == "add"


def test_a_page_with_thousands_of_links_fits_through_the_local_socket(tmp_path):
    """The browser helper is a separate process; its message must arrive complete however big the page is (~3 MB here)."""
    import queue
    import subprocess
    import sys
    import tempfile
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    sock_dir = tempfile.mkdtemp(prefix="wd")
    sock = os.path.join(sock_dir, "s.sock")
    q = queue.SimpleQueue()
    srv = ipc.IpcServer(q, path=sock)
    assert srv.listen()
    code = f"""
import sys
sys.path.insert(0, {str(root)!r})
from warpdown import ipc
ipc.socket_path = lambda: {sock!r}
big = {{"action": "links", "links": [{{"url": f"https://example.org/files/{{i}}/some-long-file-name-{{i}}.zip",
                                      "text": "x" * 40, "source": "link"}} for i in range(30000)]}}
sys.exit(0 if ipc.send(big) else 3)
"""
    proc = subprocess.run([sys.executable, "-c", code], timeout=60)
    msg = q.get(timeout=30)
    srv.close()
    os.rmdir(sock_dir)
    assert proc.returncode == 0                                              # the sender saw everything written
    links = msg[1][0]["links"]
    assert len(links) == 30000 and links[29999]["url"] == "https://example.org/files/29999/some-long-file-name-29999.zip"
