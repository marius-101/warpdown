import os
import time

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog

from warpdown import batch, gui, scheduler as sch
from warpdown.clipboard import ClipboardWatcher
from warpdown.manager import PAUSED, QUEUED, RETRYING, DownloadManager


@pytest.fixture()
def env(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    qapp.setOrganizationName("warpdown-test6")
    qapp.setApplicationName("warpdown-test6")
    QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, str(tmp_path / "conf"))
    QSettings().clear()
    QSettings().setValue("download_dir", str(tmp_path / "dl"))
    m = DownloadManager()
    m._begin = lambda it: None                                       # never touch the network in these tests
    w = gui.MainWindow(m)
    yield w, m, tmp_path
    w.clip.set_enabled(False)
    m.shutdown()


def pump(qapp, seconds=0.1):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.005)


# ---------------------------------------------------------------- status texts
def test_status_column_explains_waiting_states(env):
    w, m, tmp = env
    a = m.add("http://x/a.bin", str(tmp), start=False)
    w.model.rebuild()
    col = 5
    status = lambda: w.model.data(w.model.index(0, col), Qt.DisplayRole)
    m._fail(a, "Server error '503 Service Unavailable'")
    assert status().startswith("Retrying in") and "(attempt 1/5)" in status() and "server had a problem" in status()
    a.status, a.error, a.held = PAUSED, "", True
    assert status() == "Paused by the schedule"
    a.held, a.status = False, QUEUED
    m._gate = False
    assert status() == "Scheduled (waiting)"
    m._gate = True
    assert status() == "Queued"


# ---------------------------------------------------------------- batch dialog
def test_batch_dialog_preview_ranges_and_button(env):
    w, m, tmp = env
    d = gui.BatchDialog(w)
    ok = d.buttons.button(gui.QDialogButtonBox.Ok)
    assert not ok.isEnabled() and "0 downloads" in d.preview.text()
    d.text.setPlainText("# my list\nhttps://example.org/img[001-004].jpg\nhttps://example.org/a.zip\nnot a link\n")
    d._update()                                                      # (normally fires 0.2 s after typing stops)
    assert d.urls() == ["https://example.org/img001.jpg", "https://example.org/img002.jpg",
                        "https://example.org/img003.jpg", "https://example.org/img004.jpg", "https://example.org/a.zip"]
    assert ok.isEnabled() and ok.text() == "Download 5 files" and "from 2 lines" in d.preview.text()
    assert "1 line without a web address" in d.preview.text() and "img001.jpg" in d.preview.text()
    d.text.setPlainText("https://example.org/f[1-20000].bin")
    d._update()
    assert d.urls() == [] and "limit" in d.preview.text() or len(d.urls()) <= batch.LIMIT   # too many: refused / capped


def test_batch_dialog_types_text_and_updates_by_itself(env, qapp):
    w, m, tmp = env
    d = gui.BatchDialog(w, prefill="https://example.org/x[1-3].zip")
    assert d.buttons.button(gui.QDialogButtonBox.Ok).text() == "Download 3 files"      # prefilled: counted at once
    d.text.setPlainText("https://example.org/y[1-2].zip")
    pump(qapp, 0.5)                                                  # the 0.2 s timer re-reads the text
    assert d.buttons.button(gui.QDialogButtonBox.Ok).text() == "Download 2 files"


def test_main_window_batch_adds_everything_in_one_go(env, monkeypatch):
    w, m, tmp = env
    added = []
    m.added.connect(added.append)

    def fake_exec(self):
        self.start_now.setChecked(False)                             # keep the test offline: items start paused
        return QDialog.Accepted
    monkeypatch.setattr(gui.BatchDialog, "exec", fake_exec)
    w.batch_dialog("https://example.org/p[01-12].png")
    assert len(m.items) == 12 and len(added) == 1                    # one refresh for the whole batch
    assert all(i.status == PAUSED and i.directory == str(tmp / "dl") for i in m.items.values())
    assert sorted(i.url for i in m.items.values())[0] == "https://example.org/p01.png"
    assert "Added 12 downloads" in w.status.currentMessage()


# ---------------------------------------------------------------- scheduler dialog
def test_scheduler_dialog_round_trip(env):
    w, m, tmp = env
    start = sch.Schedule(True, (23, 30), (6, 15), frozenset({0, 1, 2, 3, 4}))
    d = gui.SchedulerDialog(w, start, sch.AFTER_SLEEP)
    assert d.schedule() == start and d.after_action() == sch.AFTER_SLEEP
    assert "23:30 to 06:15" in d.summary.text() and "Mon, Tue, Wed, Thu, Fri" in d.summary.text()
    d.use_stop.setChecked(False)
    assert d.schedule().stop is None and "then run until they are finished" in d.summary.text()
    assert not d.stop.isEnabled()
    d.enabled.setChecked(False)
    assert "scheduler is off" in d.summary.text()


def test_main_window_scheduler_saves_and_applies(env, monkeypatch):
    w, m, tmp = env
    chosen = sch.Schedule(True, (1, 0), (5, 0), frozenset({5, 6}))

    class Fake(gui.SchedulerDialog):
        def exec(self):
            self.enabled.setChecked(True)
            self.start.setTime(self.start.time().fromString("01:00", "HH:mm"))
            self.use_stop.setChecked(True)
            self.stop.setTime(self.stop.time().fromString("05:00", "HH:mm"))
            for i, c in enumerate(self.days):
                c.setChecked(i in (5, 6))
            self.after.setCurrentIndex(self.after.findData(sch.AFTER_POWEROFF))
            return QDialog.Accepted
    monkeypatch.setattr(gui, "SchedulerDialog", Fake)
    w.scheduler_dialog()
    assert m.schedule == chosen
    s = QSettings()
    assert (s.value("sched_start"), s.value("sched_stop"), s.value("sched_days"), s.value("after_action")) == (
        "01:00", "05:00", "5,6", "poweroff")
    # restart: the saved schedule is read back, and unchanged settings do not reset a running schedule
    m2 = DownloadManager()
    gui.apply_settings(m2)
    assert m2.schedule == chosen
    m2._gate = "marker"
    gui.apply_settings(m2)
    assert m2._gate == "marker"
    m2.shutdown()


# ---------------------------------------------------------------- when finished
@pytest.mark.parametrize("action,expected", [
    (sch.AFTER_NOTHING, None), (sch.AFTER_SLEEP, sch.power_command(sch.AFTER_SLEEP)), (sch.AFTER_POWEROFF, sch.power_command(sch.AFTER_POWEROFF))])
def test_after_finished_actions_ask_first(env, monkeypatch, action, expected):
    w, m, tmp = env
    QSettings().setValue("after_action", action)
    ran, asked = [], []
    monkeypatch.setattr(gui, "run_power_command", ran.append)
    monkeypatch.setattr(gui.CountdownDialog, "exec", lambda self: asked.append(self.what) or QDialog.Rejected)
    w._on_queue_finished()
    assert ran == []                                                 # cancelled (or nothing to ask): nothing happens
    assert bool(asked) == (expected is not None)
    monkeypatch.setattr(gui.CountdownDialog, "exec", lambda self: QDialog.Accepted)
    w._on_queue_finished()
    assert ran == ([expected] if expected else [])


def test_quit_after_finished_quits_the_app(env, monkeypatch, qapp):
    w, m, tmp = env
    QSettings().setValue("after_action", sch.AFTER_QUIT)
    quit_calls = []
    monkeypatch.setattr(gui.CountdownDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(type(qapp), "quit", lambda self: quit_calls.append(1))
    w._on_queue_finished()
    assert quit_calls == [1]


def test_countdown_dialog_counts_down_and_accepts_by_itself(env, qapp):
    w, m, tmp = env
    d = gui.CountdownDialog(w, sch.AFTER_SLEEP, seconds=2)
    assert "go to sleep in 2 seconds" in d.label.text()
    d._tick()
    assert "in 1 second." in d.label.text()
    d._tick()
    assert d.result() == QDialog.Accepted                            # time is up: do it


# ---------------------------------------------------------------- settings
def test_settings_dialog_has_retry_and_clipboard_options(env):
    w, m, tmp = env
    d = gui.SettingsDialog(w, m)
    assert d.retry.isChecked() and d.retries.value() == 5 and not d.clip.isChecked()      # sensible defaults
    s = QSettings()
    s.setValue("auto_retry", False); s.setValue("max_retries", 9); s.setValue("clipboard_watch", True)
    d2 = gui.SettingsDialog(w, m)
    assert not d2.retry.isChecked() and d2.retries.value() == 9 and d2.clip.isChecked()
    gui.apply_settings(m)
    assert m.auto_retry is False and m.max_retries == 9


# ---------------------------------------------------------------- clipboard watcher
def test_clipboard_watcher_offers_download_links_only(env, qapp):
    w, m, tmp = env
    found = []
    watcher = ClipboardWatcher()
    watcher._read_klipper = lambda: None                              # only the Qt clipboard path in this test
    watcher.urls_found.connect(found.append)
    cb = QGuiApplication.clipboard()
    cb.setText("https://example.org/old.zip")
    watcher.set_enabled(True)
    pump(qapp)
    assert found == []                                                # what was already there is not offered
    cb.setText("look: https://example.org/files/new.zip and https://example.org/page")
    pump(qapp)
    assert found == [["https://example.org/files/new.zip"]]           # the page is not a download
    cb.setText("https://example.org/files/new.zip")
    cb.setText("https://example.org/files/new.zip ")
    pump(qapp)
    assert len(found) == 1                                            # the same address again within a minute: not again
    watcher.remember("https://example.org/self.zip")                  # Warpdown's own "Copy URL"
    cb.setText("https://example.org/self.zip")
    pump(qapp)
    assert len(found) == 1
    watcher.set_enabled(False)
    cb.setText("https://example.org/off.zip")
    pump(qapp)
    assert len(found) == 1                                            # disabled: silent


def test_clipboard_watcher_polls_klipper_when_the_window_is_not_focused(env, qapp):
    w, m, tmp = env
    found = []
    watcher = ClipboardWatcher()
    box = {"text": "nothing yet"}
    watcher._read_klipper = lambda: box["text"]                       # what Klipper would answer over D-Bus
    watcher.urls_found.connect(found.append)
    watcher.set_enabled(True)
    watcher._poll()
    assert found == []
    box["text"] = "https://example.org/a.pdf\nhttps://example.org/b.pdf"
    watcher._poll()
    assert found == [["https://example.org/a.pdf", "https://example.org/b.pdf"]]
    watcher._poll()
    assert len(found) == 1                                            # unchanged text: not again
    watcher.set_enabled(False)


def test_window_offers_one_link_in_add_dialog_and_several_in_batch(env, qapp, monkeypatch):
    w, m, tmp = env
    calls = []
    monkeypatch.setattr(w, "add_dialog", lambda url, *a, **k: calls.append(("add", url)))
    monkeypatch.setattr(w, "batch_dialog", lambda text="": calls.append(("batch", text)))
    w._on_clipboard_urls(["https://example.org/one.zip"])
    pump(qapp)
    w._on_clipboard_urls(["https://example.org/a.zip", "https://example.org/b.zip"])
    pump(qapp)
    m.add("https://example.org/known.zip", str(tmp), start=False)
    w._on_clipboard_urls(["https://example.org/known.zip"])           # already in the list: ignored
    pump(qapp)
    assert calls == [("add", "https://example.org/one.zip"), ("batch", "https://example.org/a.zip\nhttps://example.org/b.zip")]


def test_tray_checkbox_toggles_the_setting(env):
    w, m, tmp = env
    assert not w.clip.enabled
    w.tray_clip.setChecked(True)
    assert w.clip.enabled and str(QSettings().value("clipboard_watch")).lower() == "true"
    w.tray_clip.setChecked(False)
    assert not w.clip.enabled


@pytest.mark.parametrize("action,expect_file,expect_dir", [(gui.DONE_NOTHING, False, False),
                                                           (gui.DONE_OPEN_FILE, True, False),
                                                           (gui.DONE_OPEN_FOLDER, False, True)])
def test_download_complete_action(env, monkeypatch, action, expect_file, expect_dir):
    from warpdown.manager import COMPLETED
    w, m, tmp = env
    QSettings().setValue("done_action", action)
    opened = []
    monkeypatch.setattr(gui.QDesktopServices, "openUrl", lambda url: opened.append(os.path.normpath(url.toLocalFile())))
    monkeypatch.setattr(w.tray, "showMessage", lambda *a, **k: None)
    it = m.add("http://127.0.0.1:1/a.bin", directory=str(tmp / "dl"))
    it.filename = "a.bin"
    it.status = COMPLETED
    w._notify_done(it.id)
    assert (str(it.path) in opened) == expect_file
    assert (it.directory in opened) == expect_dir
    opened.clear()
    w._open_last_done()                                              # clicking the notification shows the folder
    assert opened == [it.directory]


def test_settings_dialog_saves_the_complete_action(env):
    w, m, _ = env
    d = gui.SettingsDialog(w, m)
    d.done.setCurrentIndex(d.done.findData(gui.DONE_OPEN_FOLDER))
    assert d.done.currentData() == gui.DONE_OPEN_FOLDER
