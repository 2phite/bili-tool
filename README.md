# bili-tool

The **ingestion front-door** for a downstream knowledge-base project. Given a bilibili URL, it
produces a timeline-aligned **bundle**:

- an **original-language transcript** (subtitle-reuse when trustworthy, else faster-whisper), and
- **per-frame visual notes** (OCR + figure/slide captions via a local vision model).

The tool starts at a URL and ends at a self-contained `out/<id>-p<part>/` directory
(`bundle.md` + `bundle.json` + `frames/`). It does **not** summarize or extract entities — that
lives downstream. See [SPEC.md](SPEC.md) for the full design and [DECISIONS.md](DECISIONS.md) for
the resolved design calls (referenced inline as `D#`).

## Why a dedicated tool

bilibili hard-walls general agents: `bilibili.com/video/...` returns HTTP 412 to plain requests,
stream/subtitle URLs 403 without a `Referer`, and most content needs a logged-in session cookie.
bili-tool drives `yt-dlp` to handle the auth/signing and turns the result into a clean bundle.

## Prerequisites

- **Python 3.11**
- **ffmpeg** on PATH (or auto-detected from a winget install). Required by yt-dlp + the frame stage.
- A logged-in **Firefox** profile for bilibili (default auth path, D9), or a `SESSDATA` fallback.
- An **NVIDIA GPU** for faster-whisper (CUDA). CPU works but is slow.
- For the vision stage: **LM Studio** running with a **Qwen3-VL** model **and its mmproj projector
  loaded** (the projector is verified at runtime via a nonce-OCR probe, D7).

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -e .                   # spine deps
pip install -e ".[transcribe]"     # + faster-whisper (step 3)
pip install -e ".[frames,vision]"  # + frame extraction & captioning (steps 4-5)

cp .env.example .env               # then fill in LM Studio token + Firefox profile
```

Configure `.env` (see `.env.example`): LM Studio endpoint/token/model, and either
`BILI_COOKIES_PROFILE` (Firefox) or a `SESSDATA` fallback. The real `.env` is gitignored — never
commit it.

## Usage

```bash
bili-tool <url> [--part N] [--all-parts] [--force-whisper] [--robust] [--no-vision]
                [--scene-threshold F] [--out DIR] [--no-frame-images]
```

Defaults: auto part detection, quality-gated subtitle→Whisper decision, vision on.

| flag | effect |
|------|--------|
| `--part N` / `--all-parts` | one part (default: from URL) / loop every part (D12) |
| `--force-whisper` | skip the subtitle, always transcribe with Whisper |
| `--robust` | disable `condition_on_previous_text` (repetition-loop lectures) |
| `--no-vision` | skip frame captioning |
| `--no-frame-images` | omit PNGs from `out/` (caption text still recorded, D8) |

## Output

`out/<id>-p<part>/` — `bundle.md` (the product the KB ingests: provenance header + slide-chunked
transcript/visual notes), `bundle.json` (precise backing record), `frames/` (QA PNGs).

## Build status

End-to-end pipeline working on real `.com` content (transcript + per-slide OCR/captions +
slide-chunked markdown), 32 tests green.

- ✅ Spine: resolve → subtitle probe → `bundle.json` (cookies/Referer/yt-dlp plumbing verified)
- ✅ Quality gate (D5) + faster-whisper large-v3 fallback (CUDA via nvidia-*-cu12 wheels)
- ✅ Frames: periodic sampling + phash dedup (see note below); aria2c for robust CDN downloads
- ✅ Vision captioning + D7 fingerprint-armed nonce-OCR projector check
- ✅ Per-stage caching (D6: transcript/frames/captions) + D3 slide-chunked `bundle.md`

- ✅ `--all-parts` (D12): per-part `?p=N` enumeration + failure isolation (validated on a real
  3-part video, 3/3 parts ok)

**Known gaps (next):** the `--no-frame-images`/`--scene-threshold` levers are parsed but not yet
wired; the subtitle-accept + #6357 part-match path (D4) is built but untested on real
subtitle-bearing content (none of the test videos carry subtitles). `bilibili.tv` is unvalidated.

**Transcription backend:** uses local **faster-whisper** (HF `Systran/faster-whisper-large-v3`,
CUDA). Routing Whisper through LM Studio (to unify with the vision endpoint) is **blocked** — as of
2026-06-29 LM Studio exposes no STT API (no `/v1/audio/transcriptions`, no `lms` transcribe
command), even with `whisper-large-v3-turbo` loaded. `transcribe.py` is kept swappable for when
that endpoint ships.

**Frame extraction note:** uses periodic sampling + perceptual dedup rather than the SPEC's
scene-cut detection — the target content is continuous-shot slide recordings where scene cuts don't
exist. Same goal ("one frame per stable slide"), more robust mechanism. `scene_threshold` is kept in
config as a future secondary signal.
