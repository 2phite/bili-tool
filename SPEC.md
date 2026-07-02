# harvest — Design Spec

> Authoritative design for **harvest**: a multi-source video → knowledge-bundle ingestion tool.
> This is the single source of truth for *what* to build and *why*. [PROTOCOL.md](PROTOCOL.md) is
> the machine-facing contract the downstream **Atlas** project codes against; [README.md](README.md)
> is the human quickstart.
>
> harvest is the successor to `bili-tool` (bilibili-only). It keeps that tool's proven, platform-
> agnostic back-end and generalizes the acquisition front-end to multiple sources.

---

## 1. What it is

harvest is the **ingestion front-door** for the Atlas knowledge base. Given a video URL from a
supported source, it produces a timeline-aligned, self-contained **bundle**:

- an **original-language transcript** (reuse trustworthy captions, else faster-whisper), and
- **per-frame visual notes** (OCR + figure/slide captions via a local vision model).

The tool **starts** at a URL and **ends** at `out/<id>-p<part>/` (`bundle.md` + `bundle.json` +
`frames/`). It does **not** summarize or extract entities — that judgment lives downstream in Atlas.
Keep this seam clean: harvest is a deterministic batch unit; everything interpretive happens
elsewhere.

The defining superpower is **authenticated acquisition from walled/rich media sources, then do
whatever with what you pulled** — today that "whatever" is the bundle; the architecture is built so
future outputs (raw media collection, danmaku, thumbnails, AV remux) are additive.

## 2. Scope

**v1 targets:** single **public** videos on **bilibili.com** and **YouTube**. Content is "people
talking" (lectures, talks) — single-speaker, slide-or-talking-head.

**Deferred** (architecture accommodates, not built): playlists, live VODs, members-only/age-gated
bulk flows, `bilibili.tv`, and roadmap stages (danmaku, thumbnail metadata, AV remux for
collection).

## 3. Stack

- **Python 3.11.** Download/metadata/subtitles via **yt-dlp** (Python API, not shelling out).
- **Transcription:** `faster-whisper` `large-v3`, CUDA (RTX 4090). CUDA-12 runtime via `nvidia-*-cu12`
  wheels registered on PATH (ctranslate2 uses plain `LoadLibrary`).
- **Frames:** `ffmpeg` (system) periodic sampling; `Pillow` + `imagehash` phash dedup.
- **Vision:** OpenAI-compatible endpoint (**LM Studio**) serving a VL model + its mmproj projector.
- **Schema:** `pydantic` v2. **Downloads:** `aria2c` preferred for throttled CDNs, native fallback.

## 4. Architecture — modular monolith

harvest is a **modular monolith**, not microservices: the pipeline shuttles large local artifacts
(video, audio, frames) and is GPU-bound on one local GPU, so data locality and a shared local cache
beat any network decomposition. Two internal seams give the extensibility of services without the
distributed-systems tax:

### 4.1 Provider seam (per-source acquisition)

A `Provider` is selected by URL and owns **only** the platform-specific acquisition. Everything
downstream consumes normalized outputs and never sees a platform.

```
Provider (protocol; one impl per source):
  matches(url) -> bool                                 # registry selects the provider
  resolve(url) -> Canonical                            # {platform, id, part, url}
  auth_opts(settings) -> dict                          # yt-dlp opts fragment: platform auth
  fetch_metadata(canonical, settings) -> SourceMetadata
  enumerate_parts(canonical, settings) -> int          # bilibili: pages; youtube: 1
  fetch_subtitle(canonical, settings, meta) -> list[Segment] | None
```

- **`SourceMetadata`** is the normalized metadata type every provider produces. It **replaces**
  the old `ViewData`-or-yt-dlp-`info` branching — `merge`/`probe` read only `SourceMetadata`, no
  platform branches. bilibili fills it from the player API; YouTube from yt-dlp's `info`.
- **Media download stays shared** (one yt-dlp downloader for audio/video). The provider contributes
  only `auth_opts()` — the *one* thing that actually varies per source. A genuinely non-yt-dlp
  source would be the trigger to push download into the provider; don't prepay for it.

### 4.2 Stage sequence (per-capability processing)

The agnostic core runs as a sequence of stages over local artifacts: **transcribe → frames →
vision → merge**. Each is independent, cacheable, and skippable (`--no-vision`). This stays the
existing implicit sequence — **not** formalized into a registry yet; that's a named-trigger later
extraction. Future capabilities (danmaku, thumbnails, remux) slot in as new stages.

### 4.3 The one external service

The **LLM backend stays external** (LM Studio over HTTP). It earned a service boundary because it's
heavy, separately managed, and potentially shared with Atlas. Nothing else does.

### 4.4 When to extract a service later (named triggers, not now)

1. A second concurrent GPU consumer → a job-queue worker service.
2. Acquisition needs a different trust/network context than processing → split the *provider* layer
   out along the seam that already exists.
3. The bundle becomes a polled API → a thin serving layer *around* the monolith.

## 5. Core flow

1. **Resolve** — the registry picks a provider by URL; `provider.resolve(url)` → `Canonical
   {platform, id, part, url}`. The single-part triple is the atomic, cached, identity-bearing unit.
2. **Metadata** — `provider.fetch_metadata` → `SourceMetadata` (title, uploader, `uploader_id`,
   duration, `published_at`, pages/parts). One cheap call; the source of truth for bundle metadata.
3. **Transcript decision** — see §6.
4. **Frames** (unless `--no-vision`): periodic sampling (`ffmpeg fps=1/interval`, default 6s) +
   phash dedup **before** captioning (captioning is the cost sink; order matters). Then caption each
   surviving frame independently with the VL model.
5. **Merge** — align transcript segments + frame notes on a shared timeline; emit `bundle.json`
   (precise backing record) and `bundle.md` (the product Atlas ingests).

**Caching** — every stage keyed by `video-identity + stage-param-hash`, never bare identity. Changing
`--force-whisper`/`--dedup-threshold`/`--robust`/`--lang` must not force a re-download or
re-transcribe of unrelated stages, but must not silently return a stale result computed under
different params either.

## 6. Transcript logic (per source)

Common invariant: produce an **original-language** transcript; **never silently align the wrong
text**; bias toward Whisper when in doubt (a subtly-wrong transcript hardens into accepted truth in
Atlas — the worst failure mode). Provenance is recorded and load-bearing (§8).

**bilibili** (unchanged from bili-tool):
- Prefer human sub > AI caption; original zh only. yt-dlp does **not** surface bilibili AI subs, so a
  cookie-authenticated `x/player/v2` fallback (no WBI signing) fetches them; `ai_type` 0 = original
  transcription, 1 = translation (translations ignored).
- **Quality gate** (any one metric trips → Whisper): punct density, dup ratio, non-CJK ratio, cps.
  Thresholds are calibratable config, not law.
- **#6357 two-tier assertion:** tier-1 duration sanity (last cue vs part duration, 0.70–1.10); tier-2
  (part > 1) reject if text is near-identical to part 1 (yt-dlp's #6357 signature). Fail → Whisper.

**YouTube** (simpler — yt-dlp is native, no wall, no player-API workaround):
- **Human captions only** (`info["subtitles"]`), reused **only** on an **exact original-language key
  match**. Resolve the target language as: `--lang` if the caller pinned it, else `info["language"]`
  if truthy, else **unknown**. Then:
  - unknown → **Whisper** (we cannot identify the original track; do **not** trust an arbitrary human
    track — see probe wrinkle below);
  - known `L` and `subtitles[L]` exists → reuse it (`human-sub`, language `L`);
  - otherwise → Whisper.
- **Auto-captions (`info["automatic_captions"]`) are never consulted** — probe confirmed they're a
  separate dict (an un-gatable ASR/MT coin-flip of ~150 language variants); ignoring that dict is the
  whole skip-auto rule.
- **Exact key match only — no fuzzy/primary-subtag matching.** `subtitles` also carries hash-suffixed
  *community translation* tracks (e.g. `en-US-njLgzgtehjs` on a Spanish video); a fuzzy `en ≈ en-US-…`
  match would reuse a translation as if it were the original. Exact match avoids this.
- **No quality gate** — it can't be reliably calibrated across unknown languages, so it would misfire.
  Whisper is the safety net.
- **Field mapping** (from the probe): `uploader_id` ← `info["channel_id"]` (`UC…`, stable — not the
  mutable `@handle` in yt-dlp's `uploader_id`); `published_at` ← `info["timestamp"]` (unix epoch →
  UTC `…Z`), falling back to `upload_date` (`YYYYMMDD` → `T00:00:00Z`); subtitle tracks are
  `list[{ext,url,name}]` — fetch the **`vtt`** entry and parse WebVTT (`parse_vtt`, alongside the
  existing `parse_bcc`/`parse_srt`).

**Language:** default `language=None` (Whisper auto-detects from the opening window). Shared `--lang`
override lets the caller pin the language (from probe hints); platform-aware default is `zh` for
bilibili, `None` for YouTube. Whisper picks one language for the whole audio (no code-switching), so
`--lang` is how a caller deliberately pins the dominant language of mixed content.

## 7. Testing

Deterministic and **offline** by default: inject `opener`s returning canned JSON, use trimmed yt-dlp
`info` dicts as fixtures, never touch the network in the default suite. One opt-in `@live` smoke test
(excluded from the default run) hits a single known-stable public video to catch drift. Capture
trimmed fixtures early — they double as documentation of the provider contract surface.

**Must-verify empirical probes — RESOLVED (5-video live probe, yt-dlp 2026.06.09):**
1. Is `info["language"]` reliable? **No — treat as best-effort.** Correct when present (`en`,`ko`),
   but `None` for 2/5, including a Spanish video that *did* carry an `es` track. `language=None` does
   **not** mean "no captions." → §6's degradation stands: unknown language ⇒ straight to Whisper.
2. Human vs auto distinct? **Yes, cleanly** — separate `subtitles` / `automatic_captions` dicts; a
   human `en` and auto `en` coexist without overlap. §6's skip-auto rule is enforceable by ignoring
   `automatic_captions`. (Wrinkle: `subtitles` also holds hash-suffixed community translations →
   exact-key-match only; see §6.)

Trimmed fixtures seeding the four YouTube branches live in `tests/fixtures/youtube/`
(`dQw4w9WgXcQ` human-sub, `kJQP7kiw5Fk` language-None→whisper, `9bZkp7q19f0` no-human-sub→whisper,
`aqz-KE-bpKQ` no-captions baseline).

## 8. Design decisions & rationale (the calls that govern the code)

- **bundle.md is the product; bundle.json is the backing record.** Atlas is an LLM reading prose, not
  an indexer reading fields. Provenance and the transcript decision live in a readable `bundle.md`
  header, because the model weighs authority the way it weighs any source's — there is no code
  ranking `transcript.source`.
- **Provenance = production method, separate from language.** `human-sub` | `auto-sub` | `whisper`;
  language is its own field. Authority order (documented for Atlas): `human-sub` > `whisper` >
  `auto-sub`.
- **bundle.md is slide-chunked, always.** Chunk = "what was on screen + the speech while it was up."
  Boundaries = deduped frame timestamps when vision is on, else a fixed wall-clock window (~75s).
  Segments assigned whole by start timestamp, never split. One coarse `[mm:ss]` header per chunk.
- **Frame candidates = periodic sampling + phash dedup**, not scene-cut detection: target content is
  continuous-shot screen recordings with soft slide transitions (no hard cuts). Same goal ("one frame
  per stable slide"), more robust mechanism. Dedup compares against the last *kept* frame.
- **Cache keys = identity + stage-param-hash** (§5). The bare key silently returns a cached `auto-sub`
  transcript when you pass `--force-whisper` — a correctness bug, not just staleness.
- **Projector check = fingerprint-armed nonce-OCR probe.** A missing mmproj makes the VL model return
  confident, well-formed, hallucinated captions while every health check passes. At vision-stage start
  hash the loaded-model metadata; on change, render a PNG with a random nonce and require the model to
  read it back. Fail → hard-stop, loud error. Never degrade to silent caption-less frames.
- **Delivered bundle = self-contained `out/<id>-p<part>/`.** Frame PNGs ship for *your* QA/reprocessing
  (Atlas reads only the markdown text); `--no-frame-images` omits them (JSON keeps phash/ts/caption).
  `cache/` holds expensive intermediates (raw audio, pre-dedup frames), gitignored.
- **Auth is per-provider `auth_opts()`.** bilibili: cookies effectively required — default
  `--cookies-from-browser` (Firefox; Chromium locks the DB on Windows), `.env` SESSDATA fallback.
  YouTube: cookies **optional** — public videos need none; a configured browser profile unlocks
  age-gated/bot-checked content. No stale-cookie detector; fail loud only if the cookie *source*
  itself fails. `meta.cookies_used` means "supplied," not "honored."
- **`published_at` timezone is per-provider.** bilibili is China Standard Time (`+08:00`); YouTube is
  UTC. Centralized so each provider maps its own source timezone.
- **Single-part atomic unit; `--all-parts` is isolate-and-continue.** `{platform, id, part}` → one
  bundle dir. `--all-parts` loops the single-part pipeline; a failed part logs and continues; re-runs
  skip done parts via caching. YouTube v1 is always `part=1`; a future playlist entry maps to a part.
- **CLI verb grammar.** `harvest <verb> <url>`: `ingest` (full pipeline), `probe` (cheap metadata, no
  media). No bare-url form. Scales to future verbs (`collect`, …) and sources.

## 9. Repository layout (target)

```
harvest/
├── SPEC.md · PROTOCOL.md · README.md
├── pyproject.toml · .env.example
├── harvest/
│   ├── cli.py              # verb dispatch, per-part orchestration
│   ├── config.py           # settings + per-provider auth/secret loading
│   ├── schema.py           # pydantic Bundle/SourceMetadata/ProbeResult (the contract)
│   ├── providers/          # provider registry + one module per source
│   │   ├── base.py         #   Provider protocol, SourceMetadata, registry
│   │   ├── bilibili.py     #   resolve + player-API metadata/subtitles (was resolve/player_api)
│   │   └── youtube.py      #   resolve + yt-dlp-native metadata/subtitles
│   ├── subtitles.py        # shared yt-dlp subtitle plumbing + parsers (bcc/srt)
│   ├── quality.py          # bilibili quality gate
│   ├── transcribe.py       # faster-whisper (shared, --lang aware)
│   ├── frames.py · vision.py · merge.py · cache.py
└── cache/                  # gitignored per-stage artifacts
```

> The package rename (`bili_tool` → `harvest`), env prefix (`BILI_*`/`SESSDATA` → `HARVEST_*` +
> per-provider), and git remote are a mechanical pass folded into the refactor.

## 10. Known follow-ups (deferred, not defects)

These are economy/purity trade-offs surfaced by the YouTube whole-branch review. None affect
correctness; each is a deliberate consequence of keeping the provider seam clean (metadata does not
leak provider-internal fetch state — e.g. bilibili `ViewData` — through `SourceMetadata`).

- **Redundant per-part metadata fetch (bilibili).** `fetch_metadata` runs the view API, then
  `fetch_subtitle` re-runs `extract_info` + a second view call (more for `part > 1`). `meta` is passed
  into `fetch_subtitle` but unused. Worth a design pass on whether the seam can carry a reusable
  handle without re-leaking `ViewData`. (`providers/bilibili.py`, `cli.py` per-part path)
- **Double `extract_info` (YouTube).** `fetch_metadata` and `fetch_subtitle` each extract in
  production. Same root cause and same fix shape as the bilibili item. (`providers/youtube.py`)
- **`.tv` guard is a platform control-flow branch**, duplicated in `cli.py` (ingest) and `probe.py`
  rather than expressed through the provider registry — a seam-purity wrinkle, unreachable-path today.
- **Dead field `SubtitleResult.last_cue_end`** (pre-existing), cleanup only. (`subtitles.py`)
