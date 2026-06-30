# bili-tool — downstream (Atlas) protocol

This is the self-contained, hermes-facing contract for how the downstream **Atlas** project talks
to `bili-tool`. It covers the `probe` pre-flight call (its exact invocation and JSON shape) and
the `ingest` invocation change. You should not need to read `SPEC.md` or `DECISIONS.md` to update
Atlas's skill against this — but `DECISIONS.md` D14/D15 record the rationale if you want it.

## CLI verbs

As of this version, `bili-tool` requires a verb; the old bare-url form
(`bili-tool <url> [flags]`) is **removed**, not deprecated-but-working:

```bash
bili-tool ingest <url> [flags]   # full pipeline -> out/<id>-p<part>/ bundle
bili-tool probe <url>            # cheap pre-flight metadata only, no media
```

Any Atlas skill/script invoking `bili-tool <url>` directly must be updated to
`bili-tool ingest <url>`.

## `probe` — pre-flight metadata

### Invocation

```bash
bili-tool probe <url>
```

- Takes **only** a URL — no other flags apply to `probe`.
- **stdout carries the JSON result and nothing else** — one line, `json.dumps(...)` of the result
  object below. Safe to pipe directly into a JSON parser.
- Diagnostics and errors go to **stderr**, never stdout.
- **Exit code 0** on success, JSON printed to stdout.
- **Exit code 1** on failure (e.g. `.tv` URL, or the upstream `view` call failing): stdout is
  empty, stderr has a line of the form `error: <message>`.

### `ProbeResult` JSON shape

Field by field, matching `bili_tool/schema.py::ProbeResult` exactly:

| field | type | nullable? | notes |
|---|---|---|---|
| `schema_version` | string | no | currently `"1.1"` |
| `platform` | string | no | always `"bilibili.com"` for a successful `probe` (see `.tv` below) |
| `id` | string | no | the canonical video id (e.g. BV id) |
| `title` | string | yes | |
| `uploader` | string | yes | uploader display name |
| `uploader_mid` | integer | yes | uploader's numeric bilibili member id; **view-only**, null if unavailable |
| `description` | string | yes | video description; **view-only**, null if unavailable |
| `duration_s` | integer | yes | total duration in seconds |
| `parts` | integer | no | number of parts/pages (always >= 1) |
| `part_durations_s` | array of (integer or null) | — | one entry per part, **aligned by index to part 1..N**; individual entries may be `null` |

### Example

```json
{
  "schema_version": "1.1",
  "platform": "bilibili.com",
  "id": "BV1NL9tBsELS",
  "title": "示例课程 第3讲",
  "uploader": "某讲师",
  "uploader_mid": 123456789,
  "description": "课程简介……",
  "duration_s": 2760,
  "parts": 3,
  "part_durations_s": [920, 915, null]
}
```

### Caveat — nulls are normal, not exceptional

`probe` reports best-effort metadata from a single upstream call; do not treat nulls as errors:

- `part_durations_s` is **always present and aligned to `parts`** (same length), but **any
  individual entry may be `null`** if that part's duration wasn't reported upstream.
- `uploader_mid` and `description` may be `null` even on an otherwise-successful `probe` call —
  these two fields come only from the `view` metadata source; if it's unavailable, they're null
  while the rest of the result may still be valid.
- `title`, `uploader`, `duration_s` may also be `null` in degraded cases.

**Atlas must tolerate all of the above as `null`/missing rather than treating them as failures.**

### `.tv` is unsupported by `probe`

`probe` only supports `bilibili.com`. A `bilibili.tv` URL fails immediately (before any network
call) with:

- exit code 1
- stderr: `error: probe is bilibili.com-only; bilibili.tv unsupported (deferred)`
- stdout: empty

There is no plan to special-case `.tv` in `probe` output (e.g. an all-null result) — treat a
nonzero exit as "no probe data available for this URL," and skip directly to deciding whether to
`ingest` it anyway (ingest still works for `.tv`, see below).

## `ingest` — the full pipeline (verb change only)

```bash
bili-tool ingest <url> [--part N] [--all-parts] [--force-whisper] [--robust] [--no-vision]
                       [--dedup-threshold N] [--out DIR] [--no-frame-images]
```

Behavior and flags are unchanged from before this version — only the invocation gained the
required `ingest` verb. Output is still `out/<id>-p<part>/` containing `bundle.md`,
`bundle.json`, and `frames/`.

### New bundle fields (schema 1.1)

`bundle.json` (and the `ProbeResult` above) now additionally carries:

- `uploader_mid` (integer, nullable) — uploader's numeric bilibili member id
- `description` (string, nullable) — video description

Both are additive and nullable relative to schema 1.0 — existing Atlas code that ignores unknown
fields needs no change to keep working; code that wants these two new fields can start reading
them. They are populated from the same `view` metadata source `probe` uses, and are `null` under
the same conditions described above (view unavailable, e.g. on `.tv` ingestion, which still works
end-to-end via the yt-dlp-only fallback path but without these two fields).
