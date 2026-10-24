"""UI themes: a Style (System default / Kubuntu / Windows 11) combined with a Mode (Follow system / Light / Dark).

The Kubuntu and Windows 11 styles are *inspired by* those desktops (colours, shapes, icon character); all colours,
stylesheets and icons here are original work. Both are built on Qt's Fusion style so they look the same everywhere.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

STYLES = {"default": "System default", "kubuntu": "Kubuntu", "win11": "Windows 11"}
MODES = {"auto": "Follow system", "light": "Light", "dark": "Dark"}

# ---------------------------------------------------------------- colours
# window, base (inputs/lists), alt (alternate rows), text, disabled, button, highlight, hltext, mid (borders),
# link, tip (tooltip background), accent, hover, soft (subtle selection)
_COLORS = {
    ("default", False): dict(window="#f0f0f0", base="#ffffff", alt="#f6f6f6", text="#202020", disabled="#9a9a9a",
                             button="#e8e8e8", highlight="#308cc6", hltext="#ffffff", mid="#c4c4c4", link="#1d6fb8",
                             tip="#ffffdc", accent="#308cc6", hover="#dcdcdc", soft="#cfe3f3"),
    ("default", True): dict(window="#2d2d30", base="#1e1e1e", alt="#252526", text="#e6e6e6", disabled="#7a7a7a",
                            button="#3a3a3e", highlight="#3584e4", hltext="#ffffff", mid="#4a4a4e", link="#62a0ea",
                            tip="#3a3a3e", accent="#3584e4", hover="#44444a", soft="#26425f"),
    ("kubuntu", False): dict(window="#eff0f1", base="#fcfcfc", alt="#f3f4f5", text="#31363b", disabled="#a1a9b1",
                             button="#eff0f1", highlight="#3daee9", hltext="#ffffff", mid="#bdc3c7", link="#2980b9",
                             tip="#31363b", accent="#3daee9", hover="#d5ecf9", soft="#c4e4f6"),
    ("kubuntu", True): dict(window="#31363b", base="#232629", alt="#2a2e32", text="#eff0f1", disabled="#7f8c8d",
                            button="#31363b", highlight="#3daee9", hltext="#ffffff", mid="#4d5257", link="#1d99f3",
                            tip="#31363b", accent="#3daee9", hover="#3b4a55", soft="#27465a"),
    ("win11", False): dict(window="#f3f3f3", base="#ffffff", alt="#f9f9f9", text="#1b1b1b", disabled="#9e9e9e",
                           button="#fbfbfb", highlight="#0067c0", hltext="#ffffff", mid="#d1d1d1", link="#0067c0",
                           tip="#fbfbfb", accent="#0067c0", hover="#eaeaea", soft="#dbe9f6"),
    ("win11", True): dict(window="#202020", base="#2b2b2b", alt="#262626", text="#ffffff", disabled="#7a7a7a",
                          button="#2d2d2d", highlight="#4cc2ff", hltext="#000000", mid="#454545", link="#60cdff",
                          tip="#2d2d2d", accent="#4cc2ff", hover="#383838", soft="#2a4a5f"),
}
RADIUS = {"default": 3, "kubuntu": 3, "win11": 6}          # corner radius used by the hand-drawn progress bars

_BASE: dict = {}      # the system's own style/palette/stylesheet, captured once so "System default" can restore them


def _capture(app: QApplication) -> None:
    if not _BASE:
        _BASE.update(style=app.style().objectName(), palette=QPalette(app.palette()), qss=app.styleSheet())


def system_is_dark() -> bool:
    app = QApplication.instance()
    _capture(app)
    return _BASE["palette"].color(QPalette.Window).lightness() < 128


def colors(style: str, dark: bool) -> dict:
    return _COLORS[(style if style in STYLES else "default", bool(dark))]


def make_palette(c: dict) -> QPalette:
    p = QPalette()
    q = QColor
    p.setColor(QPalette.Window, q(c["window"])); p.setColor(QPalette.WindowText, q(c["text"]))
    p.setColor(QPalette.Base, q(c["base"])); p.setColor(QPalette.AlternateBase, q(c["alt"]))
    p.setColor(QPalette.Text, q(c["text"])); p.setColor(QPalette.Button, q(c["button"]))
    p.setColor(QPalette.ButtonText, q(c["text"])); p.setColor(QPalette.BrightText, q("#ffffff"))
    p.setColor(QPalette.Highlight, q(c["highlight"])); p.setColor(QPalette.HighlightedText, q(c["hltext"]))
    p.setColor(QPalette.Link, q(c["link"])); p.setColor(QPalette.ToolTipBase, q(c["tip"]))
    p.setColor(QPalette.ToolTipText, q(c["text"]) if c["tip"] == c["window"] or c["tip"] == c["button"] else
               q("#ffffff") if q(c["tip"]).lightness() < 128 else q("#202020"))
    ph = q(c["text"]); ph.setAlpha(120)
    p.setColor(QPalette.PlaceholderText, ph)
    p.setColor(QPalette.Mid, q(c["mid"])); p.setColor(QPalette.Midlight, q(c["hover"]))
    p.setColor(QPalette.Dark, q(c["mid"]).darker(130)); p.setColor(QPalette.Light, q(c["base"]))
    p.setColor(QPalette.Shadow, q("#000000"))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, q(c["disabled"]))
    return p


def stylesheet(style: str, c: dict) -> str:
    """Extra polish on top of the palette. Deliberately avoids QComboBox/QSpinBox rules (they lose their arrows)."""
    if style == "win11":
        return f"""
        QToolBar {{ border: none; spacing: 4px; padding: 4px 6px; background: {c['window']}; }}
        QToolButton {{ border: 1px solid transparent; border-radius: 6px; padding: 5px 10px; }}
        QToolButton:hover {{ background: {c['hover']}; }}
        QToolButton:pressed {{ background: {c['soft']}; }}
        QPushButton {{ background: {c['button']}; border: 1px solid {c['mid']}; border-radius: 6px; padding: 5px 16px; }}
        QPushButton:hover {{ background: {c['hover']}; }}
        QPushButton:pressed {{ background: {c['soft']}; }}
        QPushButton:default {{ background: {c['accent']}; color: {c['hltext']}; border: 1px solid {c['accent']}; }}
        QPushButton:disabled {{ color: {c['disabled']}; }}
        QLineEdit {{ background: {c['base']}; border: 1px solid {c['mid']}; border-bottom: 2px solid {c['mid']};
                    border-radius: 6px; padding: 4px 8px; selection-background-color: {c['accent']};
                    selection-color: {c['hltext']}; }}
        QLineEdit:focus {{ border-bottom: 2px solid {c['accent']}; }}
        QListWidget {{ background: {c['window']}; border: none; outline: none; padding: 4px; }}
        QListWidget::item {{ padding: 6px 10px; border-radius: 6px; margin: 1px 2px; }}
        QListWidget::item:hover {{ background: {c['hover']}; }}
        QListWidget::item:selected {{ background: {c['soft']}; color: {c['text']}; }}
        QTableView {{ background: {c['base']}; alternate-background-color: {c['alt']}; border: 1px solid {c['mid']};
                     border-radius: 8px; gridline-color: transparent; outline: none;
                     selection-background-color: {c['soft']}; selection-color: {c['text']}; }}
        QHeaderView::section {{ background: {c['base']}; color: {c['disabled']}; border: none;
                               border-right: 1px solid {c['mid']}; border-bottom: 1px solid {c['mid']};
                               padding: 6px 8px; }}
        QHeaderView::section:last {{ border-right: none; }}
        QGroupBox {{ border: 1px solid {c['mid']}; border-radius: 8px; margin-top: 12px; padding: 8px; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
        QStatusBar {{ background: {c['window']}; }}
        QScrollBar:vertical {{ width: 10px; background: transparent; margin: 2px; }}
        QScrollBar::handle:vertical {{ background: {c['mid']}; border-radius: 4px; min-height: 24px; }}
        QScrollBar:horizontal {{ height: 10px; background: transparent; margin: 2px; }}
        QScrollBar::handle:horizontal {{ background: {c['mid']}; border-radius: 4px; min-width: 24px; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
        QMenu {{ background: {c['base']}; border: 1px solid {c['mid']}; border-radius: 8px; padding: 4px; }}
        QMenu::item {{ padding: 6px 20px; border-radius: 4px; }}
        QMenu::item:selected {{ background: {c['soft']}; color: {c['text']}; }}
        QToolTip {{ background: {c['tip']}; color: {c['text']}; border: 1px solid {c['mid']}; border-radius: 4px;
                   padding: 4px 6px; }}
        """
    if style == "kubuntu":
        return f"""
        QToolBar {{ border: none; spacing: 2px; padding: 2px 4px; }}
        QToolButton {{ border: 1px solid transparent; border-radius: 3px; padding: 4px 8px; }}
        QToolButton:hover {{ background: {c['hover']}; border: 1px solid {c['accent']}; }}
        QToolButton:pressed {{ background: {c['soft']}; border: 1px solid {c['accent']}; }}
        QListWidget {{ outline: none; }}
        QListWidget::item {{ padding: 3px 6px; border-radius: 3px; }}
        QListWidget::item:hover {{ background: {c['hover']}; }}
        QListWidget::item:selected {{ background: {c['highlight']}; color: {c['hltext']}; }}
        QTableView {{ gridline-color: transparent; outline: none; }}
        QHeaderView::section {{ background: {c['button']}; border: none; border-right: 1px solid {c['mid']};
                               border-bottom: 1px solid {c['mid']}; padding: 3px 6px; }}
        QToolTip {{ background: {c['tip']}; color: {'#eff0f1'}; border: 1px solid {c['accent']}; padding: 3px 5px; }}
        """
    return ""


def apply_theme(style: str, mode: str) -> bool:
    """Apply Style + Mode to the whole application. Returns True if the result is dark."""
    app = QApplication.instance()
    _capture(app)
    style = style if style in STYLES else "default"
    dark = system_is_dark() if mode == "auto" else mode == "dark"
    if style == "default" and mode == "auto":
        app.setStyle(_BASE["style"])
        app.setPalette(_BASE["palette"])
        app.setStyleSheet(_BASE["qss"])
    else:
        c = colors(style, dark)
        app.setStyle("Fusion")
        app.setPalette(make_palette(c))
        app.setStyleSheet(stylesheet(style, c))
    app.setProperty("wd_style", style)
    app.setProperty("wd_dark", dark)
    app.setProperty("wd_radius", RADIUS[style])
    return dark


def current_style() -> str:
    return str(QApplication.instance().property("wd_style") or "default")


def corner_radius() -> int:
    return int(QApplication.instance().property("wd_radius") or 3)


# ---------------------------------------------------------------- icons (original drawings, 24x24 grid)
# "fluent": thin monochrome outline icons (the Windows 11 look); "breeze": flat colourful icons (the Kubuntu look).
_ICONS = {
    "add": (
        '<path d="M12 5v14M5 12h14"/>',
        '<circle cx="12" cy="12" r="9.5" fill="#27ae60"/><path d="M12 7v10M7 12h10" stroke="#fff" stroke-width="2.4" '
        'stroke-linecap="round"/>'),
    "resume": (
        '<path d="M8 5.5v13l10-6.5z"/>',
        '<circle cx="12" cy="12" r="9.5" fill="#3daee9"/><path d="M10 7.8v8.4l6.6-4.2z" fill="#fff"/>'),
    "pause": (
        '<path d="M8.5 5.5v13M15.5 5.5v13"/>',
        '<circle cx="12" cy="12" r="9.5" fill="#f67400"/><rect x="8" y="7.5" width="2.8" height="9" rx=".7" fill="#fff"/>'
        '<rect x="13.2" y="7.5" width="2.8" height="9" rx=".7" fill="#fff"/>'),
    "delete": (
        '<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 12.5h9l1-12.5M10 11v5M14 11v5"/>',
        '<path d="M6 8h12l-1 11.3a1.6 1.6 0 0 1-1.6 1.5H8.6a1.6 1.6 0 0 1-1.6-1.5z" fill="#da4453"/>'
        '<rect x="4.5" y="5.5" width="15" height="2.4" rx="1.2" fill="#a8303c"/>'
        '<rect x="9.5" y="3.2" width="5" height="2.8" rx="1" fill="#a8303c"/>'),
    "folder": (
        '<path d="M3.5 6.5h6l2 2h9v9.5a1.5 1.5 0 0 1-1.5 1.5h-14a1.5 1.5 0 0 1-1.5-1.5z"/><path d="M3.5 11h17"/>',
        '<path d="M2.5 6a1.5 1.5 0 0 1 1.5-1.5h5l2 2.5h9a1.5 1.5 0 0 1 1.5 1.5v9.5a1.5 1.5 0 0 1-1.5 1.5H4A1.5 1.5 0 0 '
        '1 2.5 18z" fill="#2b8fc7"/><path d="M2.5 10h19v8.5a1.5 1.5 0 0 1-1.5 1.5H4a1.5 1.5 0 0 1-1.5-1.5z" '
        'fill="#6cc4f0"/>'),
    "browser": (
        '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.5 2.3 3.7 5.2 3.7 8.5s-1.2 6.2-3.7 8.5c-2.5-2.3'
        '-3.7-5.2-3.7-8.5S9.5 5.8 12 3.5z"/>',
        '<circle cx="12" cy="12" r="9.5" fill="#3daee9"/><path d="M12 2.5c3 2.6 4.4 5.8 4.4 9.5S15 18.9 12 21.5M12 '
        '2.5C9 5.1 7.6 8.3 7.6 12S9 18.9 12 21.5M2.5 12h19" stroke="#fff" stroke-width="1.5" fill="none"/>'),
    "settings": (
        '<path d="M4 8h9M17 8h3M4 16h3M11 16h9"/><circle cx="15" cy="8" r="2"/><circle cx="9" cy="16" r="2"/>',
        '<circle cx="12" cy="12" r="9.5" fill="#7f8c8d"/><path d="M6.5 9h6M16.5 9h1M6.5 15h1M11.5 15h6" stroke="#fff" '
        'stroke-width="2" stroke-linecap="round"/><circle cx="14.5" cy="9" r="2" fill="#fff"/>'
        '<circle cx="9.5" cy="15" r="2" fill="#fff"/>'),
    "all": (
        '<path d="M12 4.5v11M7.5 11.5l4.5 4.5 4.5-4.5M5 19.5h14"/>',
        '<circle cx="12" cy="12" r="9.5" fill="#3daee9"/><path d="M12 6.5v7M8.5 10.5l3.5 3.5 3.5-3.5M7.5 17.2h9" '
        'stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>'),
    "unfinished": (
        '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3.2 2"/>',
        '<circle cx="12" cy="12" r="9.5" fill="#f67400"/><path d="M12 6.8v5.4l3.4 2" stroke="#fff" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round" fill="none"/>'),
    "finished": (
        '<circle cx="12" cy="12" r="8.5"/><path d="M8 12.3l2.8 2.8L16.2 9.5"/>',
        '<circle cx="12" cy="12" r="9.5" fill="#27ae60"/><path d="M7.5 12.3l3.3 3.3 5.9-6.2" stroke="#fff" '
        'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>'),
    "video": (
        '<rect x="3.5" y="6" width="17" height="12" rx="2"/><path d="M10.5 9.5v5l4-2.5z"/>',
        '<rect x="2.5" y="5" width="19" height="14" rx="2.2" fill="#9b59b6"/><path d="M10 8.7v6.6l5.5-3.3z" fill="#fff"/>'),
    "music": (
        '<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>',
        '<path d="M9 17V5.5l10.5-2V15" stroke="#1d99f3" stroke-width="2" fill="none" stroke-linejoin="round"/>'
        '<circle cx="6.5" cy="17.5" r="3" fill="#3daee9"/><circle cx="17" cy="15.5" r="3" fill="#3daee9"/>'),
    "compressed": (
        '<rect x="4.5" y="4" width="15" height="16" rx="2"/><path d="M12 4v3M12 9v2M12 13v2"/>',
        '<rect x="3.5" y="3" width="17" height="18" rx="2.2" fill="#c9852b"/><rect x="10.5" y="3" width="3" height="9" '
        'fill="#f5d08a"/><rect x="9.5" y="13" width="5" height="4.5" rx="1" fill="#f5d08a"/>'),
    "documents": (
        '<path d="M7 3.5h7l4 4V20a.5.5 0 0 1-.5.5h-10.5a.5.5 0 0 1-.5-.5z"/><path d="M14 3.5v4h4M9.5 12h5M9.5 15.5h5"/>',
        '<path d="M5.5 2.5h9l4.5 4.5v13.5a1 1 0 0 1-1 1H5.5a1 1 0 0 1-1-1V3.5a1 1 0 0 1 1-1z" fill="#eef1f3" '
        'stroke="#aeb7bf"/><path d="M14.5 2.5V7H19" fill="#cfd6db"/><path d="M8 11.5h8M8 14.5h8M8 17.5h5" '
        'stroke="#3daee9" stroke-width="1.6" stroke-linecap="round"/>'),
    "programs": (
        '<rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="M3.5 9h17M6.5 7h.01M9 7h.01"/>',
        '<rect x="2.5" y="4" width="19" height="16" rx="2.2" fill="#3daee9"/><rect x="2.5" y="4" width="19" height="4" '
        'rx="2" fill="#2b8fc7"/><circle cx="5.2" cy="6" r=".9" fill="#fff"/><circle cx="8" cy="6" r=".9" fill="#fff"/>'
        '<path d="M8.5 13l-2 2 2 2M15.5 13l2 2-2 2" stroke="#fff" stroke-width="1.8" stroke-linecap="round" '
        'stroke-linejoin="round" fill="none"/>'),
    "batch": (
        '<path d="M4.5 7h11M4.5 12h11M4.5 17h6"/><path d="M18 14v6M15 17h6"/>',
        '<rect x="3" y="5" width="13" height="3" rx="1.2" fill="#3daee9"/><rect x="3" y="10.5" width="13" height="3" '
        'rx="1.2" fill="#3daee9"/><rect x="3" y="16" width="8" height="3" rx="1.2" fill="#3daee9"/>'
        '<circle cx="17.5" cy="17.5" r="4.5" fill="#27ae60"/><path d="M17.5 15v5M15 17.5h5" stroke="#fff" '
        'stroke-width="1.8" stroke-linecap="round"/>'),
    "schedule": (
        '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M8 3v4M16 3v4"/><path d="M12 13.2v2.4l1.7.9"/>',
        '<rect x="3" y="4.5" width="18" height="16" rx="2.2" fill="#eef1f3" stroke="#aeb7bf"/>'
        '<path d="M3 6.7a2.2 2.2 0 0 1 2.2-2.2h13.6A2.2 2.2 0 0 1 21 6.7V10H3z" fill="#da4453"/>'
        '<path d="M8 3v3M16 3v3" stroke="#a8303c" stroke-width="1.8" stroke-linecap="round"/>'
        '<path d="M12 12.5v3.2l2.2 1.2" stroke="#3daee9" stroke-width="1.8" stroke-linecap="round" fill="none"/>'),
    "stats": (
        '<path d="M5 20V11M12 20V5M19 20v-7"/><path d="M3 20.5h18"/>',
        '<rect x="3.5" y="12" width="4.5" height="8" rx="1" fill="#3daee9"/><rect x="9.8" y="5" width="4.5" height="15" rx="1" fill="#27ae60"/>'
        '<rect x="16" y="9" width="4.5" height="11" rx="1" fill="#f67400"/>'),
    "images": (
        '<rect x="3.5" y="5" width="17" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="M4 17l5-5 4 4 3-3 4.5 4.5"/>',
        '<rect x="2.5" y="4" width="19" height="16" rx="2.2" fill="#27ae60"/><circle cx="8" cy="9" r="2" fill="#fcd34d"/>'
        '<path d="M2.5 17.5l6-6 4.5 4.5 3-3 5.5 5.5V18a2 2 0 0 1-2 2H4.7a2.2 2.2 0 0 1-2.2-2z" fill="#1e8449"/>'),
    "other": (
        '<path d="M5 12h.01M12 12h.01M19 12h.01" stroke-width="3"/>',
        '<circle cx="12" cy="12" r="9.5" fill="#7f8c8d"/><circle cx="7.5" cy="12" r="1.7" fill="#fff"/>'
        '<circle cx="12" cy="12" r="1.7" fill="#fff"/><circle cx="16.5" cy="12" r="1.7" fill="#fff"/>'),
}
ICON_KEYS = tuple(_ICONS)
_ICON_CACHE: dict = {}


def svg_for(key: str, style: str, color: str) -> str | None:
    if key not in _ICONS or style not in ("win11", "kubuntu"):
        return None
    fluent, breeze = _ICONS[key]
    if style == "win11":
        return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="%s" '
                'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">%s</svg>' % (color, fluent))
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">%s</svg>' % breeze


def _render(svg: str, px: int) -> QPixmap:
    img = QImage(px, px, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode())).render(p, QRectF(0, 0, px, px))
    p.end()
    return QPixmap.fromImage(img)


def icon(key: str, style: str, text_color: QColor) -> QIcon | None:
    """The themed icon, or None for the System default style (which keeps the desktop's own icons)."""
    svg = svg_for(key, style, text_color.name())
    if svg is None:
        return None
    ck = (key, style, text_color.name() if style == "win11" else "")
    if ck not in _ICON_CACHE:
        ic = QIcon()
        for px in (16, 20, 24, 32, 40, 48, 64, 96):
            ic.addPixmap(_render(svg, px))
        _ICON_CACHE[ck] = ic
    return _ICON_CACHE[ck]
