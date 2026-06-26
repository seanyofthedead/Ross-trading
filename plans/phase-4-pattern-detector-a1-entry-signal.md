# Phase 4 — Pattern Detector A1: `EntrySignal` value object + `pattern_id` taxonomy

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish the uniform output contract for the Pattern Detector (§3.4) **before** any detector logic exists, so the §3.5 position sizer and the journal have a stable value object to consume and every setup maps 1:1 onto a pinned taxonomy. This atom adds *only* the `EntrySignal` frozen value object and the `PatternId` `Literal` — no snapshot widening, no indicators, no detectors. It is the first and dependency-free step of the D6 build order.

**Architecture:** Mirrors `scanner/types.py`’s `ScannerSnapshot`/`ScannerPick` split — value objects first, logic later. A new `patterns/` package holds `patterns/types.py` with `EntrySignal` (frozen, slotted, picklable) and the `PatternId` / `Timeframe` / `TriggerSource` `Literal`s. The `PatternId` literal is the load-bearing taxonomy: it is mirrored into architecture §3.4 (the way `RejectionReasonLit` mirrors `RejectionReason`), and it is the join key against the ground-truth corpus the day that corpus gains a `setup` label (see D6 “Known limitation”). No detector, no registry, no plumbing in this atom — those are A2+.

**Tech Stack:** Python 3.11, `decimal.Decimal`, frozen slotted `dataclasses`, mypy `--strict`, ruff, pytest.

**Issue:** TBD — to be filed under the Pattern Detector epic. Tracks architecture **§3.4** and Resolved-Decisions **D6**. Predecessors: none (this is the contract atom; A2 widens the snapshot, A3 adds indicator primitives, A4 ships the first detector).

**Decisions resolved (all from D6 — restated here as the atom’s contract):**

- **One uniform output, `Optional` for no-fire.** A detector returns `EntrySignal | None`; no-fire is `None`, never a falsy/sentinel signal (the `strength.py` “absence of evidence is not promotion”, `None != False` rule). The sizer/journal consume exactly this type.
- **`pattern_id` is a closed `Literal` of the six §3.4 setups**, not free text. Six values (timeframe is a *separate* field — Micro Pullback 1-min/10-sec and Bull Flag 1-min/5-min differ by `timeframe`, not by `pattern_id`):
  `"gap_and_go_premarket_high"`, `"gap_and_go_bull_flag"`, `"micro_pullback"`, `"bull_flag"`, `"flat_top"`, `"first_candle_new_high"`.
  A seventh setup is a one-line `Literal` edit that mypy + Drift CI surface as a contract change; the doc mirror in §3.4 must move in lockstep (same discipline as `RejectionReasonLit` ↔ `RejectionReason`).
- **Trigger provenance is on the signal.** `trigger_source` distinguishes a quote-`last` cross from a completed-bar-close predicate (the D6 path-independent rule: cross-triggers read the live quote, bar-stat triggers read completed bars). The signal records which it was, so the live/replay parity audit and the journal can both see it.
- **Capacity-cap inputs travel with the signal.** `breakout_bar_volume` (and `timeframe`) are carried so the §6 capacity cap (≤1–2 % of avg 1-min volume, architecture line ~442) is enforceable by the sizer — capacity realism lives nowhere else. `entry_price` + `stop_price` make `stop_distance` and the §3.5 `shares = max_risk / stop_distance` reconstructable.
- **Frozen, slotted, picklable** — same constraints as `ScannerPick` (`frozen=True, slots=True`), so the value is immutable end-to-end and survives the journal/replay round-trip.

---

## Acceptance Criteria

- [ ] `from ross_trading.patterns.types import EntrySignal, PatternId` works; `EntrySignal` is `frozen=True, slots=True` and picklable (round-trips through `pickle.dumps`/`loads` equal).
- [ ] `PatternId` is a `Literal` with exactly the six values above; an exhaustiveness helper (`assert_never`-style, paralleling `loop.py::_lit_to_enum`) over `PatternId` type-checks under mypy `--strict`.
- [ ] Constructing an `EntrySignal` with a `pattern_id` outside the `Literal` fails mypy `--strict` (negative-type test via a `# type: ignore`-free assertion or a typing test).
- [ ] Field set matches D6: `ticker`, `pattern_id`, `timeframe`, `entry_price`, `stop_price`, `trigger_price`, `trigger_source`, `stop_basis`, `breakout_bar_volume`, `bar_ts`, `anchor_ts`, and `indicator_evidence` (immutable; `tuple[tuple[str, Decimal], ...]`).
- [ ] All price fields are `Decimal`, volume is `int`, timestamps are `datetime`; no `float` anywhere.
- [ ] Architecture §3.4 carries the mirrored `pattern_id` `Literal` block, with a note that it must stay in lockstep with `patterns/types.py::PatternId`.
- [ ] mypy `--strict` passes on `src` and `tests`; `ruff check` passes; full pytest passes.

## Files to Add / Change

| Action | Path | Purpose |
|---|---|---|
| Create | `src/ross_trading/patterns/__init__.py`        | New package for the Pattern Detector. |
| Create | `src/ross_trading/patterns/types.py`           | `EntrySignal`, `PatternId`, `Timeframe`, `TriggerSource` literals + exhaustiveness helper. |
| Create | `tests/unit/test_pattern_types.py`             | Immutability, picklability, `Literal` exhaustiveness, field/type assertions. |
| Edit   | `docs/architecture.md` (§3.4)                  | Add the mirrored `pattern_id` `Literal` block + lockstep note. |
| Create | `plans/phase-4-pattern-detector-a1-entry-signal.md` (this file) | Plan record for the atom. |

No changes to `scanner/`, `data/`, the journal, or any migration — out of scope. Detector logic, the registry, snapshot widening, and indicator primitives are A2+ atoms.

## Key Interfaces

```python
# src/ross_trading/patterns/types.py

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

PatternId = Literal[
    "gap_and_go_premarket_high",
    "gap_and_go_bull_flag",
    "micro_pullback",
    "bull_flag",
    "flat_top",
    "first_candle_new_high",
]
Timeframe = Literal["10sec", "1min", "5min"]
TriggerSource = Literal["quote", "bar_close"]


@dataclass(frozen=True, slots=True)
class EntrySignal:
    """The Pattern Detector's single output unit (§3.4).

    Returned as ``EntrySignal | None`` by every detector atom; ``None``
    means "no setup", never a falsy signal. Consumed unchanged by the
    §3.5 position sizer and the journal. Frozen/slotted/picklable so it
    survives the journal + replay round-trip immutably.
    """

    ticker: str
    pattern_id: PatternId
    timeframe: Timeframe
    entry_price: Decimal
    stop_price: Decimal
    trigger_price: Decimal
    trigger_source: TriggerSource
    stop_basis: str               # e.g. "pullback_low", "flag_low", "premarket_low"
    breakout_bar_volume: int      # capacity-cap input (§6)
    bar_ts: datetime              # completed bar's open time (left edge)
    anchor_ts: datetime           # the tick anchor the detector evaluated at
    indicator_evidence: tuple[tuple[str, Decimal], ...] = ()  # ("atr14", ...), ("vwap", ...)
```

## Test Strategy

Pure unit tests, no I/O:

- **Immutability.** Field re-assignment raises `FrozenInstanceError`; `__slots__` blocks new attributes.
- **Picklability / equality.** `pickle.loads(pickle.dumps(sig)) == sig`.
- **`Literal` exhaustiveness.** A `match`/dispatch over `PatternId` with an `assert_never` default type-checks under mypy `--strict` and covers all six arms at runtime.
- **Type discipline.** Construct a valid signal; assert all price fields are `Decimal`, `breakout_bar_volume` is `int`, timestamps are `datetime`.
- **No-fire convention.** A trivial helper/typing test asserting the detector return type is `EntrySignal | None` (documents the `None`-not-falsy rule for A2+).

No integration test — this atom has no runtime behaviour beyond the value object.

## Defects / Open Questions

- **`stop_basis` — `str` or `Literal`?** Left as `str` in A1 because the full basis vocabulary firms up as each detector lands (pullback low, flag low, pre-market low, breakout-candle low). Promote to a `Literal` in a later atom once the set is closed, if Drift visibility on it is wanted.
- **`indicator_evidence` shape.** A `tuple` of `(name, Decimal)` pairs keeps the value frozen/picklable and avoids committing to a per-pattern schema before the detectors exist. Revisit if the journal wants typed evidence per `pattern_id`.
- **Ground-truth join (cross-repo, tracked in D6).** `pattern_id` is pinned now, but `ross-trading-research/pipeline/extract/emit.py` emits no `setup` label yet, so per-pattern precision/recall is unmeasurable until a shared, versioned schema adds one. This atom does not attempt that join; it only ensures the key exists and is closed.

## Conventions

- Value objects only; no logic, no I/O, no module-level mutable state.
- `Decimal` for all price math; `int` for volume; `Optional` is `T | None`.
- Frozen slotted dataclasses, picklable (matches `ScannerPick`).
- Tests use `pytest` only.

## Tasks

- [ ] 1. Create the `patterns/` package (`__init__.py`).
- [ ] 2. Add `patterns/types.py` with `EntrySignal` + `PatternId`/`Timeframe`/`TriggerSource` literals and the `assert_never` exhaustiveness helper.
- [ ] 3. Mirror the `pattern_id` `Literal` block into architecture §3.4 with a lockstep note.
- [ ] 4. Add `tests/unit/test_pattern_types.py` covering immutability, picklability, exhaustiveness, and type discipline.
- [ ] 5. Verify `ruff check src tests` passes.
- [ ] 6. Verify `mypy src tests` passes (strict).
- [ ] 7. Verify full `pytest` passes.
- [ ] 8. Verify CI is green on the feature branch.
