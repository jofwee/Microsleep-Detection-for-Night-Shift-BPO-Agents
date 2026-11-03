# PyInstaller spec for the agent desktop (one-folder build).
#
# Build outside OneDrive (the output is hundreds of MB):
#   cd client
#   C:\Users\Mara\venvs\thesis-client\Scripts\python.exe -m PyInstaller packaging\client.spec --noconfirm ^
#       --distpath C:\Users\Mara\build\thesis-client\dist --workpath C:\Users\Mara\build\thesis-client\work
#
# The result is dist\call-desk\call-desk.exe. The three model files are bundled as data under models\,
# which is where the code looks for them (Path(__file__).parents[1] / "models" resolves to the bundle root).
# client/.env is NOT bundled: put a .env with SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY next to the exe.
# (The publishable key is meant to ship in apps; the SECRET key must never be near this build.)
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs

client = Path(SPECPATH).parent

datas, binaries, hiddenimports = [], [], []
for pkg in ("mediapipe",):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
binaries += collect_dynamic_libs("onnxruntime")
datas += collect_data_files("_sounddevice_data") + collect_data_files("_soundfile_data") + collect_data_files("certifi")
datas += [(str(client / "models" / name), "models") for name in ("face_landmarker.task", "w600k_r50.onnx", "MiniFASNetV2.onnx")]
datas += [(str(client / "assets" / "fonts"), "assets/fonts")]  # Geist, Geist Mono, Geist Pixel (SIL Open Font License)
hiddenimports += ["edge_tts", "keyring.backends.Windows", "sounddevice", "soundfile"]

excludes = [
    "tkinter", "pytest", "matplotlib", "pandas", "scipy.tests",
    # Qt parts this app does not use (they add hundreds of MB)
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.Qt3DCore",
    "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf",
    "PySide6.QtMultimedia", "PySide6.QtSql", "PySide6.QtBluetooth", "PySide6.QtNetworkAuth",
]

a = Analysis(
    [str(client / "app.py")],
    pathex=[str(client)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="call-desk", console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="call-desk", upx=False)
