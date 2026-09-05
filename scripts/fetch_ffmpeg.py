#!/usr/bin/env python3
"""Fetch static ffmpeg and ffprobe binaries for the current platform.

Runs inside the PyInstaller build (GitHub Actions or locally) and drops the
executables into ffmpeg-bin/ so they can be bundled with the app.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main() -> int:
    try:
        import static_ffmpeg
    except ImportError:
        print("static-ffmpeg is not installed. Put ffmpeg and ffprobe manually into ffmpeg-bin/.")
        return 1

    tools = static_ffmpeg.run.get_or_fetch_platform_executables_else_raise()
    out = ROOT / "ffmpeg-bin"
    out.mkdir(exist_ok=True)
    for src in tools:
        dest = out / os.path.basename(src)
        shutil.copy(src, dest)
        print(f"bundled {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())