---
wave: 4
title: Reconciliation (broker truth)
depends_on: [2, 3]
advance_gate: "a reconciliation pass asserts ledger == broker positions/open-orders on a cadence AND on every restart; in-flight orders are recovered on startup; any divergence raises an alarm and triggers the kill switch; integration tests against FakeBroker (including injected divergence + crash-recovery) green; mypy --strict + ruff + pytest green."
status: not_started
---

# Wave 4 — Reconciliation (broker truth)

> **For agentic workers:** implement task-by-task; `- [ ]` checkboxes. This is what makes the kill switch and force-flatten *trustworthy*.

**Goal:** Make broker state authoritative. Add a reconciliation pass that compares the internal Ledger (Wave 1) against broker truth (`get_positions`/`get_open_orders` from Wave 2) on a fixed cadence and on every process restart, recovers in-flight orders, and on any divergence raises an alarm and trips the kill switch (Wave 3). Without this, "force-flatten authority" is meaningless because the bot's notion of position can silently diverge from the broker's — the worst failure class for a money-mover.

**Why now:** It depends on both a broker port (truth source) and the kill switch (the action on divergence). It is the last must-do-before-live correctness brick.

## Decisions carried in

- **Broker is the source of truth** for positions and open orders; the Ledger is reconciled *to* it, never the reverse.
- **Reconcile on cadence and on restart.** On startup, rebuild in-flight order state from the broker before the loop is allowed to trade (gate via Wave 3 `can_trade()` staying False until first successful reconcile).
- **Divergence is fail-closed.** Any mismatch (unknown position, qty mismatch, orphan order) → alarm + `KillSwitch.force_flatten` + day-lock pending operator review.
- **Idempotent recovery** keyed on `client_order_id`: re-adopt broker orders into the ledger without creating duplicates.
- **Auditable.** Every reconcile run and every divergence is journaled.

## Scope

In: `execution/reconcile.py` (the pass + startup recovery), wiring into startup + a periodic scheduler, journal rows for reconcile runs/divergences, alarm hook to the kill switch, tests.
Out: the trading loop body itself (Wave 5 consumes `can_trade()` which now also depends on a clean reconcile), new vendors.

## Files to add / change

| Action | Path | Purpose |
|---|---|---|
| Create | `src/ross_trading/execution/reconcile.py` | `Reconciler.run()` (cadence) + `Reconciler.recover_on_startup()`; diff ledger vs broker; emit divergences. |
| Edit | `src/ross_trading/execution/ledger.py` | `adopt(broker_order)` idempotent on `client_order_id`; `snapshot()` for diffing. |
| Edit | `src/ross_trading/safety/actuator.py` | Accept reconciliation divergence as an automatic force-flatten trigger. |
| Edit | `src/ross_trading/journal/models.py` | `reconcile_runs` / `reconcile_divergences` tables. |
| Edit | `src/ross_trading/core/clock.py` (or scheduler) | Cadence hook for periodic reconcile (injectable `Clock`, replay-safe). |
| Create | `tests/integration/test_reconciliation.py` | Clean reconcile; injected divergence → flatten+lock; crash mid-flight → startup recovery; idempotent adopt. |

## Key interfaces (sketch)

```python
# execution/reconcile.py
@dataclass(frozen=True, slots=True)
class Divergence:
    kind: Literal["missing_position", "qty_mismatch", "orphan_broker_order",
                  "orphan_ledger_order", "state_mismatch"]
    ticker: str
    detail: str

class Reconciler:
    def recover_on_startup(self) -> list[Divergence]: ...   # adopt broker truth before trading
    def run(self) -> list[Divergence]: ...                  # cadence; [] == clean
    # on any Divergence: journal + KillSwitch.force_flatten("reconcile:<kind>")
```

## Acceptance criteria

- [ ] A clean state reconciles to `[]` (no divergence) on cadence and at startup.
- [ ] Startup recovery adopts broker open orders/positions into the ledger idempotently (no dup rows) and only then allows `can_trade()` to become True.
- [ ] Injected divergence (qty mismatch, orphan order, missing position) → journaled `Divergence` + automatic `force_flatten` + day-lock.
- [ ] A simulated crash between "sent" and "ack" is resolved on next startup by reading broker truth.
- [ ] Every reconcile run + divergence is journaled (`reconcile_runs`/`reconcile_divergences`).
- [ ] mypy `--strict`, ruff, pytest green; CI green.

## Test strategy

Integration against `FakeBroker`: (1) ledger and broker agree → clean; (2) script broker to report an extra/missing position → expect flatten+lock; (3) write an `intended`/`sent` order, drop the ack, restart → expect recovery adopts the broker order; (4) run `adopt` twice → one row. Assert `can_trade()` is gated on a successful startup reconcile.

## Tasks

- [ ] 1. `Ledger.snapshot()` + idempotent `adopt(broker_order)`.
- [ ] 2. `Reconciler.recover_on_startup()` adopting broker truth; gate `can_trade()` on it.
- [ ] 3. `Reconciler.run()` cadence diff via injectable Clock.
- [ ] 4. Divergence → journal + automatic kill-switch flatten (extend actuator triggers).
- [ ] 5. `reconcile_runs`/`reconcile_divergences` tables + migration.
- [ ] 6. Integration tests (clean / divergence / crash-recovery / idempotent adopt).
- [ ] 7. ruff / mypy --strict / pytest green; CI green.

## Claude Code prompt

```
Implement plans/waves/WAVE-04-reconciliation.md in the ross-trading repo on a new feature
branch. Add a Reconciler that treats the broker as the source of truth: on every restart it
recovers in-flight orders/positions from get_open_orders/get_positions and adopts them into
the Wave-1 Ledger idempotently (keyed on client_order_id) before can_trade() is allowed to
return True; on a fixed injectable-Clock cadence it diffs ledger vs broker and, on ANY
divergence (qty mismatch, orphan order, missing/extra position), journals the divergence and
triggers the Wave-3 KillSwitch force-flatten plus a day-lock (fail-closed). Add
reconcile_runs/reconcile_divergences tables. Integration-test against the FakeBroker:
clean reconcile, injected divergence, crash-between-sent-and-ack recovery, and idempotent
adopt. Work task-by-task; done when the advance_gate holds and CI is green.
```
