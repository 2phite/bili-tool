# bili-tool — Decision Log

> Resolutions to the under-specified / silent branches in [SPEC.md](SPEC.md).
> SPEC.md is authoritative for *what* to build; this log is authoritative wherever it
> makes a decision SPEC.md left vague. Implementation starts from SPEC.md and cross-refs
> these `D#` entries when told to. Each entry records the decision and the *why*.
> Established 2026-06-28 via a design grilling.

Status legend: **Locked** = decided; **Calibrate** = decided in principle, numbers tuned at build time.

---

## D1 — Markdown is the ingestion surface; JSON is the backing record  · Locked
**Touches:** §6, `merge.py`, `schema.py`

`bundle.md` is what the downstream Atlas ingests, because the Atlas is an **LLM agent reading prose**, not code indexing fields (confirmed directly by the downstream-Atlas model). `bundle.json` is demoted from "the contract the Atlas reads" to a **precise machine-backing record** for re-alignment, caching, QA, and any future programmatic step.

- SPEC.md §6 currently calls the markdown a "convenience/QA artifact" — that is now **wrong**; invert it.
- Do **not** over-engineer JSON leanness for an indexer that won't exist.

**Why:** one known consumer, and it's a model. Feeding it segment-granular JSON wastes tokens on repeated keys, braces, and float precision it doesn't need to *understand* content.

---

## D2 — Provenance promoted into a readable header  · Locked
**Touches:** §6, `merge.py`

The Atlas weighs provenance the way a model weighs any source's authority — there is no ranking algorithm reading `transcript.source`. So provenance must live **in the prose the model sees**: a header / frontmatter block at the top of `bundle.md`.

- Header states the transcript source **and the decision reason**, e.g.
  `transcript_source: ai-zh (quality-gate: passed)` /
  `whisper (subtitle rejected: dup_ratio 0.41 > 0.30)` /
  `whisper (subtitle rejected: failed part-match assertion, #6357)`.
- JSON still carries the structured `transcript.source` + `quality_gate` block, but the
  header is the load-bearing copy.

**Why:** "build the schema as if a model reads the header, not as if code ranks fields" — the downstream Atlas's own guidance.

---

## D3 — Markdown is slide-chunked; chunking is *always* defined  · Locked (window size: Calibrate)
**Touches:** §2, §5 step 5, `merge.py`, `frames.py`

The markdown is partitioned into chunks, each = "what was on screen (OCR + figure caption) + the speech while it was up." This makes alignment load-bearing instead of decorative.

- **Chunk boundaries = deduped frame timestamps** when vision is on.
- **Fallback = fixed wall-clock window (~60–90s, tunable)** when frame boundaries are sparse/absent — i.e. `--no-vision`, pre-vision spine builds (build steps 2–4), and talking-head content with no scene cuts. Chunking therefore exists under *every* input condition; vision only sharpens it.
- **Transcript segments are assigned whole to a chunk by their `start` timestamp; never split a segment** across a boundary. Boundaries snap to the nearest segment edge.
- Each chunk gets **one coarse `[mm:ss]` header**; per-utterance timestamps stay in JSON only.

**Why:** "slide + the speech over it" is the natural semantic unit of a lecture and collapses hundreds of 4s segments into a dozen-ish blocks. The product is named for alignment; this is where it becomes real.

---

## D4 — #6357 part-match: two-tier assertion  · Locked
**Touches:** §5 step 2, §7, `subtitles.py`

SPEC.md says "assert the sub corresponds to the requested part" but gives no test. The bug returns **part 1's subtitle for every part**, so `part=1` can never detect it; the danger is only `part>1`.

1. **Cheap sanity gate (all parts):** compare the part's real `duration_s` (from yt-dlp info for *that part*) against the subtitle's **last cue end-time**. Outside ~70%–110% → reject → Whisper.
2. **Identity check (`part>1` in a multi-part video):** fetch **part 1's** subtitle too (extra `skip_download` probe, no media) and compare. **Byte/near-identical to part 1 → the #6357 signature → reject → Whisper.**

Single-part and `part=1` trust the sub (subject to D5), with the duration sanity gate still running as a catch-all. Rejection reason flows into the D2 header.

**Why:** "never silently align the wrong text" is inert without a concrete falsifiable test; "did a sub come back" is not one.

---

## D5 — Quality gate: any single metric trips → Whisper  · Locked (thresholds: Calibrate)
**Touches:** §7, `quality.py`, `config.py`

- **Policy: fail-toward-Whisper.** Any *one* of the four metrics tripping → fall back. Not a weighted score, not "2 of 4."
- **Thresholds live in `config.py`** as named, overridable settings seeded with conservative guesses, **calibrated in build step 3** on one real degraded lecture (same empirical honesty the spec already grants scenedetect). Material-dependent by nature.
- Starting guesses (documented as guesses, not law): punct density < ~0.04/char; dup ratio > ~0.3; non-CJK ratio > ~0.2; chars-per-second outside ~1–8 cps.
- Every metric value + verdict logged into the `quality_gate` block and surfaced per D2.

**Why:** the spec's own cost asymmetry — a subtly-wrong transcript hardens into accepted truth downstream; Whisper is merely slower on a box with a 4090. Bias toward Whisper when in doubt.

---

## D6 — Cache keys = video-identity + stage-param-hash  · Locked
**Touches:** §5, §8, `cache.py`

SPEC.md's `{platform}:{id}:{part}` key is correct only for stages that depend solely on the video. Per-stage:

- **audio, subtitle-probe:** `{platform}:{id}:{part}` only.
- **transcript:** + `{source-decision, force_whisper, robust, whisper_model}`.
- **frames:** + `{scene_threshold, phash_dedup_threshold}`.
- **captions:** + `{frame-set hash, vision_model, prompt_version}`.
- **bundle/merge:** composite of the above.

Implement as `{platform}:{id}:{part}` + a short hash of that stage's determining params. **Never invalidate the whole video's cache when one flag changes** — changing `--scene-threshold` must not force a re-download or re-transcribe.

**Why:** the literal key silently returns a cached `ai-zh` transcript when you pass `--force-whisper` — a correctness bug, not just staleness. "Unchanged *inputs*" (§5) means params too.

---

## D7 — Projector check: fingerprint-armed nonce-OCR probe  · Locked
**Touches:** §7, `vision.py`, `config.py`

Two separable concerns the spec blurs:

- **Transport (one-time plumbing):** does the OpenAI-compatible REST endpoint accept base64 data-URI images, or is the LM Studio SDK path needed? Decide once in **build step 5**, hardcode the working transport.
- **Projector-actually-loaded (runtime, event-driven):** LM Studio is a hand-operated GUI whose state drifts; a missing mmproj makes it return **confident, well-formed, hallucinated** captions while every ping/health check passes.

Mechanism — armed by **state change**, not the clock (single-user local box; the only risk event is "I messed with LM Studio"):

1. At vision-stage start, do a **cheap metadata read** (`GET /v1/models` or SDK listing) and hash it (loaded model id + any load/config identifier) into a fingerprint.
2. If the fingerprint matches the last fingerprint for which the nonce probe **passed** → skip the probe.
3. If it differs → run the **nonce-OCR probe**: render a small PNG with a fresh random nonce (e.g. `K7QF2M`), ask the model to read it, require exact/near match. Re-store fingerprint on success.
4. **On failure: hard-stop the vision stage, loud error** naming the likely cause ("mmproj projector not loaded in LM Studio"). Never degrade to silent caption-less frames.

> Verify-before-pinning (build step 5): exactly what the LM Studio API exposes — `/v1/models` likely can't distinguish "loaded with mmproj" from "loaded text-only," so the fingerprint decides *when* to probe; the nonce is what actually *proves* the projector.

**Why:** the failure mode is silent and well-formed — "did it respond" passes precisely when it's broken. Only an answer the model can't produce blind (the nonce) distinguishes loaded from text-only.

---

## D8 — Delivered bundle is a self-contained directory; PNGs ship for QA  · Locked
**Touches:** §4, §6, §11, `merge.py`, `frames.py`

- Delivered unit = one **self-contained `out/<id>-p<part>/`** dir: `bundle.json` + `bundle.md` + `frames/`. Self-contained so the Atlas copies one folder with no dangling paths.
- **Frame PNGs ship — but for *your* QA/reprocessing, not Atlas consumption.** The Atlas reads only the markdown (text); vision already turned each frame into OCR + caption. PNGs let you eyeball a suspicious caption and re-caption without re-extracting.
- **`cache/`** holds the expensive intermediates (raw audio, full pre-dedup frame set), gitignored. Deduped/captioned frames are copied/hardlinked into the delivery dir.
- Optional `--no-frame-images` lever omits PNGs from `out/` (JSON still records `phash`/`ts`/`caption`). Default = ship them.

**Why:** keeps the bundle portable across the repo seam while the cache stays scratch; the consumer never opens an image, so PNGs are provenance, not payload.

---

## D9 — Auth: cookies-from-browser (Firefox) default, `.env` SESSDATA fallback  · Locked
**Touches:** §7, §11, `config.py`

- **Default: yt-dlp `--cookies-from-browser`, Firefox profile.** Reads the live logged-in cookie store at run time — always fresh, carries the full cookie set (not just SESSDATA), nothing to paste or rotate. Chosen so **re-auth is intuitive** (just log in in the browser). Config carries `{browser, profile}`.
  - Windows caveat: Firefox is the reliable choice; Chromium browsers lock/encrypt (DPAPI) the cookie DB.
- **Fallback: explicit `SESSDATA` via `.env`** (documented by `.env.example`; never a committed cookies.txt) for headless / no-browser cases.

See D11 for what we deliberately do *not* build around staleness.

**Why:** personal single-user box where you're already logged in — manual token management makes *you* the cookie-refresh cron job. Browser cookies remove that.

---

## D10 — Vision caching stays all-or-nothing  · Locked
**Touches:** §5, `cache.py`, `vision.py`

Captions are cached as **one stage blob** keyed per D6 (`frame-set hash, vision_model, prompt_version`). A mid-run crash re-captions the **whole** frame set on retry.

Considered and **rejected:** per-frame phash-keyed caption memo (would give free crash-resume + cross-video slide dedup). Rejected for **implementation simplicity**; accepted cost = wasted wall-clock on the rare mid-stage failure.

**Why:** user chose simplicity over resumability here; the failure is rare and non-corrupting.

---

## D11 — No stale-cookie detector; fail loud only if the cookie source fails  · Locked
**Touches:** §7, §11, `subtitles.py`, `config.py`

- **No login-probe / smell test.** A reliable one needs bilibili member APIs (the WBI/auth surface §3 says to keep out of our code), and the cheap signal ("did `ai-zh` come back?") is ambiguous — a video with no AI captions and a logged-out session look identical.
- **`meta.cookies_used` means "cookies were supplied," not "server honored them"** — record it honestly so provenance doesn't overclaim.
- **The one unambiguous guard:** if cookies-from-browser **fails to read the profile at all** (DB locked, wrong path, zero cookies) → hard, detectable error → **fail loud**, tell the user to check the browser/profile.

**Why:** browser-cookie (D9) already closes the usual staleness cause; a speculative login detector would cry wolf or miss silently. Walks back an over-spec'd guard floated during the grilling.

---

## D12 — Single-part atomic unit; `--all-parts` is an optional isolate-and-continue loop  · Locked
**Touches:** §9, `cli.py`, `resolve.py`

- The **atomic, cached, identity-bearing unit stays `{platform, id, part}` → one bundle dir.** Nothing already locked is disturbed.
- **`--all-parts`** = thin orchestration: resolve part count, loop the single-part pipeline, write N independent bundle dirs.
- **Batch failure = isolate, don't abort:** part 30 fails → log, mark failed, continue to 31. Re-running `--all-parts` skips done parts (D6/D10 caching) and retries only failures. Exit non-zero with a per-part summary if any failed.
- Default (no flag) = the part in the URL, or part 1. No surprise fan-out.

**Why:** multi-part courses are the target content and per-part work is long; fan-out is convenience over the unit, not a rework, and a missing part is a gap to backfill, not a reason to discard good bundles.

---

## D13 — Frame candidates: periodic sampling + phash dedup (overrides SPEC §5 step 4)  · Locked
**Touches:** §2, §5 step 4, §7, `frames.py`, `config.py`

SPEC §5 step 4 specifies **scene-cut detection** (PySceneDetect ContentDetector) as the frame
candidate source. On the first real target (BV1NL9tBsELS) this returned **1 frame for a 46-min,
69-slide lecture**: the content is a **continuous-shot screen-recording** ("一镜到底无剪") where slides
advance via soft transitions, so there are no hard cuts to detect.

**Decision:** candidates come from **periodic sampling** (`ffmpeg fps=1/interval`, default 6s) +
**perceptual dedup** (phash, last-kept comparison). The SPEC's *goal* — "one frame per stable slide"
— is unchanged; only the mechanism differs. It's also faster (one ffmpeg pass vs full-video decode).

- **Calibration (build step 4, this lecture):** within-slide phash hamming <10, slide-change ≥16 →
  `phash_dedup_threshold = 10` (72 kept ≈ 69 slides). Cropping the presenter cam did **not** help
  (the per-slide progress bar/counter is the real perturbation). Numbers are calibrated guesses.
- `scene_threshold` retained in config as a future secondary signal; `_scene_timestamps` removed.
- If a future input is a hard-cut deck, scene cuts can be unioned back in. Talking-head content with
  no slides → `--no-vision` + D3 wall-clock chunking.

**Why:** the spec's mechanism produced a degenerate result on the actual target content; the goal it
served is better met by sample-then-dedup, which the existing phash dedup (the §7 cost lever) already
implements.

---

## Build-time resolutions (settled 2026-06-28, per the deferred list below)
- **faster-whisper:** pinned `1.2.1` (current stable). CUDA on Windows via the `nvidia-cublas/cudnn/
  cuda-runtime-cu12` wheels, registered on PATH (ctranslate2 uses plain `LoadLibrary`).
- **Quality-gate thresholds (D5):** seeded defaults in `config.py` (punct<0.04, dup>0.30,
  nonzh>0.20, cps 1–8) — **not yet recalibrated on a degraded sub** (no sub-bearing test URL yet).
- **Frame thresholds (D3/D13):** `phash_dedup_threshold=10`, `sample_interval_s=6.0`,
  `chunk_window_s=75.0` (D3 wall-clock fallback).
- **D7 transport:** base64 data-URI images over the REST chat API **work**; no SDK fallback needed.
- **Vision model:** `qwen/qwen3.6-27b` + its mmproj (user's choice over a literal Qwen3-VL). It's a
  **reasoning model** → captioning needs generous `max_tokens`. Projector verified via the D7 probe.
- **Downloads:** `aria2c` (multi-connection, IPv4-forced) is preferred for bilibili's throttled CDN;
  native ranged-chunk fallback otherwise.
- **Subtitle source (D4, settled 2026-06-29):** yt-dlp — even the latest (`2026.06.09`) — does **not**
  surface bilibili's AI subtitle tracks. The plain `x/player/v2` endpoint returns them with the same
  browser cookies yt-dlp already reads, with **no WBI signing** (so the §3 surface we avoid stays
  avoided). `player_api.py` is a cookie-authenticated fallback used whenever yt-dlp's subtitle list is
  empty. AI subs carry `ai_type` (0 = original transcription, 1 = translation); only original zh is
  used. **Caveat:** these subs are generated on demand and were *incomplete* on every fetch of the
  test video (coverage 0.12–0.37), so D4 tier-1 (duration sanity) correctly rejected them → Whisper.
  D4 tier-2 (#6357 part-1 identity check) is now wired (`is_part1_duplicate`, similarity ≥ 0.90 on
  concatenated cue text). The subtitle-**accept** branch + D5 thresholds remain unexercised on real
  content (no fully-captioned test video yet).

## Deferred to build-time — RESOLVED above (kept for traceability)
- ~~faster-whisper version~~ → `1.2.1`.
- ~~Quality-gate thresholds (D5), phash dedup, chunk window~~ → set/calibrated; D5 thresholds still
  need a real degraded-sub recalibration. Scene-detect threshold superseded by D13.
- ~~Data-URI vs LM Studio SDK transport (D7)~~ → data-URI.
- ~~LM Studio endpoint URL + Qwen3-VL model name~~ → `http://localhost:1234/v1`, `qwen/qwen3.6-27b`.
