import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QPalette
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from warpdown import gui, themes
from warpdown.manager import DownloadManager


@pytest.fixture(autouse=True)
def restore_default_theme(qapp):
    themes._capture(qapp)
    yield
    themes.apply_theme("default", "auto")


def lightness(app=None):
    return (app or QApplication.instance()).palette().color(QPalette.Window).lightness()


def test_every_style_and_mode_applies(qapp):
    for style in themes.STYLES:
        for mode in themes.MODES:
            dark = themes.apply_theme(style, mode)
            assert qapp.property("wd_style") == style and qapp.property("wd_dark") == dark
            if mode == "dark":
                assert dark and lightness() < 128
            if mode == "light":
                assert not dark and lightness() > 128


def test_default_follow_system_restores_the_original_look(qapp):
    base_window = themes._BASE["palette"].color(QPalette.Window).name()
    themes.apply_theme("win11", "dark")
    assert qapp.palette().color(QPalette.Window).name() != base_window
    themes.apply_theme("default", "auto")
    assert qapp.palette().color(QPalette.Window).name() == base_window
    assert qapp.style().objectName() == themes._BASE["style"] and qapp.styleSheet() == themes._BASE["qss"]


def test_styles_have_their_own_accent_colours(qapp):
    themes.apply_theme("kubuntu", "light")
    assert qapp.palette().color(QPalette.Highlight).name() == "#3daee9"          # Breeze-like blue
    themes.apply_theme("win11", "light")
    assert qapp.palette().color(QPalette.Highlight).name() == "#0067c0"          # Windows-11-like blue
    assert themes.corner_radius() == 6 and "border-radius: 6px" in qapp.styleSheet()
    themes.apply_theme("win11", "dark")
    assert qapp.palette().color(QPalette.Highlight).name() == "#4cc2ff"


def test_all_icon_drawings_are_valid_and_visible(qapp):
    black, white = QColor("#000000"), QColor("#ffffff")
    for style in ("kubuntu", "win11"):
        for key in themes.ICON_KEYS:
            svg = themes.svg_for(key, style, "#000000")
            assert QSvgRenderer(svg.encode()).isValid(), (style, key)
            ic = themes.icon(key, style, black)
            img = ic.pixmap(24, 24).toImage()
            opaque = sum(1 for x in range(24) for y in range(24) if img.pixelColor(x, y).alpha() > 40)
            assert opaque > 20, f"{style}/{key} draws (almost) nothing"
    assert themes.icon("add", "default", black) is None                           # System default keeps desktop icons
    assert themes.svg_for("nope", "win11", "#000") is None


def test_windows_icons_follow_the_text_colour_but_kubuntu_icons_keep_theirs(qapp):
    def mean_lightness(ic):
        img = ic.pixmap(24, 24).toImage()
        px = [img.pixelColor(x, y) for x in range(24) for y in range(24) if img.pixelColor(x, y).alpha() > 200]
        return sum(c.lightness() for c in px) / len(px)
    assert mean_lightness(themes.icon("delete", "win11", QColor("#ffffff"))) > \
        mean_lightness(themes.icon("delete", "win11", QColor("#000000"))) + 100
    k_dark = mean_lightness(themes.icon("delete", "kubuntu", QColor("#ffffff")))
    k_light = mean_lightness(themes.icon("delete", "kubuntu", QColor("#000000")))
    assert abs(k_dark - k_light) < 1


@pytest.fixture()
def win(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    qapp.setOrganizationName("warpdown-test3")
    qapp.setApplicationName("warpdown-test3")
    QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, str(tmp_path / "conf"))
    QSettings().clear()
    m = DownloadManager()
    w = gui.MainWindow(m)
    yield w
    m.shutdown()


def toolbar_icon_keys(w):
    return {a.text(): a.icon().cacheKey() for a in w.toolbar.actions() if a.text()}


def test_window_icons_change_with_the_style(win):
    default = toolbar_icon_keys(win)
    assert all(win.cats.item(i).icon().isNull() for i in range(win.cats.count()))   # default: plain list

    win.set_theme("win11", "light")
    fluent = toolbar_icon_keys(win)
    assert fluent != default and not win.cats.item(0).icon().isNull()
    assert all(not win.cats.item(i).icon().isNull() for i in range(win.cats.count()))

    win.set_theme("kubuntu", "dark")
    assert toolbar_icon_keys(win) not in (default, fluent)

    win.set_theme("default", "auto")
    assert toolbar_icon_keys(win) == default and win.cats.item(0).icon().isNull()   # fully restored


def test_saved_theme_and_settings_dialog(win):
    assert gui.saved_theme() == ("default", "auto")
    QSettings().setValue("ui_style", "bogus")
    QSettings().setValue("ui_mode", "dark")
    assert gui.saved_theme() == ("default", "dark")                                  # invalid values fall back
    QSettings().setValue("ui_style", "win11")
    d = gui.SettingsDialog(win, win.mgr)
    assert d.style_box.currentData() == "win11" and d.mode_box.currentData() == "dark"
    seen = []
    d.theme_preview.connect(lambda s, m: seen.append((s, m)))
    d.style_box.setCurrentIndex(d.style_box.findData("kubuntu"))
    d.mode_box.setCurrentIndex(d.mode_box.findData("light"))
    assert seen == [("kubuntu", "dark"), ("kubuntu", "light")]                       # previews live while choosing
