# PyInstaller spec. Usage: pyinstaller packaging/taskbar_battery_widget.spec [-- onefile]
# Build mode is chosen with the TBW_ONEFILE environment variable ("1" -> single-file exe).
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

SPEC_DIR = Path(SPECPATH)
ROOT = SPEC_DIR.parent
ONEFILE = os.environ.get("TBW_ONEFILE") == "1"

hiddenimports = collect_submodules("taskbar_battery_widget") + [
    "winrt.runtime",
    "winrt.system",
    "winrt.windows.devices.enumeration",
    "winrt.windows.devices.bluetooth",
    "winrt.windows.devices.radios",
    "winrt.windows.foundation",
    "winrt.windows.foundation.collections",
    "winrt.windows.storage.streams",
]

EXCLUDES = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras", "PySide6.QtQuick", "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets", "PySide6.QtQuickControls2", "PySide6.QtQml", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtNetwork", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtSql",
    "PySide6.QtSvg", "PySide6.QtSvgWidgets", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtTest", "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    "PySide6.QtSpatialAudio", "PySide6.QtStateMachine", "PySide6.QtTextToSpeech",
    "PySide6.QtXml", "PySide6.QtConcurrent", "PySide6.QtDBus", "PySide6.QtPrintSupport",
    "PySide6.QtUiTools", "PySide6.QtGraphs", "PySide6.QtGraphsWidgets", "PySide6.QtHttpServer",
    "PySide6.QtLocation", "PySide6.QtNetworkAuth", "PySide6.QtShaderTools",
    "tkinter", "unittest", "pytest", "test",
]

# Binaries/data that are never needed by a QtCore/QtGui/QtWidgets-only app.
DROP = (
    "opengl32sw", "qt6quick", "qt6qml", "qt6pdf", "qt6network", "qt6opengl", "qt6svg",
    "qt6virtualkeyboard", "qt6webengine", "qt6multimedia", "qt6charts", "qt6datavis",
    "d3dcompiler", "translations", "qtquick", "qml", "imageformats\qpdf",
    "ffmpeg", "avcodec", "avformat", "avutil", "swresample", "swscale",
)


def keep(entry):
    name = entry[0].lower().replace("/", "\\")
    return not any(d in name for d in DROP)


a = Analysis(
    [str(SPEC_DIR / "app_entry.py")],
    pathex=[str(ROOT)],
    hiddenimports=hiddenimports,
    excludes=EXCLUDES,
    noarchive=False,
)
a.binaries = [b for b in a.binaries if keep(b)]
a.datas = [d for d in a.datas if keep(d)]
pyz = PYZ(a.pure)

common = dict(
    name="TaskbarBatteryWidget",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(SPEC_DIR / "app.ico"),
    version=str(SPEC_DIR / "version_info.txt"),
)

if ONEFILE:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], runtime_tmpdir=None, **common)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **common)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="TaskbarBatteryWidget")
