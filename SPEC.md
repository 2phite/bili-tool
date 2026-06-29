# bili-tool — Build Spec

> Authoritative build spec. Supersedes `Claude app initial project scoping.md` (kept only as a record).
> Audience: Claude Code, implementing from a clean repo.
> Last updated: 2026-06-28.
>
> **Companion: [DECISIONS.md](DECISIONS.md).** Where this spec is vague or silent, that log makes the
> call and is authoritative on that point. Inline `→ D#` markers below point at the relevant decision.
> Implement from this spec; cross-ref the cited `D#` when told to.

---

## 1. Why this tool exists (don't skip — it sets the scope)

Bilibili is a **hard wall** for general-purpose agents. A plain request to `bilibili.com/video/...`
returns **HTTP 412** even with a browser User-Agent; real stream/subtitle URLs return **403**
without a `Referer` header, and most content needs a `SESSDATA` session cookie. Verified directly,
2026-06-28.

So this tool is the **ingestion front-door** for a downstream knowledge-base project: it turns an
otherwise-inaccessible bilibili URL into a clean, immutable, timeline-aligned **bundle** that the Atlas
layer ingests as a raw source. The tool's job **starts** at a URL and **ends** at that bundle. It does
not summarize, extract entities, or write to the wiki — that judgment lives downstream. Keep this seam
clean: bili-tool is a deterministic batch unit; everything interpretive happens elsewhere.

---

## 2. Goal

Given a bilibili URL, produce a **timeline-aligned bundle** of:
- (a) an **original-language transcript** (segment-level, timestamped), and
- (b) **independent per-frame visual notes** (OCR + figure/slide description),

ready for downstream summarization. Primary target: `bilibili.com` UGC lecture/talk content.
`bilibili.tv` is a secondary path that should work but isn't optimized for.

---

## 3. Stack & prerequisites

- **Language:** Python 3.11 (the working interpreter on this box).
- **Download + subtitle extraction:** `yt-dlp` via its **Python API** (not shelling out). Drive yt-dlp
  rather than reimplementing bilibili's WBI signing/auth — keep reverse-engineered signing out of our code.
- **Transcription:** `faster-whisper`, `large-v3` model. (Pin the latest stable version at scaffold time;
  the earlier draft named 1.2.1 — verify current before pinning.)
- **Frames:** `ffmpeg` (system binary) for extraction; **PySceneDetect** (`scenedetect`) for scene-cut
  detection; `Pillow` + `imagehash` for perceptual dedup.
- **Vision:** an OpenAI-compatible endpoint (LM Studio) serving a **Qwen3-VL GGUF** with its mmproj
  projector. Talk to it via the `openai` Python client.
- **Schema/validation:** `pydantic` v2.
- **Hardware:** single RTX 4090, 24 GB (confirmed). large-v3 + a Qwen3-VL GGUF both fit comfortably.

**Prerequisites that are NOT yet installed on this machine** (flag to the user, don't assume):
- `ffmpeg` — absent from PATH. Required by both yt-dlp (audio extraction/mux) and our frame stage.
- `yt-dlp`, `faster-whisper` — absent. Install into the project venv.
- LM Studio must be **running** with the Qwen3-VL model **and its mmproj loaded** before the vision stage.

---

## 4. Repository layout

```
bili-tool/
├── SPEC.md                  # this file
├── pyproject.toml           # deps, entry point
├── README.md                # quickstart + prerequisites
├── .env.example             # SESSDATA + LM Studio endpoint vars (never commit real .env)
├── bili_tool/
│   ├── __init__.py
│   ├── cli.py               # CLI entry point
│   ├── config.py            # settings + secret loading (SESSDATA, endpoint, paths)
│   ├── resolve.py           # URL resolution: b23.tv expand, platform/id/part detection
│   ├── subtitles.py         # yt-dlp subtitle probe (skip_download)
│   ├── quality.py           # subtitle quality gate (sub vs whisper decision)
│   ├── transcribe.py        # faster-whisper
│   ├── frames.py            # ffmpeg + scenedetect extraction, imagehash dedup
│   ├── vision.py            # per-frame captioning via OpenAI-compatible endpoint
│   ├── merge.py             # timeline alignment → bundle.json + bundle.md
│   ├── schema.py            # pydantic Bundle models (the interface contract)
│   └── cache.py             # per-stage caching keyed by {platform}:{id}:{part}
└── cache/                   # gitignored; per-stage artifacts
```

---

## 5. Core flow

1. **Resolve URL** (`resolve.py`): expand `b23.tv` short links; detect `.com` vs `.tv`; extract video id
   (`BV…`/`av…`) and **part index** (`?p=N`, default 1). Emit a canonical `{platform, id, part, url}`.
2. **Probe for an existing original-language subtitle** (`subtitles.py`): call yt-dlp with
   `skip_download=True`. This surfaces `.com` AI auto-captions (`ai-zh`) as a normal subtitle track —
   no media touched, SRT text returned in-memory.
   - **Multi-part assertion (critical):** yt-dlp issue #6357 is open — for multi-part videos it has
     historically returned **part 1's subtitle for every part**. Assert the returned track actually
     corresponds to the requested `part`; if it can't be verified, treat as "no usable sub" and fall
     through to Whisper. Never silently align the wrong text. **→ D4** (two-tier: duration sanity +
     part-1 identity check).
3. **Decide transcript source** (`quality.py`):
   - If a usable original-language sub exists, run it through the **quality gate** (see §7). If it
     passes, use it. If it looks degraded, fall back to Whisper. **→ D5** (any single metric trips →
     Whisper; thresholds in config, calibrated step 3).
   - `--force-whisper` skips the sub entirely.
   - Otherwise download audio and transcribe with faster-whisper (`language="zh"`, `vad_filter=True`,
     `word_timestamps=True`, model `large-v3`).
4. **Frames, in parallel** (`frames.py` → `vision.py`): **→ D7** (fingerprint-armed nonce projector
   probe), **→ D8** (deduped frames shipped to the bundle dir for QA), **→ D10** (caption cache is
   all-or-nothing).
   - Extract candidate frames by **scene cut** (PySceneDetect content detector; configurable threshold).
   - **Perceptual-dedup BEFORE captioning** (imagehash phash, near-duplicate collapse) — on lectures a
     scene cut ≈ a slide change, so caption **one frame per stable segment**, not per raw cut. This is
     the main cost lever (see §7).
   - Caption each surviving frame **independently** with Qwen3-VL, prompted for OCR + figure/slide
     extraction.
5. **Merge** (`merge.py`): align transcript segments + frame notes on a shared timeline. Emit the
   canonical `bundle.json` (the contract) plus a human-readable interleaved `bundle.md`. **→ D1**
   (markdown is the *primary* ingestion surface, JSON is backing record — invert §6's framing),
   **→ D2** (provenance promoted into a readable header), **→ D3** (markdown is slide-chunked; chunking
   always defined, ~60–90s wall-clock fallback when no frame boundaries).

**Caching** (`cache.py`): every stage (audio, subs, transcript, frames, captions, bundle) is cached
keyed by `{platform}:{id}:{part}`. Re-runs must not re-download, re-transcribe, or re-caption unchanged
inputs. **→ D6** (key each stage by `video-identity + stage-param-hash`, not bare identity — the bare
key silently ignores `--force-whisper`/`--scene-threshold`/`--robust`).

---

## 6. The bundle schema (interface contract — pin this, downstream depends on it)

This is consumed by the Atlas project as a raw source landing in `raw/transcripts/`. Treat the shape as a
stable API. **→ D1/D2: the Atlas actually ingests `bundle.md` (an LLM reads prose); `bundle.json` is the
precise backing record, not the primary read. Provenance is promoted into a readable `bundle.md` header.
The "canonical JSON / convenience markdown" framing below is inverted by D1 — keep the JSON precise, but
the markdown is the product.** `bundle.json`:

```jsonc
{
  "schema_version": "1.0",
  "platform": "bilibili.com",          // or "bilibili.tv"
  "id": "BV1xx411x7xx",
  "part": 1,
  "url": "https://www.bilibili.com/video/BV1xx411x7xx",
  "title": "…",
  "uploader": "…",
  "duration_s": 1834,
  "fetched_at": "2026-06-28T12:00:00Z",
  "transcript": {
    "source": "ai-zh" ,                // "human-sub" | "ai-zh" | "whisper"  ← provenance, load-bearing
    "language": "zh",
    "model": "large-v3",               // null when source is a subtitle
    "robust": false,                   // condition_on_previous_text disabled?
    "quality_gate": { "passed": true, "punct_density": 0.11, "dup_ratio": 0.02, "nonzh_ratio": 0.01 },
    "segments": [ { "start": 0.0, "end": 4.2, "text": "…" } ]
  },
  "frames": [
    { "ts": 12.5, "path": "frames/000012_500.png", "phash": "…", "caption": "…", "ocr": "…" }
  ],
  "meta": {
    "cookies_used": true,
    "referer_used": true,
    "vision_model": "qwen3-vl-…",
    "tool_version": "0.1.0"
  }
}
```

**Why `transcript.source` matters:** the downstream Atlas does self-rewriting, auto-resolving contradiction
handling, and it ranks competing claims by source authority. Provenance is that authority signal —
`human-sub` > clean `whisper` > degraded `ai-zh`. This field is not cosmetic; it's an input to the wiki's
reconciliation logic. Always populate it accurately.

Also emit `bundle.md`: transcript and frame notes interleaved in timeline order, human-scannable. The
JSON is canonical; the markdown is a convenience/QA artifact.

---

## 7. Hard constraints & non-obvious traps (the stuff that bites)

- **Referer is mandatory.** Stream and subtitle URLs 403 without a bilibili `Referer`. yt-dlp's bilibili
  extractor sets this, but if you ever touch a URL directly (e.g. fetching a frame thumbnail or stream),
  set `Referer: https://www.bilibili.com`.
- **Cookies (`SESSDATA`)** are required for most `.com` subtitles and higher-quality streams. Load from a
  secret (env var / `.env` / cookies file), pass to yt-dlp, **never log it**, never commit it. `.env.example`
  documents the var; real `.env` is gitignored. **→ D9** (default is `--cookies-from-browser` with a
  Firefox profile; `.env` SESSDATA is the fallback), **→ D11** (no stale-cookie detector; fail loud only
  if the cookie *source* fails; `meta.cookies_used` means "supplied," not "honored").
- **Multi-part subtitle bug (#6357)** — see §5 step 2. Assert part match or fall back.
- **`ai-zh` quality is a coin-flip on lectures.** Default heuristic is NOT "trust the sub." Run the
  **quality gate**: flag degraded subs by low punctuation density, high segment-duplication ratio (repetition),
  abnormal length-vs-duration, or high non-CJK garbage ratio → auto-fall-back to Whisper. In a compounding
  Atlas, a subtly-wrong transcript is the worst failure mode (bad facts harden into accepted truth), so bias
  toward Whisper when in doubt. Keep `--force-whisper` as the manual override.
- **faster-whisper repetition guard:** keep the default hallucination guards. Expose a `--robust` switch
  that disables `condition_on_previous_text` for lectures that degrade into repetition loops.
- **Vision must fail loud:** at startup, verify the mmproj projector is actually loaded by sending one tiny
  test image and confirming a non-empty, image-grounded response. If the projector isn't loaded the model
  **silently ignores images** and emits plausible hallucinated captions — the most dangerous silent failure
  here. If the REST endpoint rejects base64 data-URI images, fall back to the LM Studio SDK path. **→ D7**
  ("non-empty" is too weak — the check is a **nonce-OCR** round-trip the model can't pass blind, armed by a
  loaded-model **fingerprint** so it only fires after LM Studio state changes; transport is one-time
  plumbing settled in build step 5).
- **Perceptual dedup before VL**, not after — captioning is the cost/time sink. Order matters.

---

## 8. Build order (validate the spine before wiring the expensive stages)

Get the **cheapest end-to-end path working first**, on a real video, before Whisper or vision:

1. Scaffold: repo layout, `pyproject.toml`, config/secret loading, pydantic `Bundle` schema, stubbed stages.
2. **Spine:** `resolve → subtitle probe → bundle.json` (transcript-only, frames empty). This validates the
   yt-dlp / cookie / Referer / multi-part plumbing on a real URL in minutes. Everything else hangs off a
   spine you've confirmed works.
3. Add the **quality gate + Whisper fallback**.
4. Add **frames + perceptual dedup** (still no vision — emit frame stubs with phash/ts).
5. Add **vision captioning** (with the fail-loud projector check).
6. Add **per-stage caching** and the `bundle.md` renderer.

Each step ends with a runnable CLI invocation against a real bilibili lecture.

---

## 9. CLI

```
bili-tool <url> [--part N] [--all-parts] [--force-whisper] [--robust] [--no-vision]
                [--scene-threshold F] [--out DIR] [--no-frame-images]
```

Defaults: auto part detection, quality-gated sub→whisper decision, vision on. **→ D12** (`--all-parts`
is an optional isolate-and-continue loop over the single-part unit), **→ D8** (`--no-frame-images` omits
PNGs from `out/`; default ships them for QA).

---

## 10. Out of scope for v1

- Speaker diarization (lectures are single-speaker; skip WhisperX).
- `.com` AI-vs-human subtitle flag filtering beyond the quality gate.
- Translation tracks.
- Any summarization / entity extraction / wiki integration — that's the downstream Atlas project's job.

---

## 11. Open config the implementer should surface to the user

> Several of these are resolved in [DECISIONS.md](DECISIONS.md): SESSDATA storage **→ D9**; output root /
> bundle dir shape **→ D8**; scene threshold default **→ D3/Deferred**. The LM Studio endpoint + model
> name and the faster-whisper pin remain genuine confirm-at-scaffold items.


- Exact LM Studio endpoint URL + model name for Qwen3-VL (and confirm mmproj is loaded).
- Where `SESSDATA` will be stored (env var vs `.env` vs cookies.txt export).
- Scene-detection threshold default (tune on one real lecture during step 4).
- Output root for bundles (the Atlas's `raw/transcripts/` lives in a separate repo — for now write to a local
  `out/` and let the Atlas pull/copy; do NOT hardcode the wiki path here, keep the seam clean).
