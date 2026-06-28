---
wave: 6
title: Go-live gate — paid SIP data, parity CI, paper soak
depends_on: [0, 5]
advance_gate: "a full-SIP real-time feed is wired behind MarketDataProvider (IEX-only rejected); a bit-identical live-vs-replay decision test runs in CI as a hard merge gate; daily float refresh (<07:00 ET) is verified; a documented micro-stakes paper soak runbook + observability/metrics exist; CI green. NO real capital until this wave's checklist is signed off."
status: not_started
---

# Wave 6 — Go-live gate: paid SIP data, parity CI, paper soak

> **For agentic workers:** implement task-by-task; `- [ ]` checkboxes. This is the gate between "works on paper" and "trusted with real dollars."

**Goal:** Close the operational + data-quality gap before live capital. Wire a full-SIP real-time feed (the scanner's rel-volume signal is meaningless on IEX-only ~2% volume), promote the live-vs-replay parity test (Wave 0) into a hard CI merge gate, verify daily float refresh (decision #33), and produce a micro-stakes paper-soak runbook with observability. This is the realization of the review's "must-do-before-live" set.

**Why last:** It depends on a correct ingestion contract (Wave 0) and a working end-to-end path (Wave 5). It is mostly wiring, verification, and operability — plus the explicit decision gate.

## Decisions carried in

- **Full SIP is mandatory.** Start on Alpaca paid SIP (CTA+UTP, 100% volume) behind the existing `MarketDataProvider` Protocol. IEX-only / free tier is rejected as the signal source for low-float names. Keep the provider seam clean so a later swap to Databento (signal data) / IBKR (execution) is drop-in when a trigger fires.
- **Parity is a merge gate.** The bit-identical live-vs-replay decision test becomes required CI — no merge to the live branch if a feed change breaks reproducibility.
- **Float daily-refresh verified, else add a vendor.** Confirm the chosen feed refreshes float pre-07:00 ET daily; if not, add Polygon reference / Benzinga for float (decision #33) — the one second-vendor likely unavoidable.
- **Observable before live.** Metrics/logs for order lifecycle, fills, positions, PnL, supervisor verdicts, reconcile runs, feed gaps/halts. A human can see what the bot is doing in real time.
- **Switch triggers documented** (the at-scale upgrade path): replay parity unprovable on Alpaca's unsequenced WS; the §3.7 real-L2 exit gets built (needs MBP-10); measured slippage exceeds simulator; pre-market entries become core.

## Scope

In: a paid-SIP provider adapter behind `MarketDataProvider`, the parity-CI workflow, float-refresh verification + (if needed) a float vendor adapter, observability/metrics, a paper-soak runbook + go/no-go checklist.
Out: real-capital trading (that is the operator's sign-off after this gate), the Databento/IBKR adapters themselves (future, trigger-driven — only the seam is kept clean here).

## Files to add / change

| Action | Path | Purpose |
|---|---|---|
| Create | `src/ross_trading/data/providers/alpaca_sip.py` | Full-SIP real-time adapter behind `MarketDataProvider` (quotes/bars + seq/halt from Wave 0). |
| Edit | `src/ross_trading/data/float_reference.py` | Verify/implement daily (<07:00 ET) float refresh; add vendor if Alpaca insufficient. |
| Edit | `.github/workflows/*.yml` | Add `test_live_replay_parity` as a required gate on the live branch. |
| Create | `src/ross_trading/observability/metrics.py` | Order/fill/position/PnL/supervisor/reconcile/feed metrics + structured logs. |
| Create | `docs/runbook-paper-soak.md` | Micro-stakes paper-soak procedure + go/no-go checklist + switch triggers. |
| Create | `tests/integration/test_sip_provider_contract.py` | SIP adapter honors the Wave-0 seq/halt/timestamp contract; volume completeness sanity check. |
| Edit | `docs/architecture.md` | Mark #10/#11/#12/#33 resolutions + the documented switch triggers. |

## Acceptance criteria

- [ ] A full-SIP real-time feed is wired behind `MarketDataProvider` and emits the Wave-0 contract (`seq`, three timestamps, halts); IEX-only is not used as the signal source.
- [ ] The SIP adapter passes the ingestion-contract tests; a volume-completeness check confirms it is not an IEX-sized slice.
- [ ] The live-vs-replay bit-identical decision test is a **required** CI check on the live branch.
- [ ] Float refresh is verified daily before 07:00 ET (or a float vendor is added and verified).
- [ ] Observability exists: order lifecycle, fills, positions, PnL, supervisor verdicts, reconcile runs, feed gaps/halts are all visible in real time.
- [ ] `docs/runbook-paper-soak.md` defines the micro-stakes soak + a go/no-go checklist + the documented switch triggers; the checklist is the explicit pre-live gate.
- [ ] mypy `--strict`, ruff, pytest green; CI green (including the new parity gate).

## Test strategy

Contract-test the SIP adapter against the Wave-0 invariants and a volume sanity check. Verify the parity test fails CI if a feed change breaks reproducibility (deliberately break it once to prove the gate bites, then revert). Dry-run the runbook on the paper account end-to-end and capture the observability output.

## Tasks

- [ ] 1. `alpaca_sip.py` full-SIP adapter behind `MarketDataProvider` honoring the Wave-0 contract.
- [ ] 2. SIP contract test + volume-completeness sanity check.
- [ ] 3. Verify/implement daily float refresh (<07:00 ET); add float vendor if needed (#33).
- [ ] 4. Promote `test_live_replay_parity` to a required CI gate on the live branch.
- [ ] 5. Observability/metrics for the full order/risk/reconcile/feed lifecycle.
- [ ] 6. `docs/runbook-paper-soak.md` + go/no-go checklist + switch triggers; record #10/#11/#12/#33 resolutions in architecture.md.
- [ ] 7. ruff / mypy --strict / pytest green; CI green including the parity gate.

## Claude Code prompt

```
Implement plans/waves/WAVE-06-go-live-gate.md in the ross-trading repo on a new feature
branch. Wire a full-SIP real-time market-data adapter behind the existing
MarketDataProvider Protocol (Alpaca paid SIP to start), honoring the Wave-0 ingestion
contract (seq numbers, three timestamps, typed halts); IEX-only must NOT be the signal
source. Keep the provider seam clean so a later swap to Databento (data) / IBKR (execution)
is drop-in. Verify the float feed refreshes daily before 07:00 ET and add a float vendor if
not (decision #33). Promote the Wave-0 live-vs-replay bit-identical decision test to a
REQUIRED CI gate on the live branch (prove it bites by breaking it once, then revert). Add
observability/metrics covering the order/fill/position/PnL/supervisor/reconcile/feed
lifecycle. Write docs/runbook-paper-soak.md with a micro-stakes paper-soak procedure, an
explicit go/no-go pre-live checklist, and the documented switch triggers. Record the
#10/#11/#12/#33 resolutions in docs/architecture.md. This wave is the gate before real
capital — do not enable live trading. Work task-by-task; done when the advance_gate holds
and CI is green.
```
