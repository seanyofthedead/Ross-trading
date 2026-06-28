# Handoff — execution build context & rationale

This file carries the *reasoning* behind `plans/waves/` so an implementer (human or a fresh
Claude Code run) starts with the decisions already made, not a blank slate. The wave files
say **what** to build; this says **why**, and what was explicitly rejected.

It is the distilled output of a two-philosophy design review (a "pragmatist" lens —
ship-soon, operable, reversible — and a "purist" lens — correctness/integrity before a
dollar moves) plus a broker debate and a live-data-ingestion analysis.

## Kickoff prompt (paste into Claude Code)

```
Read plans/waves/README.md and plans/waves/HANDOFF.md for context and the loop contract,
then implement the lowest-numbered wave whose dependencies are done, on a new feature branch.
Start with WAVE-00. Work task-by-task, keep mypy --strict / ruff / pytest green, and stop at
the wave's advance_gate for review before moving on.
```

## State of the system (as of this review)

- Phase 2: a live **scanner + journaling** system works. The execution path is **specified
  but unbuilt** — `docs/architecture.md` §3.5–§3.8 and §4 are prose/pseudocode; there is no
  order routing, broker client, position/PnL ledger, risk supervisor, or reconciliation in
  code. `src/ross_trading/safety/` is an empty package.
- The journal (`journal/models.py`) persists scanner decisions only — **no orders/fills/
  positions/PnL tables**.
- Strong correctness discipline already exists and should be reused, not disturbed: the
  `SnapshotAssembler` determinism boundary and D6 replay-parity contract, `Decimal`-
  everywhere, frozen/slotted value objects, and the journal's `BEGIN IMMEDIATE` + WAL +
  FK/CheckConstraint rigor (`journal/engine.py`). **That rigor stops exactly where money
  starts — closing that gap is the whole point of these waves.**
- No market-data **recordings** exist in either repo (`recordings/` is gitignored and empty;
  the research repo's `data/` holds only the YouTube-transcript corpus). This is decision
  #78. Real recordings first appear when the paid feed is wired (Wave 6).

## The core decisions (and why)

### 1. Build order: correctness foundation before features
Both lenses agreed the riskiest, least-retrofittable work is the **write path** (duplicate
sends, lost fills, position drift) and the **ingestion contract** (silent drop/reorder
corrupting the as-of snapshot). So the waves front-load those:

- **Wave 0 (ingestion contract)** comes first because the architecture's entire thesis is
  bit-identical live/replay, yet `Quote`/`Tape`/`Bar` carry **no sequence numbers** and gaps
  are detected **only on socket disconnect** (`data/reconnect.py`). A feed that silently
  drops a message produces no gap sentinel and the assembler picks the wrong as-of value —
  on *any* vendor. Fix the contract (seq numbers, three-timestamp split, seq-discontinuity
  gap detection, typed halts, append-only corrections) before anything trusts the snapshot.
- **Wave 1 (ledger) before Wave 2 (broker)**: lock the order/fill/position model, state
  machine, and `client_order_id` idempotency against a fake first, so the broker adapter is
  built against a correct internal model — not the reverse. This was the purist's "ledger-
  first" point; the pragmatist's "broker-first" instinct is honored by resolving the broker
  choice now (below) so the port is shaped by a real API.
- **Wave 3 (kill switch) early**: it's the blast-radius limiter and currently an empty
  package; everything after it runs under a supervisor that can stop it.
- **Wave 4 (reconciliation)** makes the kill switch *trustworthy* — force-flatten is
  meaningless if the bot's notion of position can silently diverge from the broker's.

**Must-do-before-live:** Waves 0, 1, 3, 4 and the Wave 6 parity gate. The rest (real L2,
the 10-sec micro-pullback, regime calibration) is later hardening.

### 2. Broker: Alpaca to start, with two non-negotiable guardrails
The pragmatist picked Alpaca (fast, commission-free at micro-stakes, native brackets, free
paper, sits behind a swappable port). The purist picked IBKR (authoritative queryable
order/position state). The split **resolved** once the purist surfaced a load-bearing fact:
**Alpaca's order-update WebSocket has no sequence numbers** — Alpaca's own docs say events
can drop/reorder and recommend FIX for guaranteed delivery. So "Alpaca" is only safe with:

1. **Do not trust the JSON WebSocket as position-state truth.** Use FIX for order events, or
   treat the WS as a hint with REST `/orders` + `/positions` authoritative (Wave 4
   reconciliation enforces this).
2. **Pay for full-SIP data from day one.** The scanner's first hard filter is relative-volume
   ≥5×, computed on total market volume. Alpaca's free tier is IEX-only (~2% of volume) and
   venue-biased — wrong for exactly the low-float small-caps this strategy trades. IEX-only
   is **rejected** as the signal source (Wave 6).

The broker sits behind a clean `BrokerClient` port (Wave 2) so the agreed upgrade to **IBKR**
(execution) and/or **Databento** (signal data) is a drop-in when a switch trigger fires.

### 3. Data ingestion shape (Wave 0 + Wave 5/6)
- Hybrid transport: WebSocket streaming for quotes/M1 bars/halts; once-daily REST for D1
  baseline and float.
- Two consumers, one buffer: the ~2s scanner loop reads the **assembled as-of snapshot**
  (unchanged); the **exit monitor subscribes to the quote stream + broker order-event stream
  directly** and reacts on arrival — **never polls** for fill state, never waits for the 2s
  tick to hit a hard stop (Wave 5).
- As-of selection by `(exchange_ts, seq)`, completed bars only (`is_final`), gaps explicit —
  the existing record-forward → replay path makes this provable.

## Switch triggers (the at-scale upgrade path — documented, not yet built)

Move execution to **IBKR** and/or signal data to **Databento** when any fire:

- The live-vs-replay parity test can't be made to pass on Alpaca's unsequenced market-data WS.
- The §3.7 **real L2-weakness exit** gets built (needs MBP-10 depth — until then use the
  spread+size **proxy**).
- Measured live slippage on low-float fills consistently exceeds the simulator.
- **Pre-market** Gap-and-Go entries become core (Alpaca brackets are RTH-only / TIF DAY|GTC).
- Float can't be confirmed refreshing daily pre-07:00 ET → add a float vendor (Polygon
  reference / Benzinga), decision #33 — the one second-vendor likely unavoidable.

## What was explicitly rejected / deferred

- **IEX-only / free data** as the signal source — correctness failure for low-float rel-vol.
- **Alpaca order JSON WebSocket as truth** — no sequence numbers; FIX or REST-reconcile only.
- **Buying Databento now** — nothing consumes L2/tape yet; premature. Keep the port clean and
  add it on a trigger.
- **Building detectors before the execution skeleton** — the repo's next planned atom was the
  `EntrySignal` value object; these waves intentionally resequence the ledger/kill-switch/
  reconciliation ahead of more detector work, since that's where the unguarded money risk is.

## Open decision-issues these waves resolve

#10 broker (Alpaca-with-guardrails, IBKR on trigger) · #11 data feed (paid full SIP) ·
#12 L2 (proxy now, MBP-10 on trigger) · #33 float daily refresh (verify, add vendor if
needed) · #78 recordings (first real recordings appear at Wave 6).

## Provenance

Derived from a design-review session over both repos. The factual file:line citations live
in the individual wave files; `docs/architecture.md` (§3.5–§3.8, §4, D6) and
`.claude/decision-issues.json` are the upstream sources. The reviews themselves were not
committed — this file is their durable residue.
