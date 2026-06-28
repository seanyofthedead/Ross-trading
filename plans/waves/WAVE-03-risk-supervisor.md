---
wave: 3
title: Risk supervisor / kill switch
depends_on: [1]
advance_gate: "safety/ package enforces daily max-loss, 3-consecutive-loser lock, single-position-at-a-time, PDT and T+1 cash-settlement guards; a force-flatten authority exists (manual + automatic) and calls broker.flatten; the loop is gated by a day_locked()/can_trade() check before any entry; unit + integration tests (against FakeBroker) green; mypy --strict + ruff + pytest green."
status: not_started
---

# Wave 3 — Risk supervisor / kill switch

> **For agentic workers:** implement task-by-task; `- [ ]` checkboxes. This is the highest-leverage safety component — the `safety/` package is currently empty.

**Goal:** Build the risk supervisor / kill switch specified in §3.8 and §5 of `docs/architecture.md`, which today is an empty `safety/` package. It is the authority that can refuse new entries and force-flatten everything, and it must exist (with manual force-flatten at minimum) before the system is trusted with real size.

**Why before reconciliation/wiring:** The kill switch is the blast-radius limiter. Building it early — against the Ledger (Wave 1) and `FakeBroker` (available once Wave 2 lands, but the gate only needs Wave 1) — means every later integration runs under a supervisor that can stop it.

## Decisions carried in

- **Pure decision core, side-effecting actuator.** A pure `RiskSupervisor.evaluate(state) -> Verdict` (testable, replay-safe) plus an actuator that performs `broker.flatten` / lock writes. Keeps determinism intact.
- **Hard gates enforced in code, not delegated to broker:** daily max-loss, 3-consecutive-loser lock, single-position-at-a-time, PDT (margin <$25k), T+1 cash-settlement.
- **Force-flatten authority** is the kill switch: a manual trigger (operator) and automatic triggers (limits breached, reconciliation divergence from Wave 4). It calls `broker.flatten` and writes a lock so no new entries pass.
- **Fail-closed.** On unknown/ambiguous state (e.g. can't read positions), the supervisor blocks new entries rather than allowing them.
- **Decimal money math.** PnL/limits in `Decimal`; share/sizing inputs `int`.

## Scope

In: `safety/` package (supervisor core, limits config, actuator, day-lock persistence), a `can_trade()`/`day_locked()` gate consumed by the loop, journal rows for risk events/locks, tests.
Out: the reconciliation loop itself (Wave 4 — but expose the hook the supervisor's automatic flatten will use), full trading-loop wiring (Wave 5). Position sizing lives with execution; the supervisor only *checks*.

## Files to add / change

| Action | Path | Purpose |
|---|---|---|
| Create | `src/ross_trading/safety/__init__.py` | Package init. |
| Create | `src/ross_trading/safety/supervisor.py` | Pure `RiskSupervisor.evaluate(state) -> Verdict`; all hard gates. |
| Create | `src/ross_trading/safety/limits.py` | Typed limits config (max daily loss, loser-streak, PDT/T+1 params). |
| Create | `src/ross_trading/safety/actuator.py` | Force-flatten (manual + auto) via `broker.flatten`; writes day-lock. |
| Create | `src/ross_trading/safety/state.py` | `RiskState` assembled from ledger positions/PnL + account/PDT counters. |
| Edit | `src/ross_trading/journal/models.py` | `risk_events` / `day_locks` tables (audit of every block/flatten/lock). |
| Edit | `src/ross_trading/scanner/loop.py` (or new exec loop placeholder) | Add `can_trade()` gate before any entry path. |
| Create | `tests/unit/test_risk_supervisor.py` | Each gate fires correctly; fail-closed on unknown state. |
| Create | `tests/integration/test_kill_switch.py` | Manual + automatic flatten against FakeBroker; lock blocks subsequent entries. |

## Key interfaces (sketch)

```python
# safety/supervisor.py
@dataclass(frozen=True, slots=True)
class Verdict:
    allow_new_entry: bool
    must_flatten: bool
    reasons: tuple[str, ...]          # audit trail, journaled

class RiskSupervisor:
    def evaluate(self, state: RiskState) -> Verdict: ...   # pure
    # gates: daily_loss <= max; loser_streak < 3; open_positions <= 1;
    #        pdt_ok(account); cash_settled_ok(T+1); else fail-closed

# safety/actuator.py
class KillSwitch:
    def force_flatten(self, reason: str) -> None: ...       # broker.flatten all + write day-lock
    def is_locked(self) -> bool: ...
```

## Acceptance criteria

- [ ] `evaluate` is pure and deterministic; given a `RiskState` it returns the same `Verdict` every time, with human-readable `reasons`.
- [ ] Each hard gate is independently tested: daily max-loss, 3-loser lock, single-position, PDT, T+1 cash settlement.
- [ ] Fail-closed: missing/ambiguous state yields `allow_new_entry=False`.
- [ ] `KillSwitch.force_flatten` (manual and automatic) flattens via `broker.flatten` and persists a day-lock; `can_trade()` returns False afterward.
- [ ] Every block/flatten/lock is journaled to `risk_events`/`day_locks` with its reason.
- [ ] The loop calls `can_trade()` before any entry; a locked day admits no entries.
- [ ] mypy `--strict`, ruff, pytest green; CI green.

## Test strategy

Unit-test the pure supervisor across a matrix of states (each limit at/over threshold, combinations, unknown). Integration-test the kill switch against `FakeBroker`: open a position, trigger manual flatten, assert position closed + day locked + next entry refused. Add an automatic-trigger test simulating a max-loss breach.

## Tasks

- [ ] 1. `safety/limits.py` + `safety/state.py` (RiskState from ledger + account counters).
- [ ] 2. `safety/supervisor.py` pure evaluate() with all hard gates + fail-closed.
- [ ] 3. `safety/actuator.py` KillSwitch force-flatten (manual + auto) + day-lock persistence.
- [ ] 4. `risk_events`/`day_locks` journal tables + migration.
- [ ] 5. `can_trade()` gate in the loop before entries.
- [ ] 6. Unit (supervisor matrix) + integration (kill switch vs FakeBroker) tests.
- [ ] 7. ruff / mypy --strict / pytest green; CI green.

## Claude Code prompt

```
Implement plans/waves/WAVE-03-risk-supervisor.md in the ross-trading repo on a new feature
branch. Build the risk supervisor / kill switch in the currently-empty safety/ package per
docs/architecture.md §3.8 and §5. Provide a PURE RiskSupervisor.evaluate(state)->Verdict
enforcing hard gates in code (daily max-loss, 3-consecutive-loser lock,
single-position-at-a-time, PDT for margin <$25k, T+1 cash settlement), fail-closed on
unknown state, with human-readable reasons. Add a KillSwitch actuator with manual and
automatic force-flatten that calls broker.flatten and writes a persisted day-lock, plus a
can_trade()/day_locked() gate the loop checks before any entry. Journal every
block/flatten/lock to new risk_events/day_locks tables. Money math in Decimal. Unit-test
the supervisor across a state matrix and integration-test the kill switch against the
FakeBroker from Wave 2. Work task-by-task; done when the advance_gate holds and CI is green.
```
