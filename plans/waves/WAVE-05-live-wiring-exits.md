---
wave: 5
title: Live signal->order wiring + event-driven exit monitor
depends_on: [2, 3, 4]
advance_gate: "trading_loop wires scanner->pattern->sizer->risk-checks->execution.place_bracket_order; a position-management/exit monitor consumes the broker order-event stream AND the quote stream directly (no polling for fill state); position sizer is Decimal/int end-to-end; full path runs end-to-end against FakeBroker in an integration test; mypy --strict + ruff + pytest green."
status: not_started
---

# Wave 5 — Live signal->order wiring + event-driven exit monitor

> **For agentic workers:** implement task-by-task; `- [ ]` checkboxes. This connects the (now-correct) signal path to the (now-safe) execution path.

**Goal:** Wire the end-to-end real-time path the architecture specifies (`docs/architecture.md` §4): a `trading_loop` that gates on hours + `can_trade()` (Wave 3) then runs scanner -> pattern detector -> position sizer -> risk checks -> `execution.place_bracket_order(...)`, and a `position_management` / exit monitor that flattens on §3.7 triggers. Critically, the exit monitor consumes the broker **order-event stream** (fills/partials, from Wave 2) and the **quote stream directly** for millisecond reaction — it must NOT poll, and must NOT wait for the ~2s scanner tick to learn its fill state or hit a hard stop.

**Why after 2/3/4:** Entries only make sense once they can be placed safely (broker port), supervised (kill switch), and trusted (reconciliation). This wave assembles them.

## Decisions carried in

- **Two consumers, one buffer.** The ~2s scanner loop reads the assembled as-of snapshot (unchanged from today). The exit monitor subscribes to the buffered quote stream + the broker order-event stream and reacts on arrival.
- **Event-driven, not polling.** A fill/partial/stop can occur between polls; consume the order-event stream (FIX-sequenced or WS+reconcile) for state transitions. Poll only as a backstop.
- **Position sizer is Decimal/int end-to-end.** Replace any float `risk_pct` math (`architecture.md` §3.5) so share counts can't drift from the ledger's Decimal columns; `shares = floor(max_risk / stop_distance)`.
- **Every entry passes the supervisor.** `place_bracket_order` is only called when `Verdict.allow_new_entry` and `can_trade()` are both true; the bracket uses the Wave-1 `client_order_id`.
- **Exits respect the bracket.** Server-side stop/target from the bracket are primary; the monitor's discretionary §3.7 exits (jackknife/MACD/volume-dry-up, L2 proxy) issue `flatten`/cancel through the port.

## Scope

In: `execution/sizer.py` (Decimal sizing), `execution/runtime.py` (the two loops), exit-trigger evaluation reusing completed-bar stats from the assembler + live quotes, wiring to ledger/supervisor/reconciler, integration tests on FakeBroker.
Out: detector internals (separate Phase-4 pattern atoms), live (non-paper) capital, the parity CI gate + paid data (Wave 6). Use the L2 *proxy* (spread+size) for the §3.7 L2 exit; real L2/MBP-10 is deferred.

## Files to add / change

| Action | Path | Purpose |
|---|---|---|
| Create | `src/ross_trading/execution/sizer.py` | `position_size(signal, account, risk_pct) -> int` in Decimal/int; §6 capacity cap via `avg_1min_volume`. |
| Create | `src/ross_trading/execution/runtime.py` | `trading_loop()` + `position_management_loop()` (the §4 orchestration). |
| Create | `src/ross_trading/execution/exits.py` | §3.7 exit triggers over completed bars + live quotes; emits flatten/cancel. |
| Edit | `src/ross_trading/execution/ledger.py` | `place_bracket_order` convenience that records intent then calls `broker.submit_bracket`. |
| Edit | `src/ross_trading/scanner/loop.py` | Factor shared snapshot/clock so both loops share one buffer; keep scanner tick intact. |
| Create | `tests/integration/test_trading_path.py` | scanner pick -> signal -> sized -> risk-checked -> bracket placed -> partial fill -> exit, all on FakeBroker. |
| Create | `tests/unit/test_sizer.py` | Decimal sizing + capacity cap; no float. |

## Key interfaces (sketch)

```python
# execution/runtime.py
async def trading_loop(deps) -> None:
    # gate: market hours and supervisor.can_trade()
    # snapshot -> scanner -> detector -> sizer -> supervisor.evaluate
    # if allow_new_entry: ledger.place_bracket_order(BracketRequest(...))

async def position_management_loop(deps) -> None:
    async for ev in merge(broker.order_events(), quote_stream):
        # update ledger on fills/partials; eval §3.7 exits on each quote/bar-close
        # on trigger: broker.flatten(ticker) / cancel siblings
```

## Acceptance criteria

- [ ] `trading_loop` only places an entry when market hours + `can_trade()` + `Verdict.allow_new_entry` all hold; the bracket carries a Wave-1 `client_order_id`.
- [ ] Sizer is Decimal/int end-to-end; capacity cap uses `avg_1min_volume`; no `float`.
- [ ] Exit monitor reacts to fills/partials from the order-event stream and to hard-stop crosses on the live quote — without waiting for the 2s tick and without polling for fill state.
- [ ] A partial fill mid-position is reflected in the ledger before the scale-out/exit logic runs.
- [ ] End-to-end integration test passes on FakeBroker (pick -> bracket -> partial -> exit -> flat), with the supervisor and reconciler in the loop.
- [ ] mypy `--strict`, ruff, pytest green; CI green.

## Test strategy

One integration test drives a scripted FakeBroker + synthetic quote/bar stream through the full path and asserts: correct sizing, supervisor gating, bracket placement, partial-fill ledger update, an exit trigger firing on a quote between ticks, and a flat end state reconciling clean. Unit-test the sizer's Decimal math and capacity cap.

## Tasks

- [ ] 1. `execution/sizer.py` Decimal/int sizing + §6 capacity cap.
- [ ] 2. `execution/exits.py` §3.7 triggers (bar-stat + quote-cross + L2 proxy).
- [ ] 3. `ledger.place_bracket_order` (intent -> submit_bracket).
- [ ] 4. `execution/runtime.py` trading_loop + event-driven position_management_loop (merge order-events + quotes).
- [ ] 5. Share one snapshot/clock buffer between scanner tick and exit monitor.
- [ ] 6. Integration test (full path on FakeBroker) + sizer unit tests.
- [ ] 7. ruff / mypy --strict / pytest green; CI green.

## Claude Code prompt

```
Implement plans/waves/WAVE-05-live-wiring-exits.md in the ross-trading repo on a new
feature branch. Wire the end-to-end real-time path from docs/architecture.md §4: a
trading_loop that gates on market hours and the Wave-3 can_trade()/supervisor verdict, then
runs scanner -> pattern detector -> position sizer -> risk check ->
ledger.place_bracket_order (using the Wave-1 client_order_id and Wave-2 submit_bracket); and
a position_management_loop / exit monitor that consumes the broker order-event stream AND
the live quote stream directly (NO polling for fill state, NO waiting for the ~2s scanner
tick) to update the ledger on fills/partials and fire §3.7 exits via broker.flatten/cancel.
Make the position sizer Decimal/int end-to-end (remove float risk_pct math) with the §6
capacity cap on avg_1min_volume. Use the spread+size L2 proxy for the L2-weakness exit
(real L2 is deferred). Keep the existing scanner tick intact; share one snapshot/clock
buffer. Integration-test the full path on the FakeBroker with the supervisor and reconciler
in the loop. Work task-by-task; done when the advance_gate holds and CI is green.
```
