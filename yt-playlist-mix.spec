# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the PyPI-free Windows .exe build.

Build on Windows with:  pyinstaller yt-playlist-mix.spec
Requires a local ffmpeg-bin/ next to this file containing ffmpeg.exe and
ffprobe.exe (the GitHub Actions workflow provisions them automatically).
"""

from PyInstaller.building.build_main import EXE, PYZ, Analysis

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=[("ffmpeg-bin", "ffmpeg-bin")],
    hiddenimports=["yt_dlp", "textual"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="YouTubePlaylistMixer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)