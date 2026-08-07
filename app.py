#!/usr/bin/env python3
"""Textual TUI for the YouTube playlist mixer.

Ask for a playlist URL, preview its tracks, then download and merge them
into a single MP3 with short pauses in between.
"""

from __future__ import annotations

import re
import threading

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.validation import ValidationResult, Validator
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Input,
    Label,
    LoadingIndicator,
    ProgressBar,
    Static,
)
from textual.worker import get_current_worker

from pipeline import (
    PipelineCancelled,
    PipelineError,
    PlaylistInfo,
    fetch_playlist,
    run_pipeline,
)

PLAYLIST_URL_RE = re.compile(r"[?&]list=[\w-]{6,}")


class PlaylistValidator(Validator):
    """Reject anything that clearly isn't a YouTube playlist URL."""

    def validate(self, value: str) -> ValidationResult:
        url = value.strip()
        if not url:
            return self.failure("Paste a YouTube playlist URL first.")
        host = url.split("://", 1)[-1].split("/")[0].lower()
        if "youtu" not in host:
            return self.failure("That doesn't look like a YouTube URL.")
        if "list=" not in url:
            return self.failure("No playlist found in the URL (missing list= parameter).")
        if not PLAYLIST_URL_RE.search(url):
            return self.failure("The playlist id looks invalid.")
        return self.success()


class InputScreen(Screen):
    BINDINGS = [("ctrl+q", "app.quit")]

    CSS = """
    InputScreen {
        align: center middle;
    }

    #wrap {
        width: 82;
        height: auto;
        padding: 1 2;
        border: round $accent;
        background: $panel;
    }

    #title {
        width: 100%;
        content-align: center middle;
        text-style: bold;
    }

    #subtitle {
        width: 100%;
        content-align: center middle;
        color: $text-muted;
    }

    Input {
        margin: 1 0 0 0;
    }

    #error {
        width: 100%;
        margin-top: 1;
        color: $error;
        text-style: bold;
    }

    #spinner {
        height: 3;
        display: none;
    }

    #buttons {
        height: 3;
        align-horizontal: center;
    }

    Button {
        min-width: 18;
        margin: 0 1;
    }

    #hint {
        width: 100%;
        content-align: center middle;
        color: $text-muted;
    }
    """

    def __init__(self) -> None:
        self._busy = False
        self._url = ""
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="wrap"):
            yield Static("YouTube Playlist Mixer", id="title")
            yield Static(
                "Download a playlist, add short pauses between tracks and merge into a single mix.",
                id="subtitle",
            )
            yield Input(
                placeholder="https://www.youtube.com/playlist?list=...",
                validators=[PlaylistValidator()],
                id="url-input",
            )
            yield Label("", id="error")
            yield LoadingIndicator(id="spinner")
            with Horizontal(id="buttons"):
                yield Button("Fetch Playlist", id="fetch", variant="primary")
                yield Button("Quit", id="quit", variant="error")
            yield Static(
                "Your URL is validated before anything runs. Press Enter to fetch.",
                id="hint",
            )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    @on(Input.Submitted)
    def on_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_fetch()

    @on(Button.Pressed, "#fetch")
    def on_fetch_pressed(self) -> None:
        self.action_fetch()

    @on(Button.Pressed, "#quit")
    def on_quit_pressed(self) -> None:
        self.app.exit()

    def action_fetch(self) -> None:
        if self._busy:
            return
        url = self.query_one(Input).value.strip()
        result = PlaylistValidator().validate(url)
        error = self.query_one("#error", Label)
        if not result.is_valid:
            error.update(" - ".join(result.failure_descriptions))
            self.query_one(Input).focus()
            return
        error.update("")
        self._busy = True
        self._url = url
        self.query_one(Input).disabled = True
        self.query_one("#fetch", Button).disabled = True
        spinner = self.query_one("#spinner", LoadingIndicator)
        spinner.display = True
        spinner.loading = True
        self.fetch_playlist()

    @work(thread=True, exclusive=True)
    def fetch_playlist(self) -> None:
        worker = get_current_worker()
        try:
            info = fetch_playlist(self._url)
        except Exception as exc:
            if not worker.is_cancelled:
                self._safe(self._fetch_failed, str(exc))
            return
        if not worker.is_cancelled:
            self._safe(self._fetch_ok, info)

    def _fetch_ok(self, info: PlaylistInfo) -> None:
        self._set_idle()
        self.app.push_screen(PreviewScreen(info))

    def _fetch_failed(self, message: str) -> None:
        self._set_idle()
        self.query_one("#error", Label).update(f"Could not fetch playlist: {message}")
        self.query_one(Input).focus()

    def _set_idle(self) -> None:
        self._busy = False
        self.query_one(Input).disabled = False
        self.query_one("#fetch", Button).disabled = False
        spinner = self.query_one("#spinner", LoadingIndicator)
        spinner.loading = False
        spinner.display = False

    def _safe(self, func, *args, **kwargs) -> None:
        try:
            self.app.call_from_thread(func, *args, **kwargs)
        except Exception:
            pass


class PreviewScreen(Screen):
    BINDINGS = [("escape", "go_back"), ("ctrl+q", "app.quit")]

    CSS = """
    PreviewScreen {
        padding: 0 2;
    }

    #wrap {
        height: 1fr;
        padding: 1 0;
    }

    #title {
        width: 100%;
        content-align: center top;
        text-style: bold;
    }

    #meta {
        width: 100%;
        content-align: center top;
        color: $text-muted;
    }

    #track-table {
        height: 1fr;
        margin: 1 0;
        border: round $primary;
    }

    #buttons {
        height: 3;
        align-horizontal: center;
    }

    Button {
        min-width: 16;
        margin: 0 1;
    }
    """

    def __init__(self, playlist: PlaylistInfo) -> None:
        self.playlist = playlist
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="wrap"):
            yield Static(self.playlist.title, id="title")
            yield Static(self._meta_text(), id="meta")
            yield DataTable(id="track-table", zebra_stripes=True, cursor_type="row")
            with Horizontal(id="buttons"):
                yield Button("Start Mix", id="start", variant="primary")
                yield Button("Back", id="back", variant="default")
        yield Footer()

    def _meta_text(self) -> str:
        total = sum(t.duration or 0 for t in self.playlist.tracks)
        parts = [f"{len(self.playlist.tracks)} tracks"]
        if total:
            parts.append(f"≈ {fmt_duration(total)}")
        if self.playlist.channel:
            parts.append(self.playlist.channel)
        return "  -  ".join(parts)

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_column("#", key="index", width=6)
        table.add_column("Title", key="title")
        table.add_column("Channel", key="channel")
        table.add_column("Duration", key="duration", width=10)
        for track in self.playlist.tracks:
            table.add_row(
                str(track.index),
                track.title,
                track.channel or "—",
                fmt_duration(track.duration) if track.duration else "—",
            )

    def action_go_back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#start")
    def on_start(self) -> None:
        self.app.push_screen(ProgressScreen(self.playlist))

    @on(Button.Pressed, "#back")
    def on_back(self) -> None:
        self.action_go_back()


class ProgressScreen(Screen):
    BINDINGS = [("ctrl+q", "app.quit")]

    CSS = """
    ProgressScreen {
        padding: 0 2;
    }

    #wrap {
        height: 1fr;
        padding: 1 0;
    }

    #title {
        width: 100%;
        content-align: center top;
        text-style: bold;
    }

    #phase {
        width: 100%;
        margin: 1 0 0 0;
        text-style: italic;
    }

    #tracks-bar {
        margin: 0 0 1 0;
    }

    #track-label {
        width: 100%;
    }

    #track-bar {
        margin: 0 0 1 0;
    }

    #merge-label {
        width: 100%;
    }

    #merge-bar {
        margin: 0 0 1 0;
    }

    #log {
        width: 100%;
        height: 4;
        margin: 0 0 1 0;
        border: round $warning 30%;
        padding: 0 1;
    }

    .hidden {
        display: none;
    }

    #track-table {
        height: 1fr;
        margin: 1 0;
        border: round $primary;
    }

    #buttons {
        height: 3;
        align-horizontal: center;
    }

    Button {
        min-width: 16;
        margin: 0 1;
    }
    """

    def __init__(self, playlist: PlaylistInfo) -> None:
        self.playlist = playlist
        self.cancel_event = threading.Event()
        self.row_keys: dict[int, object] = {}
        self._log_lines: list[tuple[str, str]] = []
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="wrap"):
            yield Static(self.playlist.title, id="title")
            yield Static("Preparing…", id="phase")
            yield ProgressBar(total=len(self.playlist.tracks), id="tracks-bar")
            yield Static("", id="track-label")
            yield ProgressBar(total=100, id="track-bar")
            yield Static("", id="merge-label", classes="hidden")
            yield ProgressBar(total=100, id="merge-bar", classes="hidden")
            yield Static("", id="log", classes="hidden")
            yield DataTable(id="track-table", zebra_stripes=True, cursor_type="row")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel", variant="warning")
                yield Button("New Mix", id="new-mix", variant="primary", disabled=True)
                yield Button("Quit", id="quit", variant="error", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_column("#", key="index", width=6)
        table.add_column("Title", key="title")
        table.add_column("Duration", key="duration", width=10)
        table.add_column("Status", key="status", width=20)
        for track in self.playlist.tracks:
            key = table.add_row(
                str(track.index),
                track.title,
                fmt_duration(track.duration) if track.duration else "—",
                Text("pending", style="dim"),
            )
            self.row_keys[track.index] = key
        self.run_pipeline()

    @work(thread=True, exclusive=True)
    def run_pipeline(self) -> None:
        def on_progress(kind: str, **kwargs) -> None:
            self._safe(self._handle_progress, kind, kwargs)

        try:
            run_pipeline(self.playlist, on_progress, self.cancel_event)
        except PipelineCancelled:
            self._safe(self._handle_progress, "cancelled", {})
        except PipelineError as exc:
            self._safe(self._handle_progress, "error", {"message": str(exc)})
        except Exception as exc:
            self._safe(self._handle_progress, "error", {"message": f"{type(exc).__name__}: {exc}"})

    def _safe(self, func, *args, **kwargs) -> None:
        try:
            self.app.call_from_thread(func, *args, **kwargs)
        except Exception:
            pass

    # ----- UI updates (run on the app thread) -----

    def _handle_progress(self, kind: str, data: dict) -> None:
        try:
            if kind == "phase":
                self._on_phase(data.get("text", ""))
            elif kind == "track":
                self._on_track(data)
            elif kind == "track_done":
                self._on_track_done(data.get("index"))
            elif kind == "track_failed":
                for index in data.get("indices", []):
                    self._set_status(index, Text("failed", style="bold red"))
            elif kind == "track_error":
                self._on_track_error(data)
            elif kind == "download_log":
                self._on_download_log(data)
            elif kind == "merge":
                self.query_one("#merge-bar", ProgressBar).update(progress=data.get("percent", 0))
            elif kind == "done":
                self._on_done(data)
            elif kind == "error":
                self._on_error(data.get("message", "Unknown error"))
            elif kind == "cancelled":
                self._on_cancelled()
        except Exception:
            pass

    def _on_phase(self, text: str) -> None:
        self.query_one("#phase", Static).update(text)
        if text.startswith("Downloading"):
            self._show_download_bars()
        elif text == "Merging tracks":
            self._show_merge_bars()

    def _on_track(self, data: dict) -> None:
        index = data.get("index")
        title = data.get("title", "")
        percent = data.get("percent", 0.0)
        speed = data.get("speed")
        eta = data.get("eta")
        label = f"Track {index}/{len(self.playlist.tracks)}: {title}"
        if speed is not None:
            label += f"  {fmt_speed(speed)}"
        if eta is not None:
            label += f"  ETA {fmt_eta(eta)}"
        self.query_one("#track-label", Static).update(label)
        self.query_one("#track-bar", ProgressBar).update(progress=percent)
        self._set_status(index, Text(f"downloading {percent:.0f}%", style="bold yellow"))

    def _on_track_done(self, index) -> None:
        self.query_one("#tracks-bar", ProgressBar).advance(1)
        self._set_status(index, Text("done", style="bold green"))
        self.query_one("#track-bar", ProgressBar).update(progress=0)

    def _set_status(self, index, status: Text) -> None:
        key = self.row_keys.get(index)
        if key is not None:
            self.query_one(DataTable).update_cell(key, "status", status)

    def _show_download_bars(self) -> None:
        self.query_one("#track-label", Static).set_class(False, "hidden")
        self.query_one("#track-bar", ProgressBar).set_class(False, "hidden")
        self.query_one("#merge-label", Static).set_class(True, "hidden")
        self.query_one("#merge-bar", ProgressBar).set_class(True, "hidden")

    def _show_merge_bars(self) -> None:
        self.query_one("#track-label", Static).set_class(True, "hidden")
        self.query_one("#track-bar", ProgressBar).set_class(True, "hidden")
        self.query_one("#merge-label", Static).set_class(False, "hidden")
        self.query_one("#merge-bar", ProgressBar).set_class(False, "hidden")

    def _on_track_error(self, data: dict) -> None:
        index = data.get("index")
        message = data.get("message", "")
        reason = message.replace("ERROR: ", "", 1).strip()
        self._set_status(index, Text("failed", style="bold red"))
        self._append_log(reason)

    def _on_download_log(self, data: dict) -> None:
        level = data.get("level", "info")
        message = data.get("message", "")
        self._append_log(f"[{level.upper()}] {message}")

    def _append_log(self, line: str) -> None:
        self._log_lines.append(line)
        self._log_lines = self._log_lines[-6:]
        log = self.query_one("#log", Static)
        log.update("\n".join(self._log_lines))
        log.set_class(False, "hidden")

    def _finish(self) -> None:
        self.query_one("#cancel", Button).disabled = True
        self.query_one("#new-mix", Button).disabled = False
        self.query_one("#quit", Button).disabled = False

    def _on_done(self, data: dict) -> None:
        count = data.get("count", 0)
        out = data.get("file", "")
        plural = "s" if count != 1 else ""
        if count < len(self.playlist.tracks):
            failed = len(self.playlist.tracks) - count
            msg = (
                f"Done! Merged {count} of {len(self.playlist.tracks)} tracks "
                f"into {out} ({failed} failed)"
            )
            self.query_one("#phase", Static).update(msg)
        else:
            msg = f"Done! Merged {count} track{plural} into {out}"
            self.query_one("#phase", Static).update(msg)
        self.query_one("#merge-label", Static).update("")
        self._finish()

    def _on_error(self, message: str) -> None:
        self.query_one("#phase", Static).update(f"Error: {message}")
        self._finish()

    def _on_cancelled(self) -> None:
        self.query_one("#phase", Static).update("Cancelled.")
        self._finish()

    @on(Button.Pressed, "#cancel")
    def on_cancel(self) -> None:
        self.cancel_event.set()
        self.query_one("#cancel", Button).disabled = True
        self.query_one("#phase", Static).update("Cancelling…")

    @on(Button.Pressed, "#new-mix")
    def on_new_mix(self) -> None:
        while len(self.app.screen_stack) > 1:
            self.app.pop_screen()
        self.app.push_screen(InputScreen())

    @on(Button.Pressed, "#quit")
    def on_quit(self) -> None:
        self.app.exit()


class PlaylistMixerApp(App):
    TITLE = "YouTube Playlist Mixer"
    SUB_TITLE = "download, space out and merge a playlist into one audio file"
    BINDINGS = [("ctrl+q", "quit", "Quit")]

    def on_mount(self) -> None:
        self.push_screen(InputScreen())


# ----- helpers -----


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def fmt_speed(bytes_per_sec) -> str:
    bps = max(float(bytes_per_sec or 0), 0.0)
    for unit in ("B/s", "KiB/s", "MiB/s", "GiB/s"):
        if bps < 1024:
            return f"{bps:.0f} {unit}"
        bps /= 1024
    return f"{bps:.1f} GiB/s"


def fmt_eta(seconds) -> str:
    seconds = max(0, int(seconds or 0))
    return f"{seconds // 60}:{seconds % 60:02d}"


def main() -> None:
    PlaylistMixerApp().run()


if __name__ == "__main__":
    main()
