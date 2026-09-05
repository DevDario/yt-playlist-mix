"""Core logic for the YouTube playlist mixer.

Fetch a playlist's metadata, download every track as MP3, then merge the
tracks into a single audio file separated by short pauses.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from yt_dlp import DownloadCancelled, YoutubeDL

OUTPUT_DIR = Path("downloads")
SILENCE_FILE = Path("silence.mp3")
CONCAT_FILE = Path("concat.txt")
OUTPUT_FILE = Path("playlist_mix.mp3")

SILENCE_SECONDS = 0.5
BITRATE = "320k"


def _frozen_base() -> Path | None:
    """Return the extraction dir of a PyInstaller one-file bundle, if frozen."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return None


def _prepend_bundle_to_path() -> None:
    """Make bundled tooling (ffmpeg/ffprobe) visible on PATH for yt-dlp."""
    base = _frozen_base()
    if base is not None:
        binary_dir = base / "ffmpeg-bin"
        if binary_dir.is_dir():
            os.environ["PATH"] = str(binary_dir) + os.pathsep + os.environ.get("PATH", "")


def _tool(name: str) -> str:
    """Absolute path to a bundled executable, or the bare command name."""
    base = _frozen_base()
    if base is not None:
        exe = base / "ffmpeg-bin" / (name + (".exe" if os.name == "nt" else ""))
        if exe.exists():
            return str(exe)
    return name


_prepend_bundle_to_path()
_FFMPEG = _tool("ffmpeg")
_FFPROBE = _tool("ffprobe")


class PipelineCancelled(Exception):
    """Raised when the user aborts the current run."""


class PipelineError(Exception):
    """Raised for expected failures (fetch, download, merge, ...)."""


class DownloadGate:
    """Pauses the pipeline after downloads so the UI can ask the user how to
    finalize the files: merge them into one mix, or keep them individually."""

    MODE_MIX = "mix"
    MODE_INDIVIDUAL = "individual"

    def __init__(self) -> None:
        self._decided = threading.Event()
        self._mode: str = self.MODE_MIX

    def await_decision(self, on_progress, cancel_event) -> str:
        """Block the worker thread until decide() is called or the user cancels."""
        on_progress("mode_prompt", text="Downloads complete. Choose how to finalize the files.")
        while not self._decided.is_set():
            if cancel_event.is_set():
                raise PipelineCancelled("cancelled")
            self._decided.wait(0.1)
        return self._mode

    def decide(self, mode: str) -> None:
        self._mode = mode
        self._decided.set()


class _NullLogger:
    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass


class _ForwardingLogger:
    def __init__(self, on_log) -> None:
        self._on_log = on_log

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        self._on_log("warning", msg)

    def error(self, msg: str) -> None:
        self._on_log("error", msg)


_YD_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "noprogress": True,
}


@dataclass
class Track:
    index: int
    title: str
    duration: float | None
    channel: str
    id: str = ""


@dataclass
class PlaylistInfo:
    url: str
    title: str
    channel: str
    tracks: list[Track]


def fetch_playlist(url: str) -> PlaylistInfo:
    """Return a playlist's metadata without downloading anything."""
    opts = {
        **_YD_OPTS,
        "logger": _NullLogger(),
        "extract_flat": "in_playlist",
        "ignoreerrors": True,
        "noplaylist": False,
    }
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise PipelineError(f"Could not fetch the playlist: {exc}") from exc

    if not info:
        raise PipelineError("The playlist returned no metadata.")

    entries = [e for e in (info.get("entries") or []) if e]
    if not entries:
        raise PipelineError("The playlist contains no tracks.")

    tracks = [
        Track(
            index=i,
            title=(entry.get("title") or "Untitled").strip()[:120],
            duration=_as_seconds(entry.get("duration")),
            channel=(entry.get("channel") or entry.get("uploader") or ""),
            id=entry.get("id") or "",
        )
        for i, entry in enumerate(entries, start=1)
    ]
    return PlaylistInfo(
        url=url,
        title=(info.get("title") or "Untitled playlist").strip(),
        channel=info.get("channel") or info.get("uploader") or "",
        tracks=tracks,
    )


def prepare_output_dir() -> None:
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True)
    for stale in (CONCAT_FILE, OUTPUT_FILE):
        stale.unlink(missing_ok=True)


def create_silence() -> None:
    try:
        subprocess.run(
            [
                _FFMPEG,
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=44100:cl=stereo",
                "-t",
                str(SILENCE_SECONDS),
                "-q:a",
                "9",
                str(SILENCE_FILE),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        raise PipelineError(f"Could not create the silence segment: {exc}") from exc


def download_playlist(playlist: PlaylistInfo, on_progress, cancel_event) -> None:
    """Download every track in the playlist as MP3, reporting progress."""
    total = len(playlist.tracks)
    started: set[int] = set()
    by_id = {track.id: track.index for track in playlist.tracks if track.id}

    def resolve_index(info: dict) -> int:
        index = info.get("playlist_index")
        if isinstance(index, int) and 0 < index <= total:
            return index
        for i in range(1, total + 1):
            if i not in started:
                return i
        return len(started) + 1

    def on_log(level: str, message: str) -> None:
        if level == "error":
            for video_id, index in by_id.items():
                if video_id in message:
                    on_progress("track_error", index=index, message=message)
                    return
        on_progress("download_log", level=level, message=message)

    def hook(d: dict) -> None:
        if cancel_event.is_set():
            raise DownloadCancelled("cancelled by user")
        status = d.get("status")
        info = d.get("info_dict") or {}
        index = resolve_index(info)
        if status == "downloading":
            started.add(index)
            total_bytes = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            percent = (downloaded / total_bytes * 100.0) if total_bytes else 0.0
            on_progress(
                "track",
                index=index,
                title=info.get("title") or "",
                percent=percent,
                speed=d.get("speed"),
                eta=d.get("eta"),
            )
        elif status == "finished":
            on_progress("track_done", index=index)

    opts = {
        **_YD_OPTS,
        "logger": _ForwardingLogger(on_log),
        "format": "bestaudio/best",
        "outtmpl": str(OUTPUT_DIR / "%(playlist_index)03d - %(title)s.%(ext)s"),
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"}
        ],
        "progress_hooks": [hook],
        "ignoreerrors": True,
        "noplaylist": False,
        "retries": 1,
        "extractor_retries": 1,
    }
    try:
        with YoutubeDL(opts) as ydl:
            ydl.download([playlist.url])
    except DownloadCancelled as exc:
        raise PipelineCancelled(str(exc)) from exc
    except Exception as exc:
        raise PipelineError(f"Download failed: {exc}") from exc


def build_concat_list(files: list[str]) -> None:
    with open(CONCAT_FILE, "w", encoding="utf-8") as f:
        for i, file in enumerate(files):
            safe_file = file.replace("'", "'\\''")
            f.write(f"file '{safe_file}'\n")
            if i < len(files) - 1:
                f.write(f"file '{SILENCE_FILE.absolute()}'\n")


def _probe_duration(path: Path) -> float:
    try:
        out = subprocess.run(
            [
                _FFPROBE,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return float(out) if out else 0.0
    except (subprocess.SubprocessError, ValueError):
        return 0.0


def expected_duration_us(playlist: PlaylistInfo, files: list[str]) -> int:
    durations = [t.duration for t in playlist.tracks if t.duration]
    if len(durations) == len(playlist.tracks):
        total_s = sum(durations) + SILENCE_SECONDS * (len(files) - 1)
    else:
        total_s = sum(_probe_duration(Path(f)) for f in files) + SILENCE_SECONDS * (len(files) - 1)
    return max(1, int(total_s * 1_000_000))


def merge_files(playlist: PlaylistInfo, files: list[str], on_progress, cancel_event) -> None:
    files = sorted(files)
    if not files:
        raise PipelineError("No MP3 files were downloaded, nothing to merge.")

    build_concat_list(files)
    if cancel_event.is_set():
        raise PipelineCancelled("cancelled")

    cmd = [
        _FFMPEG,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(CONCAT_FILE),
        "-c:a",
        "libmp3lame",
        "-b:a",
        BITRATE,
        "-progress",
        "pipe:1",
        "-nostats",
        str(OUTPUT_FILE),
    ]
    expected_us = expected_duration_us(playlist, files)
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    except OSError as exc:
        raise PipelineError(f"Could not run ffmpeg: {exc}") from exc

    last = -1.0
    cancelled = False
    try:
        for line in proc.stdout:
            if cancel_event.is_set():
                cancelled = True
                proc.terminate()
                break
            if line.startswith("out_time_us="):
                try:
                    us = int(line.split("=", 1)[1].strip())
                except ValueError:
                    continue
                percent = min(100.0, us / expected_us * 100.0)
                if percent - last >= 1.0:
                    last = percent
                    on_progress("merge", percent=percent)
    finally:
        if proc.stdout:
            proc.stdout.close()
    proc.wait()

    if cancelled:
        OUTPUT_FILE.unlink(missing_ok=True)
        raise PipelineCancelled("cancelled")
    if proc.returncode != 0:
        OUTPUT_FILE.unlink(missing_ok=True)
        raise PipelineError("ffmpeg failed to merge the tracks.")
    on_progress("merge", percent=100.0)


def _check_cancel(cancel_event) -> None:
    if cancel_event.is_set():
        raise PipelineCancelled("cancelled")


def run_pipeline(
    playlist: PlaylistInfo,
    on_progress,
    cancel_event,
    gate: DownloadGate | None = None,
) -> None:
    """Run the full pipeline: clean, silence, download, then finalize.

    After the downloads the pipeline pauses so the caller can decide whether to
    merge the tracks into one mix or keep each track as its own file.
    """
    _check_cancel(cancel_event)
    on_progress("phase", text="Cleaning output directory")
    prepare_output_dir()

    _check_cancel(cancel_event)
    on_progress("phase", text="Creating silence segment")
    create_silence()

    _check_cancel(cancel_event)
    on_progress("phase", text=f"Downloading {len(playlist.tracks)} tracks")
    download_playlist(playlist, on_progress, cancel_event)

    _check_cancel(cancel_event)
    files = sorted(glob.glob(str(OUTPUT_DIR / "*.mp3")))
    present: dict[int, str] = {}
    for f in files:
        try:
            present[int(Path(f).stem.split(" - ")[0])] = f
        except ValueError:
            continue
    failed = sorted({t.index for t in playlist.tracks} - set(present))
    if failed:
        on_progress("track_failed", indices=failed)

    mode = DownloadGate.MODE_MIX
    if gate is not None:
        mode = gate.await_decision(on_progress, cancel_event)

    if mode == DownloadGate.MODE_INDIVIDUAL:
        _check_cancel(cancel_event)
        on_progress("phase", text="Finished")
        on_progress("done", file=str(OUTPUT_DIR), count=len(files), mode=mode)
        return

    on_progress("phase", text="Merging tracks")
    merge_files(playlist, files, on_progress, cancel_event)

    _check_cancel(cancel_event)
    on_progress("phase", text="Finished")
    on_progress("done", file=str(OUTPUT_FILE), count=len(files), mode=mode)


def _as_seconds(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
