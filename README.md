# YouTube Playlist Mixer

A terminal app that turns a YouTube playlist into a single MP3 mix. It downloads every track, adds a short pause between songs, and merges them all into one audio file, all from an interactive TUI built with [Textual](https://textual.textualize.io/).

## Features

- Paste a playlist URL, the URL is validated before anything runs
- Preview the playlist (tracks, channels, durations) before starting
- Downloads each track as MP3 with per-track progress, speed and ETA
- Inserts a 0.5s pause between tracks, then merges everything into a single `playlist_mix.mp3`
- Live per-track status table, log of failed downloads and merge progress bar
- Cancel anytime (failed tracks are skipped, the rest are still merged)

## Requirements

- Python 3.11+
- [ffmpeg](https://ffmpeg.org/) and ffprobe (for the silence segment, MP3 conversion and merging)

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

ffmpeg is a system package, install it with your distro's package manager (e.g. `sudo apt install ffmpeg`).

## Usage

```bash
python app.py
```

1. Paste a YouTube playlist URL and press Enter.
2. Review the tracks, then hit **Start Mix**.
3. Wait for the download and merge to finish. The result is written to `playlist_mix.mp3` in the project root.

Keybindings: `Ctrl+Q` quits, `Esc` goes back from the preview screen.

## How it works

`pipeline.py` holds the core logic, `app.py` is the TUI that drives it:

1. **Fetch** – reads the playlist metadata (title, channel, tracks) without downloading anything.
2. **Prepare** – cleans the output directory and generates a short silence segment with ffmpeg.
3. **Download** – uses `yt-dlp` to download each track and convert it to MP3 into `downloads/`.
4. **Merge** – writes an ffmpeg concat list that interleaves the silence segment between tracks and produces `playlist_mix.mp3`.

## Configuration

Tunables live at the top of `pipeline.py`:

| Setting | Default | Description |
| --- | --- | --- |
| `SILENCE_SECONDS` | `0.5` | Pause length between tracks |
| `BITRATE` | `320k` | Output MP3 bitrate |
| `OUTPUT_FILE` | `playlist_mix.mp3` | Final mix file name |

## Output

- `playlist_mix.mp3` – the final mix
- `downloads/` – individual MP3 tracks (named `001 - Title.mp3`)
- `silence.mp3` – generated silence segment
- `concat.txt` – ffmpeg concat list used for the merge

Intermediate files (`downloads/`, `silence.mp3`, `concat.txt`) are cleaned and regenerated on every run.

## Development

Lint with [ruff](https://docs.astral.sh/ruff/) (config in `ruff.toml`):

```bash
ruff check .
```

## License

MIT
