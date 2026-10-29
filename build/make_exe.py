# -*- coding: utf-8 -*-
"""把 Suno 下载器打成单文件 exe。

用法：
    python make_exe.py            # 打 GUI 版（默认）
    python make_exe.py cli        # 打命令行版

产物：dist/SunoDownloader-x64.exe

只支持 64 位 Windows：Qt6/PySide6、Qt5/PySide2、PyQt5 均无 win32  wheel，
32 位图形界面版无法构建（已逐个 pip download --platform win32 验证）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import PyInstaller.__main__  # noqa: E402

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
MODE = (sys.argv[1] if len(sys.argv) > 1 else "gui").lower()
ARCH = "x64"

entry = "suno_gui.py" if MODE == "gui" else "suno_download.py"
name = f"SunoDownloader-{ARCH}" + ("" if MODE == "gui" else "-cli")

sys.path.insert(0, str(SCRIPTS))
import imageio_ffmpeg  # noqa: E402
ff = Path(imageio_ffmpeg.get_ffmpeg_exe())
ff_dest = "imageio_ffmpeg" + str(ff.parent.as_posix()).split("imageio_ffmpeg")[-1] + "/" + ff.name

args = [
    str(SCRIPTS / entry),
    f"--name={name}",
    "--onefile",
    "--noconfirm",
    "--clean",
    "--collect-data=certifi",
    f"--add-binary={ff};{ff_dest.rsplit('/',1)[0]}",
    "--exclude-module=PySide6.QtWebEngineCore",
    "--exclude-module=PySide6.QtWebEngineWidgets",
    "--exclude-module=PySide6.QtQml",
    "--exclude-module=PySide6.QtQuick",
    "--exclude-module=PySide6.Qt3DCore",
    "--exclude-module=PySide6.QtMultimedia",
    "--exclude-module=PySide6.QtCharts",
    "--exclude-module=PySide6.QtDataVisualization",
    "--exclude-module=PySide6.QtBluetooth",
    "--exclude-module=PySide6.QtNfc",
    "--exclude-module=PySide6.QtPositioning",
    "--exclude-module=PySide6.QtSensors",
    "--exclude-module=PySide6.QtSerialPort",
    "--exclude-module=PySide6.QtTest",
    "--exclude-module=PySide6.QtDesigner",
    "--exclude-module=PySide6.QtHelp",
    "--exclude-module=PySide6.QtOpenGLWidgets",
    "--exclude-module=PySide6.QtSpatialAudio",
    "--exclude-module=PySide6.QtSql",
    "--exclude-module=PySide6.QtNetworkAuth",
    "--exclude-module=PySide6.QtRemoteObjects",
    "--exclude-module=PySide6.QtSvg",
    "--exclude-module=PySide6.QtSvgWidgets",
    "--exclude-module=PySide6.QtUiTools",
    "--exclude-module=tkinter",
    "--exclude-module=matplotlib",
    "--exclude-module=numpy",
    "--exclude-module=PIL",
    "--exclude-module=scipy",
    "--exclude-module=pandas",
]
if MODE == "gui":
    args.insert(2, "--windowed")

print("entry :", entry)
print("ffmpeg:", ff, "->", ff_dest)
print("args  :", " ".join(a for a in args if not a.startswith("--exclude")))
PyInstaller.__main__.run(args)
