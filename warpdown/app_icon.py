from pathlib import Path

from PySide6.QtGui import QIcon

ASSETS = Path(__file__).parent / "assets"


def app_icon() -> QIcon:
    return QIcon(str(ASSETS / "warpdown.svg"))
