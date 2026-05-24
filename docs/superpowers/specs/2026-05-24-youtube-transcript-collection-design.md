# YouTube Transcript Collection for Ross-Trading Research

**Date:** 2026-05-24
**Author:** Sean Pedersen (with Claude)
**Status:** Spec — ready for implementation planning
**Audience:** Implementer of the `ross-trading-research` sibling repo

---

## 1. Purpose & Scope

Build a one-shot-plus-refresh data pipeline that collects publicly available transcripts from Ross Cameron's YouTube channel (`@DaytradeWarrior`), normalizes them, and produces:

1. A retrieval-augmented-generation (RAG) corpus that the `ross-trading` agent (or analysts) can query for Cameron's stated trading concepts, setups, and risk rules.
2. A periodically-regenerated `RULEBOOK.md` of extracted, citation-backed trading rules.

The dataset is **internal research material only**. It is not redistributed, not used to fine-tune any model, not used to impersonate or claim endorsement by Ross Cameron, and not used to reproduce substantial verbatim passages in any public-facing artifact.

### In scope
- Public videos on the `@DaytradeWarrior` channel.
- Public captions (manual and auto-generated) served by YouTube to anonymous viewers.
- Local Whisper transcription of public videos that have no captions.
- Metadata enrichment via the YouTube Data API v3 (where helpful and within free quota).

### Out of scope (excluded by policy)
- Any method that bypasses authentication, paywalls, DRM, age-restriction gates, or robots/access controls.
- Scraping of paid Warrior Trading course material, members-only videos, or private/unlisted videos.
- Public redistribution of the transcript corpus.
- Fine-tuning a model on Cameron's commentary (use RAG instead — same agent utility, lower legal exposure).

---

## 2. Architecture

### Repo placement
New sibling repository at `C:\Users\peder\Documents\ross-trading-research`. No runtime dependency from `ross-trading` on this repo; the only artifact handoff is a versioned snapshot of `RULEBOOK.md` and/or the RAG index, vendored into `ross-trading/` via a small sync script.

### Stack
- **Language:** Python 3.12 (matches `ross-trading`).
- **Discovery & audio:** `yt-dlp`.
- **Captions:** `youtube-transcript-api` (primary), `yt-dlp --write-sub --write-auto-sub` (secondary fallback).
- **ASR fallback:** `faster-whisper` (CTranslate2), local; `base.en` model by default.
- **Audio extraction:** `ffmpeg` (system binary, invoked by yt-dlp).
- **Storage:** SQLite (stdlib) with the `sqlite-vec` extension for the vector index.
- **HTTP:** `httpx` for the small amount of raw HTTP needed (caption-track listing fallback).
- **CLI:** `typer`.
- **Schema validation:** `pydantic` at boundaries (DB row → object, LLM JSON → object).

### Pipeline stages

```
  discover ──► filter ──► fetch_captions ──┬─► normalize ──► chunk ──► embed
                              ▲             │
                              │             │
                          fetch_audio       │
                              │             │
                          transcribe ───────┘
```

Each stage:
- Is a `typer` subcommand on a single `cli` entrypoint.
- Reads/writes only the SQLite DB and the on-disk `data/` tree.
- Is idempotent — re-running with the same input state produces the same output state.
- Drains a queue (`fetch_jobs`) of work items rather than scanning the world each invocation.

### On-disk layout

```
ross-trading-research/
  pipeline/                            # Python package
    cli.py                             # typer entrypoint
    discover.py
    filter.py
    fetch_captions.py
    fetch_audio.py
    transcribe.py
    normalize.py
    chunk.py
    embed.py
    db.py                              # connection helper + migration runner
    migrations/
      0001_initial_schema.sql
      0002_*.sql
    schemas.py                         # pydantic models
    rules/
      title_filters.py                 # regex include/exclude lists
      ad_read_filters.py
      term_dictionary.py
      topic_hints.py
  data/
    pipeline.db                        # SQLite: metadata + work queue + vec index
    raw_transcripts/{video_id}.json    # source-of-truth caption/Whisper output
    audio_cache/{video_id}.m4a         # gitignored, LRU-evictable
    chunks/{video_id}.jsonl            # post-chunking artifacts
  logs/
    pipeline-YYYY-MM-DD.jsonl          # structured run logs
  pyproject.toml
  README.md
  .gitignore                           # excludes data/audio_cache, data/pipeline.db, logs/
```

**Separation of state and content:** SQLite is the source of truth for state (what's done, what failed, why). The on-disk JSON/JSONL files are the source of truth for content. You can drop and rebuild the DB from disk, or drop and rebuild `chunks/` and re-chunk without touching the network.

---

## 3. Data Schema

All tables in `data/pipeline.db`. Migrations as forward-only SQL under `pipeline/migrations/`. SQLite is opened in WAL mode.

### `channels`

| col | type | notes |
|---|---|---|
| `id` | TEXT PK | YouTube channel id (`UC...` form) |
| `handle` | TEXT UNIQUE | `@DaytradeWarrior` |
| `name` | TEXT | Display name |
| `discovered_at` | TEXT | ISO-8601 UTC |
| `last_discovery_run_at` | TEXT | ISO-8601 UTC |

### `videos`

| col | type | notes |
|---|---|---|
| `id` | TEXT PK | YouTube video id (11-char) |
| `channel_id` | TEXT FK | |
| `title` | TEXT | |
| `description` | TEXT | First 4 KB only |
| `upload_date` | TEXT | ISO date |
| `duration_seconds` | INTEGER | |
| `view_count` | INTEGER | as of last discovery |
| `is_live` | INTEGER | 0/1 — was livestream |
| `is_short` | INTEGER | 0/1 — YouTube Short |
| `availability` | TEXT | `public`, `unlisted`, `private`, `members_only`, `removed`, `restricted` |
| `in_scope` | INTEGER | 0/1 — set by `filter` stage |
| `scope_reason` | TEXT | human-readable reason |
| `discovered_at` | TEXT | first time we saw this video |
| `last_seen_at` | TEXT | most recent discovery pass |
| `metadata_json` | TEXT | raw yt-dlp metadata for re-deriving fields |

Indexes: `(channel_id, in_scope)`, `(availability)`.

### `transcripts`

Multiple transcripts per video allowed (e.g. captions and Whisper for QA), keyed by source.

| col | type | notes |
|---|---|---|
| `id` | INTEGER PK | autoinc |
| `video_id` | TEXT FK | |
| `source` | TEXT | `captions_manual`, `captions_auto`, `whisper:<model>` |
| `language` | TEXT | BCP-47 |
| `is_translated` | INTEGER | 0/1 |
| `word_count` | INTEGER | |
| `duration_covered_seconds` | INTEGER | sanity vs `videos.duration_seconds` |
| `text_path` | TEXT | relative path under `data/raw_transcripts/` |
| `fetched_at` | TEXT | ISO-8601 UTC |
| `tool_version` | TEXT | e.g. `youtube-transcript-api==0.6.2` |

Unique: `(video_id, source, language)`.

### `transcript_segments`

Stored in DB *and* in the on-disk JSONL (DB for indexed lookup, JSONL for portability).

| col | type | notes |
|---|---|---|
| `id` | INTEGER PK | autoinc |
| `transcript_id` | INTEGER FK | |
| `seq` | INTEGER | 0-based ordinal |
| `start_seconds` | REAL | from video start |
| `end_seconds` | REAL | |
| `text` | TEXT | raw segment text |
| `speaker` | TEXT NULL | filled if diarization runs (optional) |

Indexes: `(transcript_id, seq)`, `(transcript_id, start_seconds)`.

### `chunks`

RAG-ready, re-derivable.

| col | type | notes |
|---|---|---|
| `id` | INTEGER PK | autoinc |
| `transcript_id` | INTEGER FK | |
| `video_id` | TEXT FK | denormalized |
| `seq` | INTEGER | ordinal within transcript |
| `start_seconds` | REAL | min segment start in chunk |
| `end_seconds` | REAL | max segment end |
| `token_count` | INTEGER | |
| `text` | TEXT | normalized chunk text |
| `text_hash` | TEXT | sha256 for cross-video dedup |
| `dup_count` | INTEGER | how many other chunks share this hash |
| `near_dup_cluster_id` | INTEGER NULL | set post-embedding for near-dupes |
| `content_type` | TEXT | `recap`, `strategy_explainer`, `live_commentary`, `general` |
| `topic_hints` | TEXT | comma-separated; e.g. `setup:gap_go,risk:max_loss` |
| `mentions_tickers` | TEXT | comma-separated tickers from whitelist |
| `chunk_schema_version` | INTEGER | bumped when chunking logic changes |
| `embedding_id` | INTEGER NULL | FK into vec table |

Indexes: `(video_id)`, `(text_hash)`, `(embedding_id)`, `(content_type)`.

### `vec_chunks` (via `sqlite-vec`)

Single column `embedding` (float32, dim=384 for MiniLM-L6-v2). Joined to `chunks.embedding_id`.

### `fetch_jobs`

The work queue.

| col | type | notes |
|---|---|---|
| `id` | INTEGER PK | autoinc |
| `video_id` | TEXT FK | |
| `stage` | TEXT | `fetch_captions`, `fetch_audio`, `transcribe`, `normalize`, `chunk`, `embed` |
| `status` | TEXT | `pending`, `in_progress`, `succeeded`, `failed`, `skipped` |
| `attempts` | INTEGER | |
| `last_error` | TEXT NULL | classified code + message |
| `error_class` | TEXT NULL | `no_captions`, `rate_limited`, `network`, `forbidden`, `unavailable`, `asr_failure`, `language_unavailable`, `unknown` |
| `next_retry_at` | TEXT NULL | exp backoff; NULL = no auto-retry |
| `worker_id` | TEXT NULL | hostname/pid |
| `started_at` | TEXT NULL | |
| `finished_at` | TEXT NULL | |

- Unique partial index `(video_id, stage)` where `status NOT IN ('failed', 'skipped')` — prevents duplicate live work.
- Index `(status, stage, next_retry_at)` — workers grab the next eligible job in one query.

### `run_log`

Append-only invocation log; mirrors `logs/*.jsonl`.

| col | type | notes |
|---|---|---|
| `id` | INTEGER PK | autoinc |
| `run_id` | TEXT | uuid for the invocation |
| `stage` | TEXT | |
| `started_at` | TEXT | |
| `finished_at` | TEXT | |
| `videos_attempted` | INTEGER | |
| `videos_succeeded` | INTEGER | |
| `videos_failed` | INTEGER | |
| `notes` | TEXT | freeform |

**Migrations:** forward-only SQL under `pipeline/migrations/NNNN_*.sql`, applied by a small custom runner. No downgrade; to walk back, restore from a snapshot of `pipeline.db` (cheap; file is <100 MB at full scale).

---

## 4. Step-by-Step Collection Workflow

```
1.  cli init                              # create DB, run migrations, insert channel row
2.  cli discover                          # yt-dlp --flat-playlist → populate `videos`
3.  cli filter                            # title/duration rules → set in_scope + scope_reason
4.  cli enqueue fetch_captions            # one job per in-scope video without captions transcript
5.  cli run fetch_captions --workers 4    # fast path
6.  cli enqueue fetch_audio               # auto-enqueued from caption failures with error_class=no_captions
7.  cli run fetch_audio   --workers 2     # bandwidth-bound, lower concurrency
8.  cli enqueue transcribe                # auto-chained off successful audio downloads
9.  cli run transcribe    --workers 1     # GPU=1; CPU=N if multi-core and no GPU
10. cli run normalize                     # idempotent cleanup
11. cli run chunk                         # semantic chunking → `chunks` table
12. cli run embed                         # MiniLM → `vec_chunks`
13. cli status                            # human dashboard
```

**Refresh:** re-run from step 2. Idempotent everywhere — only `pending` and retry-eligible `failed` jobs do work.

**Reap:** every `cli run <stage>` invocation first reclaims that stage's `in_progress` jobs older than 30 min back to `pending` (no separate command needed). `cli reap --all` is also exposed for manual use across all stages.

**Quiet hours (default on):** no YouTube-bound requests during 09:30–16:00 ET, Monday–Friday. Whisper and embedding stages are unaffected (they're local). Configurable via `--ignore-quiet-hours`.

---

## 5. Transcript Acquisition Options (Ranked)

Reliability ↘, compliance ↗. Higher options are tried first.

1. **`youtube-transcript-api`** — primary.
   - Direct caption-endpoint client. Handles manual and auto-generated captions, language fallback, translation requests.
   - Rate limit: 1 req/s with jitter. Exponential backoff on 429/5xx (1, 2, 4, 8, 16s) then `failed/rate_limited`.
2. **`yt-dlp --write-sub --write-auto-sub --skip-download`** — secondary caption path.
   - Different code path, loads the watch page; sometimes succeeds when (1) is rate-limited.
3. **`yt-dlp -x -f bestaudio/best` → `faster-whisper`** — `no_captions` fallback.
   - Free, local. `base.en` model default. `small.en` upgrade-path for quality.
   - Drop hallucinated segments with `avg_logprob < -1.0` or `compression_ratio > 2.4`.
4. **Hosted ASR (Deepgram / AssemblyAI)** — manual opt-in per video via `cli transcribe --video-id X --provider deepgram`. Not part of automated runs.
5. **YouTube Data API v3** — metadata enrichment only (view counts, accurate timestamps, topic categories). 10k units/day default quota.

**Excluded by policy:** auth bypass, DRM circumvention, age-gate spoofing, paid-content scraping. Private / members-only / removed videos are marked and skipped — no recovery attempt.

---

## 6. Preprocessing for RAG

Run per video, idempotent, re-runnable from disk (`raw_transcripts/{id}.json`) without re-fetching.

- **Cleaning**
  - Collapse repeated filler ("um um um" → "").
  - Strip `[Music]`, `[Applause]`, `[Laughter]` markers.
  - Drop ad-read blocks via regex against `rules/ad_read_filters.py` (Trade Ideas, Warrior Trading course pitches, …).
  - Fix mid-word splits common in auto-captions (`"micro pull back"` → `"micro pullback"`) via `rules/term_dictionary.py`.
- **Sentence reconstruction**
  - Auto-captions arrive in 1–3 word fragments. Reconstitute sentences using segment timestamps + capitalization + pauses >700ms as boundary signals.
  - Preserve `start_seconds` of the *first* segment in each sentence so citations stay accurate.
- **Timestamp preservation**
  - Every chunk carries `(video_id, start_seconds, end_seconds)`.
  - Deep-link format: `https://youtube.com/watch?v={id}&t={start}s`.
- **Chunking**
  - Target ~500 tokens with 50-token overlap.
  - Never split across a >2s pause boundary.
  - Prefer sentence-end breaks.
  - Smaller chunks (250 tok) for `content_type=recap` (dense factual claims).
  - Larger (700 tok) for `content_type=strategy_explainer` (context matters more).
- **Labeling** (deterministic, cheap)
  - `content_type` via parent-video title regex.
  - `mentions_tickers`: regex hits filtered against a known-symbol whitelist (bootstrapped from `ross_trading.data.universe` if available, else a static Nasdaq+NYSE small-cap snapshot).
  - `topic_hints`: keyword dictionary lookup (`rules/topic_hints.py`).
- **Deduplication**
  - Exact: sha256 of normalized chunk text. Repeated intros, disclaimers, ad reads collapse to one canonical row; others record `dup_count`.
  - Near: embedding cosine similarity >0.95 at index time → `near_dup_cluster_id`. Retrieval can choose to dedupe at query time.

---

## 7. Strategy Extraction Plan

Downstream pass against the indexed corpus. Produces `extracted_rules.json` + `RULEBOOK.md`. Both ship to `ross-trading/` for the agent to consume or for the human to cross-check against the agent's hardcoded rules.

### Output schema

```json
{
  "rule_type": "setup|entry|exit|risk|sizing|market_condition|heuristic",
  "name": "micro pullback long",
  "summary": "single sentence statement",
  "preconditions": ["has news catalyst", "float < 20M"],
  "triggers": ["pullback to rising 9 EMA on low relative volume"],
  "invalidation": ["breaks below 9 EMA on increasing volume"],
  "position_sizing": "1/4 of max position on first entry",
  "risk_per_trade_pct": 0.5,
  "evidence": [
    {"video_id": "abc12345xyz", "start_seconds": 412, "quote": "short verbatim snippet"}
  ],
  "needs_review": false
}
```

### Pipeline

1. **Bucket chunks** by `topic_hints` (cheap filter; e.g. all chunks tagged `setup:gap_go`).
2. **Pick representatives** per bucket — k-means on embeddings, take chunks nearest each centroid. Avoids feeding the LLM thousands of redundant chunks.
3. **Structured extraction** — Claude/GPT JSON-mode call per bucket, 20–50 representative chunks as evidence, fixed schema. Temperature 0.
4. **Merge across buckets** — same rule extracted twice → merge, concatenate evidence, regenerate `summary` from the union.
5. **Validation pass** — for each rule, a second LLM call: "does this rule statement contradict the supplied evidence?" Failing rules get `needs_review: true` and do NOT merge into `RULEBOOK.md` until manually reviewed.

Every claim in `RULEBOOK.md` is backed by `[video_id @ MM:SS]` citations linkable to YouTube. No ungrounded assertions.

---

## 8. Failure Modes & Handling

| Failure | Handling |
|---|---|
| 429 on captions endpoint | Exp backoff, 5 retries, then `failed/rate_limited`. Resumable next run. |
| Captions disabled on a video | `failed/no_captions`. Auto-enqueue `fetch_audio` job. |
| Age-restricted video | `availability='restricted'`. Skip. No auth bypass. |
| Private / members-only / removed | Mark `availability` accordingly. Skip permanently. |
| Only non-English captions | Prefer `en`/`en-US`; else any English variant; else `failed/language_unavailable`. No auto-translate. |
| Whisper hallucinations on silence/music | Drop segments where `avg_logprob < -1.0` or `compression_ratio > 2.4`. |
| yt-dlp download failure (network, geo, format) | 3 retries with backoff. Classify error: `network`, `forbidden`, `format`. |
| Disk full during audio cache | LRU eviction of `audio_cache/` after Whisper succeeds for that video. Default cap 20 GB. |
| SQLite write contention | WAL mode + writer lock on job-state mutations. Readers unaffected. |
| Worker crash mid-job | `cli reap` reclaims `in_progress` jobs older than 30 min back to `pending`. Run at top of every batch. |
| Chunking schema changes after embedding | `chunk_schema_version` column. Re-chunk + re-embed deltas only. |
| Hallucinated tickers in extracted rules | Caught by the evidence-validation LLM pass; flagged `needs_review`, excluded from `RULEBOOK.md`. |
| Video deleted/edited after we indexed it | Next `discover` marks `availability='removed'`. We do NOT delete indexed content (kept for reproducibility) but retrieval filters `WHERE availability='public'`. |
| Channel handle re-points | `channels.id` is the YouTube channel id (not the handle). Handle changes are cosmetic. If the channel id itself changes, that is a new channel — manual decision. |
| Global circuit breaker | If 10+ consecutive `forbidden` or `rate_limited` errors hit in a stage, pause that stage for 15 min. Surface in `run_log`. |

---

## 9. MVP (one weekend)

Hard cuts vs the full design:

- Captions-only. No Whisper, no audio download.
- No `fetch_jobs` queue. Use a `videos.fetch_status` TEXT column instead. Single-process loop.
- No chunking, embedding, or vector index. Write normalized transcripts to `data/raw_transcripts/{video_id}.json` only.
- No CLI subcommands. Single `python -m pipeline.run_mvp` entrypoint.
- No strategy extraction.
- Filtering: hardcoded title regex + 90-min duration cap. No manual review.

Target size: ~300 lines.

Goal: a grep-able corpus that lets you eyeball signal quality before investing in the rest of the pipeline.

---

## 10. Production (the full design)

This spec is implementable either as one plan or — recommended — as two plans: MVP (section 9) first, ship it, validate signal quality on a real corpus, then a second plan for the production layers below. The downstream consumers in `ross-trading` should not begin work until at least the MVP is producing files.

Additions over MVP, in priority order:

1. **`fetch_jobs` queue + resumability.** Highest ROI. Add as soon as MVP runs longer than a coffee break.
2. **Whisper fallback (`faster-whisper`, `base.en`).** Coverage for `no_captions` videos.
3. **Chunking + embedding + `sqlite-vec` index.** RAG-ready.
4. **Strategy extraction pass.** The actual product.
5. **Hosted ASR opt-in.** Rare, manual.
6. **Multi-channel generalization.** Only if needed.

---

## 11. Open Questions & Defaults

I'll proceed with the defaults below unless overridden in the implementation plan.

1. **Embedding model:** `all-MiniLM-L6-v2` (384-dim, free, local). Upgrade to OpenAI `text-embedding-3-small` only if retrieval quality stalls.
2. **Whisper model:** `base.en` (74 MB, fast). Upgrade to `small.en` if word-error rate on rapid-fire passages degrades rule extraction.
3. **Audio cache cap:** 20 GB LRU under `data/audio_cache/`. Gitignored.
4. **License header on extracted artifacts:** `CONFIDENTIAL — internal research artifact, not for redistribution`. Stays out of any public ross-trading docs.
5. **Ticker whitelist source:** bootstrap from `ross_trading.data.universe` if it exposes one; else a static Nasdaq+NYSE small-cap snapshot.
6. **Livestream chat replay:** NO. Noise, not Ross's commentary.
7. **Quiet hours:** pause all YouTube-bound requests 09:30–16:00 ET, Monday–Friday. Off-hours only.
8. **YouTube Data API v3 key:** optional. Pipeline works without it; if provided, used only for metadata enrichment.
9. **Distribution of `RULEBOOK.md`:** vendored into `ross-trading/docs/research/` as a tracked file with the confidentiality header. Not redistributed externally.

---

## 12. Non-Goals

- This pipeline does NOT trade. It does NOT make predictions. It produces text data and a derived rulebook.
- This pipeline does NOT modify the `ross-trading` agent's behavior at runtime. Any consumption by the agent is an explicit, separate change in `ross-trading`.
- This pipeline does NOT validate that Cameron's stated rules are profitable. That is the agent's paper-trading job.

---

## 13. Compliance Posture

- Only public videos and public captions. No auth bypass, no DRM circumvention, no paid-content scraping.
- Polite request cadence (1 req/s + jitter, exp backoff, circuit breaker, quiet hours during US market open).
- The corpus is internal research material; not published, not redistributed, not used to fine-tune a model, not used to impersonate Ross Cameron.
- Extracted artifacts (RAG index, `RULEBOOK.md`) cite source video + timestamp for every claim, enabling audit and removal on request.
- Removal-on-request: if Ross Cameron or his counsel asks, the corpus and derived artifacts are deleted, and the channel is added to a do-not-collect list.
