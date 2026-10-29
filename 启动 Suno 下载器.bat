@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title Suno 音乐下载器

set "SKILL=%~dp0"
set "PY="

rem ---- 1. 找一个可用的 Python 3.9+ ----
for %%C in (python python3 py) do (
    if not defined PY (
        %%C -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>nul && set "PY=%%C"
    )
)
if not defined PY (
    for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do (
        if exist "%%~D\python.exe" set "PY=%%~D\python.exe"
    )
)
if not defined PY (
    echo 未找到 Python 3.9 及以上版本。
    echo 请先到 https://www.python.org/downloads/ 安装，安装时勾选 "Add python.exe to PATH"。
    echo.
    pause
    exit /b 1
)

rem ---- 2. 依赖自检（缺什么装什么） ----
"%PY%" -c "import requests,cryptography,imageio_ffmpeg,PySide6" >nul 2>nul
if errorlevel 1 (
    echo 首次运行，正在安装依赖（约 1-2 分钟，取决于网速）...
    "%PY%" -m pip install -q requests cryptography imageio-ffmpeg PySide6-Essentials
    if errorlevel 1 (
        echo 依赖安装失败，请手动执行：
        echo   "%PY%" -m pip install requests cryptography imageio-ffmpeg PySide6-Essentials
        echo.
        pause
        exit /b 1
    )
)

rem ---- 3. 启动图形界面 ----
start "" "%PY%" "%SKILL%scripts\suno_gui.py"
exit /b 0
