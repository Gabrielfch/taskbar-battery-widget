"""Generate packaging/app.ico (16-256 px) from the app's own battery glyph."""
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter  # noqa: E402

from taskbar_battery_widget.icons import make_tray_icon  # noqa: E402

SIZES = (16, 24, 32, 48, 64, 256)


def render(size: int) -> bytes:
    icon = make_tray_icon(75, dark=False, fill_color=QColor("#2e9e4f"))
    src = icon.pixmap(256, 256).toImage()
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.drawImage(img.rect(), src)
    p.end()
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)  # noqa: F841
    pngs = [render(s) for s in SIZES]
    out = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries = b""
    for s, data in zip(SIZES, pngs):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    (ROOT / "packaging" / "app.ico").write_bytes(out + entries + b"".join(pngs))


if __name__ == "__main__":
    main()
