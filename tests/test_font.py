import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from warpdown import gui
from warpdown.manager import DownloadManager


@pytest.fixture()
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    qapp.setOrganizationName("warpdown-test")
    qapp.setApplicationName("warpdown-test")
    QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, str(tmp_path / "conf"))
    QSettings().clear()
    m = DownloadManager()
    w = gui.MainWindow(m)
    yield w
    gui.apply_font(0)                 # never leak a changed font into other tests
    m.shutdown()


def test_apply_font_scales_and_resets(qapp):
    base = gui.base_font_pt() if gui._BASE_FONT else QApplication.font().pointSizeF()
    assert gui.apply_font(0) == pytest.approx(1.0)
    k = gui.apply_font(20)
    assert QApplication.font().pointSizeF() == pytest.approx(20.0) and k == pytest.approx(20.0 / gui.base_font_pt())
    assert gui.apply_font(0) == pytest.approx(1.0) and QApplication.font().pointSizeF() == pytest.approx(gui.base_font_pt())
    assert gui.base_font_pt() == pytest.approx(base) or base <= 0          # the system default was remembered


def test_layout_follows_text_size(window):
    w = window
    w0 = w.table.columnWidth(2)
    row0 = w.table.verticalHeader().defaultSectionSize()
    icon0 = w.toolbar.iconSize().width()
    w.set_font_size(20)
    assert w.table.columnWidth(2) > w0                       # columns widen
    assert w.table.verticalHeader().defaultSectionSize() > row0   # rows grow
    assert w.toolbar.iconSize().width() > icon0               # toolbar icons grow
    w.set_font_size(0)
    assert w.table.columnWidth(2) == w0 and w.toolbar.iconSize().width() == icon0


def test_zoom_shortcuts_step_and_persist(window):
    w = window
    w.zoom(0)
    assert gui.saved_font_size() == 0
    w.zoom(1)
    up = gui.saved_font_size()
    assert up > round(gui.base_font_pt())
    w.zoom(1)
    assert gui.saved_font_size() > up                         # keeps stepping up
    w.zoom(-1); w.zoom(-1)
    assert gui.saved_font_size() == 0                         # back at the system default
    w.zoom(-1)
    assert 0 < gui.saved_font_size() < round(gui.base_font_pt())   # can also go smaller
    w.zoom(0)
    assert gui.saved_font_size() == 0 and QApplication.font().pointSizeF() == pytest.approx(gui.base_font_pt())


def test_settings_dialog_previews_live_and_remembers(window, qapp):
    w = window
    QSettings().setValue("font_size", 16)
    d = gui.SettingsDialog(w, w.mgr)
    assert d.fontsize.currentData() == 16                     # shows the saved choice
    seen = []
    d.font_preview.connect(seen.append)
    d.fontsize.setCurrentIndex(d.fontsize.findData(20))
    assert seen == [20]                                        # the main window can resize while choosing


class _FakeScreen:
    def __init__(self, width):
        self._w = width

    def availableGeometry(self):
        from PySide6.QtCore import QRect
        return QRect(0, 0, self._w, 1080)


def test_window_grows_for_bigger_text_but_never_shrinks_or_exceeds_screen(window):
    w = window
    w.screen = lambda: _FakeScreen(1920)
    w.resize(1000, 400)
    w.set_font_size(16)
    assert 1000 < w.width() <= 1920                      # grew so the Name column keeps room
    grown = w.width()
    w.set_font_size(0)
    assert w.width() == grown                            # never shrinks a window the user may have sized
    w.screen = lambda: _FakeScreen(900)
    w.resize(700, 400)
    w.set_font_size(24)
    assert w.width() <= 900                              # capped at the screen width


def _gap_between_list_and_table(w):
    w.show()
    for _ in range(15):
        QApplication.processEvents()
    return w.table.geometry().left() - w.cats.geometry().right() - 1


@pytest.mark.parametrize("pt,style,mode", [(0, "default", "auto"), (12, "kubuntu", "dark"), (16, "win11", "light")])
def test_no_empty_bar_between_category_list_and_table(qapp, tmp_path, monkeypatch, pt, style, mode):
    """Regression: the splitter used to start with a share wider than the list's maximum, leaving a fat empty bar."""
    from warpdown import themes
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    gui.apply_font(pt)
    themes.apply_theme(style, mode)
    m = DownloadManager()
    w = gui.MainWindow(m)
    w.resize(1200, 620)
    try:
        handle = w.split.handleWidth()
        assert _gap_between_list_and_table(w) <= handle + 1          # just the divider, nothing more
        w.set_font_size(20 if pt != 20 else 8)                       # changing the text size must not reopen the gap
        assert _gap_between_list_and_table(w) <= handle + 1
        w.set_theme("win11", "dark")
        assert _gap_between_list_and_table(w) <= handle + 1
    finally:
        gui.apply_font(0)
        themes.apply_theme("default", "auto")
        m.shutdown()
