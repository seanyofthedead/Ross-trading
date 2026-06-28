---
wave: 0
title: Ingestion contract correctness
depends_on: []
advance_gate: "Quote/Tape/Bar carry (exchange_ts, vendor_ts, ingest_ts, seq); assembler + replay order/dedup on (ts, seq); gap detection fires on sequence discontinuity (not just socket disconnect); typed Halt + correction/bust events exist; a live-vs-replay bit-identical decision test passes; codec is SCHEMA_VERSION 2 with v1 still supported and a v1 fixture decoding/replaying under the v2 build; an idempotent recordings v1->v2 upgrade script exists; mypy --strict + ruff + pytest green on a feature branch."
status: in_progress
---

# Wave 0 — Ingestion contract correctness

> **For agentic workers:** implement task-by-task; steps use `- [ ]` checkboxes. READ-of-review → BUILD. Stay inside the file scope below; do not start broker/execution work (that is Wave 1+).

**Goal:** Make the live-data ingestion path *provably reproducible* before any real dollar depends on it. The `SnapshotAssembler` is already the single determinism boundary (`docs/architecture.md` D6, `scanner/assembler.py`), but it can only be as reproducible as its inputs — and today the inputs are leaky: `Quote`/`Tape`/`Bar` carry **no sequence number** (`data/types.py`), gaps are detected **only on socket disconnect** (`data/reconnect.py`), halts are indistinguishable from gaps, and there is no path for trade corrections/busts. This wave closes those four holes so live/replay is bit-identical by construction on any vendor.

**Why first:** Every later wave (orders, exits, reconciliation, the go-live parity gate) trusts that the snapshot a decision was made on is faithfully recorded and replayable. If a feed can silently drop or reorder ticks and the system can't tell, that trust is unfounded regardless of which broker or data vendor is chosen.

## Decisions carried in (from the review)

- **Order by `(ts, seq)`, dedup on the scoped `(ticker, channel, seq)` key.** Sorting on `ts` alone is non-deterministic on ties and cannot detect dupes/reorders; `seq` is only monotonic per `(ticker, channel)`, so the dedup key must carry that scope. The single highest-value change.
- **Three timestamps, not one.** Persist `exchange_ts` (participant/exchange), `vendor_ts` (vendor send) and `ingest_ts` (local receipt). All *as-of* logic keys off `exchange_ts`; staleness/watermark keys off `ingest_ts`. The codec already records two (`data/_codec.py` `ts`/`ts_recorded`) — formalize three.
- **Gaps from sequence discontinuity, not socket lifecycle.** `ReconnectingProvider` currently emits `FeedGap` only on `FeedDisconnected` (`data/reconnect.py`). A silent drop must still surface a `FeedGap` via per-channel seq discontinuity.
- **Halts are a typed event.** Add a `Halt` event distinct from `FeedGap`; trading the resume off a stale pre-halt `last` is a real-money error.
- **Corrections/busts are first-class, append-only.** A late print or busted trade must adjust recorded volume via an append-only delta with audit trail — never an in-place overwrite — so rel-vol can't be silently corrupted.
- **Recording back-compat is mandatory, not optional.** Changing `Quote`/`Tape`/`Bar` changes the on-disk wire format (`data/_codec.py`, `SCHEMA_VERSION = 1`). Bump to `SCHEMA_VERSION = 2`, keep `1` in `SUPPORTED_SCHEMA_VERSIONS`, and keep every v1 decoder forever — the codec's own docstring already commits to "decoders must dispatch on the version field and accept old payloads forever." A v1 payload (no `seq`/three-ts/halt/correction) must decode by synthesizing deterministic defaults (`seq` derived from file order, `exchange_ts = vendor_ts = ts`, `ingest_ts = ts_recorded`), so old recordings still replay bit-identically to how they replayed under v1. `recordings/` is gitignored and currently empty (decision #78), so there may be nothing on disk yet — but the forward-decodability contract must hold regardless, and an offline upgrade script is provided for any recordings that do exist.

## Scope

In: `data/types.py`, `data/_codec.py`, `data/recorder.py`, `data/reconnect.py`, `data/market_feed.py` (protocol), `data/providers/replay.py`, `scanner/assembler.py`, `scanner/replay.py`, `scanner/loop.py` (staleness), one new migration only if recordings schema is versioned on disk, tests.
Out: brokers, orders, execution, risk, reconciliation (Waves 1+). No new data vendor integration here — this is contract + plumbing, validated against existing/replay + fakes.

## Files to add / change

| Action | Path | Purpose |
|---|---|---|
| Edit | `src/ross_trading/data/types.py` | Add `seq: int`, `exchange_ts`/`vendor_ts`/`ingest_ts` to `Quote`/`Tape`/`Bar`; add `Halt` event; add `Correction`/`Bust` event types. |
| Edit | `src/ross_trading/data/_codec.py` | Bump to `SCHEMA_VERSION = 2`; add `2` to `SUPPORTED_SCHEMA_VERSIONS` (keep `1`); add `EventType.HALT`/`CORRECTION`; **keep v1 decoders** and version-dispatch so v1 payloads upgrade with synthesized defaults. |
| Create | `scripts/upgrade_recordings_v1_to_v2.py` | Offline, idempotent batch upgrade of any existing `recordings/**.jsonl.gz` from v1→v2 (synthesize `seq`/three-ts; non-destructive, writes alongside or to a versioned dir; re-runnable). |
| Edit | `src/ross_trading/data/recorder.py` | Persist new fields/events; corrections as append-only deltas with audit fields. |
| Edit | `src/ross_trading/data/reconnect.py` | Per-channel seq tracking; emit `FeedGap` on seq discontinuity; emit/propagate `Halt`. |
| Edit | `src/ross_trading/data/market_feed.py` | Extend `MarketDataProvider` protocol with halt subscription + seq contract docs. |
| Edit | `src/ross_trading/scanner/assembler.py` | As-of selection orders by `(exchange_ts, seq)`, dedups on `(ticker, channel, seq)`; refuse to bridge gap/halt sentinels. |
| Edit | `src/ross_trading/scanner/replay.py` | `_last_at_or_before` keys on `(exchange_ts, seq)`; reproduce halts/corrections deterministically. |
| Edit | `src/ross_trading/data/providers/replay.py` | Replay emits new fields/events in recorded order. |
| Edit | `src/ross_trading/scanner/loop.py` | Staleness uses `ingest_ts`; distinguish stale-feed vs halt vs gap. |
| Create | `tests/integration/test_live_replay_parity.py` | Bit-identical decisions from a synthetic out-of-order/dropped/halted/corrected stream vs its replay. |
| Create | `tests/unit/test_ingestion_contract.py` | seq dedup/order, three-ts, gap-on-discontinuity, halt typing, correction deltas. |
| Create | `tests/unit/test_codec_backcompat.py` | A captured v1 fixture line decodes under the v2 build; round-trip + synthesized-default assertions; v2 encodes/decodes; unsupported version still raises. |

## Key interfaces (sketch)

```python
# data/types.py
@dataclass(frozen=True, slots=True)
class Quote:
    ticker: str
    bid: Decimal
    ask: Decimal
    seq: int            # per-(ticker,channel) monotonic vendor sequence
    exchange_ts: datetime
    vendor_ts: datetime
    ingest_ts: datetime
    # ... existing fields

@dataclass(frozen=True, slots=True)
class Halt:
    ticker: str
    state: Literal["halted", "resumed"]
    reason_code: str | None
    seq: int
    exchange_ts: datetime
    ingest_ts: datetime

@dataclass(frozen=True, slots=True)
class Correction:                # also covers busts (qty/price -> 0 == bust)
    ticker: str
    corrects_seq: int            # the original print being amended
    new_size: int | None
    new_price: Decimal | None
    seq: int
    exchange_ts: datetime
    ingest_ts: datetime
```

## Acceptance criteria

- [ ] `Quote`/`Tape`/`Bar` carry `seq` + the three timestamps; all `Decimal` prices, `int` volume, tz-aware UTC datetimes; no `float`.
- [ ] Assembler and replay order as-of by `(exchange_ts, seq)` and dedup on a scoped `(ticker, channel, seq)` key — `seq` is only monotonic per `(ticker, channel)`, so de-duping on bare `seq` would discard valid events when different symbols/channels reuse the same number; identical inputs in any arrival order produce identical snapshots.
- [ ] A dropped seq (e.g. 1,2,4) surfaces a `FeedGap` for that channel even with the socket up; detectors refuse to bridge it.
- [ ] `Halt`/resume is a typed event, distinct from `FeedGap`; a resume does not fire entries off a pre-halt stale `last`.
- [ ] A `Correction`/bust adjusts recorded volume via append-only delta; original + corrected both visible in the recording; rel-vol reflects the correction deterministically in replay.
- [ ] `tests/integration/test_live_replay_parity.py` proves bit-identical decisions live-vs-replay over an adversarial stream (reorder + drop + halt + bust).
- [ ] **Back-compat:** `SCHEMA_VERSION == 2`, `SUPPORTED_SCHEMA_VERSIONS ⊇ {1, 2}`; a captured v1 recording line decodes under the v2 build via synthesized defaults (`seq` from file order, `exchange_ts = vendor_ts = ts`, `ingest_ts = ts_recorded`) and replays identically to its v1 behavior; an unsupported/future version still raises.
- [ ] **Upgrade script:** `scripts/upgrade_recordings_v1_to_v2.py` is idempotent and non-destructive (re-running is a no-op; originals recoverable); a v1 fixture upgraded by the script decodes as native v2.
- [ ] mypy `--strict`, ruff, full pytest green; CI green on the branch.

## Test strategy

Drive the assembler with a hand-built event stream that is (a) shuffled in arrival order, (b) missing a seq, (c) interrupted by a halt/resume, (d) hit by a busted print. Record it, replay it, assert the decision journal is byte-identical. Unit-test each invariant in isolation.

## Tasks

- [x] 1. Extend `data/types.py` value objects (`seq`, three ts) + add `Halt`, `Correction`.
- [x] 2. Update `_codec.py` + `recorder.py` (encode/decode new fields/events, bump to `SCHEMA_VERSION=2`, append-only corrections).
- [x] 2a. **Back-compat:** keep all v1 decoders, version-dispatch in `decode_envelope`, synthesize defaults for v1 payloads; add `tests/unit/test_codec_backcompat.py` with a captured v1 fixture.
- [x] 2b. **Migration:** write idempotent `scripts/upgrade_recordings_v1_to_v2.py` for any on-disk recordings; document it (a one-liner in `README.md`/`docs/` on when to run it).
- [x] 3. Add per-channel seq tracking + discontinuity gap detection + halt propagation in `reconnect.py`.
- [x] 4. Make assembler + replay order on `(exchange_ts, seq)` and dedup on `(ticker, channel, seq)`, honoring halt/correction sentinels.
- [x] 5. Point staleness at `ingest_ts`; separate stale/halt/gap handling in `loop.py`.
- [x] 6. Write the parity integration test + unit contract tests.
- [x] 7. ruff / mypy --strict / pytest green locally; CI green on feature branch pending push/review.

> **Reviewer note (advance gate).** All seven tasks are implemented and the local gate is green
> (`ruff`, `mypy --strict`, full `pytest` incl. the new `tests/integration/test_live_replay_parity.py`
> and `tests/unit/test_codec_backcompat.py` / `test_ingestion_contract.py`; `alembic upgrade head` clean).
> One scoping decision left for review: the journal **decision schema** was *not* extended with a new
> `halt` `DecisionKind` — that is journal/ledger surface (Wave 1+, and `safety/`/orders are explicitly
> out of scope here). Halts are handled at the data/assembler layer (typed event, halted-symbol
> suppression, no pricing off a pre-halt quote on resume) and staleness now keys on `ingest_ts`. Flip
> `status: done` once CI is green on the branch.

## Claude Code prompt

```
Implement plans/waves/WAVE-00-ingestion-contract.md in the ross-trading repo, on a new
feature branch. This is the ingestion-correctness wave: add per-message sequence numbers
and a three-way timestamp split to Quote/Tape/Bar; make the SnapshotAssembler and the
replay path order strictly on (exchange_ts, seq) and dedup on the scoped
(ticker, channel, seq) key; detect feed gaps from
sequence discontinuity rather than only on socket disconnect; add a typed Halt event and
an append-only Correction/bust path. Follow the repo's existing conventions (frozen
slotted dataclasses, Decimal prices, tz-aware UTC, mypy --strict, ruff, pytest), mirroring
the style in scanner/types.py and data/types.py.

These field changes alter the on-disk recording format, so preserve back-compat: in
data/_codec.py bump SCHEMA_VERSION to 2, keep 1 in SUPPORTED_SCHEMA_VERSIONS, keep every v1
decoder, and version-dispatch so v1 payloads upgrade with deterministic synthesized defaults
(seq from file order; exchange_ts = vendor_ts = ts; ingest_ts = ts_recorded) and still
replay bit-identically to their v1 behavior. Add tests/unit/test_codec_backcompat.py with a
captured v1 fixture, and write an idempotent, non-destructive
scripts/upgrade_recordings_v1_to_v2.py for any existing recordings (recordings/ is gitignored
and may be empty, but the forward-decodability contract must still hold).

Work task-by-task through the Tasks list, checking each box as you go. Do not start any
broker/order/execution work. Done when the advance_gate in the frontmatter holds: the new
live-vs-replay parity test passes, v1 recordings still decode/replay under the v2 build, and
CI is green. Keep docs/architecture.md D6 in lockstep if you change the determinism contract.
```
