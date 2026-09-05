# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec. Builds a platform-specific single-file executable.

Windows / macOS:  pyinstaller yt-playlist-mix.spec
Linux:            pyinstaller --noconfirm yt-playlist-mix.spec

Requires a local ffmpeg-bin/ (ffmpeg + ffprobe) next to this file; the GitHub
Actions workflow provisions it via the `static-ffmpeg` package.
"""

import sys

from PyInstaller.building.build_main import EXE, PYZ, Analysis

icon = None
if sys.platform.startswith("win"):
    icon = "assets/icon.ico"
elif sys.platform == "darwin":
    icon = "assets/icon.icns"

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
    icon=icon,
)