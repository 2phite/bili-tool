# harvest — downstream (Atlas) protocol

The machine-facing contract between the **Atlas** project and **harvest**. Atlas codes against the
shapes here; treat them as a stable API. Design rationale lives in [SPEC.md](SPEC.md) — you should not
need it to update an Atlas skill against this contract.

harvest supersedes `bili-tool`. The contract is **multi-source**: `platform` distinguishes the source.
This is a fresh `1.0` contract, not a bili-tool patch — fields that were bilibili-specific are
generalized (see §Changes-from-bili-tool at the end).

## CLI verbs

```bash
harvest ingest <url> [flags]   # full pipeline -> out/<id>-p<part>/ bundle
harvest probe  <url>           # cheap pre-flight metadata only, no media
```

There is no bare-url form. Supported sources: `bilibili.com`, `youtube.com`.

### `ingest` flags

```
--part N        1-based part index (default: from URL; YouTube is always 1)
--all-parts     loop every part, isolate-and-continue (bilibili multi-part)
--force-whisper skip subtitle reuse, always transcribe
--lang CODE     pin transcription language (default: zh for bilibili, auto-detect for YouTube)
--robust        disable condition_on_previous_text (repetition-loop lectures)
--no-vision     skip frame captioning
--dedup-threshold N   phash hamming distance to collapse near-duplicate frames (default 10)
--out DIR       output root (default ./out)
--no-frame-images     omit PNGs from out/ (JSON still records phash/ts/caption)
```

## `probe` — pre-flight metadata

- Takes **only** a URL — no other flags apply.
- **stdout carries the JSON result and nothing else** (one line, safe to pipe into a parser).
- Diagnostics/errors go to **stderr**. Exit **0** on success, **1** on failure (stderr:
  `error: <message>`, stdout empty).

### `ProbeResult` shape (matches `schema.py::ProbeResult`)

| field | type | nullable? | notes |
|---|---|---|---|
| `schema_version` | string | no | currently `"1.0"` |
| `platform` | string | no | `"bilibili.com"` or `"youtube.com"` |
| `id` | string | no | canonical video id (bilibili `BV…`; YouTube 11-char id) |
| `title` | string | yes | |
| `uploader` | string | yes | uploader/channel display name |
| `uploader_id` | string | yes | stable author id — bilibili member id (as string) or YouTube channel id (`UC…`) |
| `description` | string | yes | video description |
| `duration_s` | integer | yes | total duration in seconds |
| `published_at` | string | yes | ISO 8601 with explicit offset; **per-source tz** (bilibili `+08:00`, YouTube `Z`/UTC) |
| `parts` | integer | no | number of parts (always ≥ 1; YouTube always 1) |
| `part_durations_s` | array of (integer or null) | — | one entry per part, index-aligned to part 1..N; entries may be `null` |

### Example (YouTube)

```json
{
  "schema_version": "1.0",
  "platform": "youtube.com",
  "id": "dQw4w9WgXcQ",
  "title": "Example Talk",
  "uploader": "Example Channel",
  "uploader_id": "UCabc123...",
  "description": "…",
  "duration_s": 2760,
  "published_at": "2024-06-28T16:00:00Z",
  "parts": 1,
  "part_durations_s": [2760]
}
```

### Nulls are normal, not exceptional

`probe` reports best-effort metadata from a single upstream call. Any of `title`, `uploader`,
`uploader_id`, `description`, `duration_s`, `published_at` may be `null` on an otherwise-successful
call. `part_durations_s` is always present and length-aligned to `parts`, but individual entries may
be `null`. **Atlas must tolerate all of these as `null`/missing, not as failures.**

`bilibili.tv` is unsupported by `probe` (deferred): a `.tv` URL exits 1 with
`error: probe is bilibili.com-only; bilibili.tv unsupported (deferred)`. Treat a nonzero exit as "no
probe data for this URL."

## `ingest` — bundle output

Output is `out/<id>-p<part>/` containing `bundle.md`, `bundle.json`, and `frames/`.

- **`bundle.md` is the primary ingestion surface** — Atlas reads this prose. It opens with a
  frontmatter header carrying provenance (platform, id, url, title, uploader, `published_at`,
  `transcript_source` + decision reason, vision model, tool version), then slide-chunked
  transcript + visual notes.
- **`bundle.json` is the precise backing record** — same facts, structured. Mirrors `ProbeResult`'s
  metadata fields plus:

```jsonc
{
  "schema_version": "1.0",
  "platform": "youtube.com",           // or "bilibili.com"
  "id": "…", "part": 1, "url": "…",
  "title": "…", "uploader": "…", "uploader_id": "…", "description": "…",
  "duration_s": 2760, "published_at": "…", "fetched_at": "2026-07-01T12:00:00Z",
  "transcript": {
    "source": "human-sub",             // "human-sub" | "auto-sub" | "whisper"  ← provenance
    "language": "en",                  // language axis, separate from source
    "model": "large-v3",               // whisper model; null when source is a caption
    "robust": false,
    "quality_gate": { … } | null,      // populated only when a caption was gated (bilibili)
    "segments": [ { "start": 0.0, "end": 4.2, "text": "…" } ]
  },
  "frames": [ { "ts": 12.5, "path": "frames/000012_500.png", "phash": "…", "caption": "…", "ocr": "…" } ],
  "meta": { "cookies_used": true, "referer_used": true, "vision_model": "…", "tool_version": "…" }
}
```

### Provenance authority (how Atlas should rank competing transcripts)

`transcript.source` is a **source-authority signal**, not cosmetic. Rank:
**`human-sub` > `whisper` > `auto-sub`.** Rationale: a human/original caption is authoritative; a
clean local Whisper transcript is trustworthy; a machine auto-caption is a coin-flip. `language` is a
separate axis — a `whisper` transcript's language is Whisper's detected (or `--lang`-pinned) language.

> harvest currently produces `auto-sub` only on the bilibili path (when its quality gate passes).
> YouTube produces only `human-sub` or `whisper` (auto-captions are skipped by design).

## Changes from the bili-tool contract (for migrating an existing Atlas skill)

- `platform` gains `"youtube.com"`.
- `uploader_mid` (integer, bilibili-only) is **removed**; use **`uploader_id`** (string, all sources).
- `transcript.source` value `"ai-zh"` is **renamed** to `"auto-sub"`; language moves to
  `transcript.language`.
- `published_at` offset is now **per-source** (was always `+08:00`); read the offset from the value.
- Invocation is `harvest …`, not `bili-tool …`.
