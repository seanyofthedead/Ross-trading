---
wave: 1
title: Execution domain model & ledger
depends_on: [0]
advance_gate: "orders/fills/positions tables + Alembic migration apply cleanly; Order/Fill/Position value objects (frozen, Decimal) exist; an order state machine (intended->sent->acked->partial->filled/canceled/rejected) is enforced; client_order_id is a unique idempotency key; intent is persisted transactionally (BEGIN IMMEDIATE) before any send; unit tests green; mypy --strict + ruff + pytest green on a feature branch."
status: not_started
---

# Wave 1 — Execution domain model & ledger

> **For agentic workers:** implement task-by-task; `- [ ]` checkboxes. Stay in scope; no broker I/O yet (that is Wave 2 — this wave persists *intent* and *state*, validated with an in-memory fake).

**Goal:** Build the write-path correctness foundation **before** any broker call: an order/fill/position ledger with a unique `client_order_id`, a persisted order state machine, and transactional intent-before-send. The current journal models scanner decisions only (`journal/models.py` — picks/watchlist/scanner_decisions); there is no concept of an order, fill, position, or PnL. The hardest correctness problems in a money-mover (duplicate sends, lost fills, position drift) live here, and they must be designed in the way `EntrySignal` was — while changing them is free.

**Why before brokers:** The duplicate-send/lost-fill/drift invariants cannot be retrofitted safely. Locking the ledger and state machine first means the broker adapter (Wave 2) is built against a correct internal model, not the other way round.

## Decisions carried in

- **`client_order_id` is the idempotency key.** Generated and persisted *before* send; a retry/restart re-uses it; the broker dedups on it. Cleaner than relying on broker-assigned ids.
- **Persist intent before the wire.** Write the `intended` row inside the existing `BEGIN IMMEDIATE` discipline (`journal/engine.py`) *then* (in Wave 2) send. A crash between intent and send is recoverable; a send with no persisted intent is not.
- **Explicit state machine.** `intended -> sent -> acked -> partial -> filled | canceled | rejected | expired`. Illegal transitions raise. Partial fills accumulate filled/remaining quantity precisely (the §3.5 sizer and §6 capacity cap depend on exact filled qty).
- **Broker is truth for positions** (enforced in Wave 4); this wave models the *internal* ledger that will be reconciled against it.
- **Decimal/int end-to-end.** Prices `Decimal`, quantities `int`, no `float` (matches `EntrySignal` / `ScannerPick`).

## Scope

In: a new `execution/` package (value objects + state machine + ledger writer), new journal tables + one Alembic migration, journal models/types edits, unit tests.
Out: any network/broker call, the `BrokerClient` protocol (Wave 2), risk checks (Wave 3), reconciliation loop (Wave 4), loop wiring (Wave 5).

## Files to add / change

| Action | Path | Purpose |
|---|---|---|
| Create | `src/ross_trading/execution/__init__.py` | New package. |
| Create | `src/ross_trading/execution/types.py` | `Order`, `Fill`, `Position`, `OrderState` enum + `OrderStateLit` mirror (frozen, slotted, Decimal). |
| Create | `src/ross_trading/execution/state_machine.py` | Pure transition function; illegal transitions raise; partial-fill accumulation. |
| Create | `src/ross_trading/execution/ledger.py` | Transactional writer: persist intent, apply acks/fills/cancels; idempotent on `client_order_id`. |
| Edit | `src/ross_trading/journal/models.py` | `orders`, `fills`, `positions` tables; unique `client_order_id`; FKs; CheckConstraints (mirror existing rigor). |
| Edit | `src/ross_trading/journal/types.py` | Persist/read mappings for the new rows. |
| Create | `src/ross_trading/journal/migrations/versions/<rev>_orders_fills_positions.py` | Alembic migration for the three tables. |
| Create | `tests/unit/test_execution_state_machine.py` | All legal/illegal transitions, partial accumulation. |
| Create | `tests/unit/test_execution_ledger.py` | Idempotency on `client_order_id`, intent-before-send ordering, crash-recovery read-back. |

## Key interfaces (sketch)

```python
# execution/types.py
class OrderState(enum.Enum):
    INTENDED = "intended"; SENT = "sent"; ACKED = "acked"
    PARTIAL = "partial"; FILLED = "filled"
    CANCELED = "canceled"; REJECTED = "rejected"; EXPIRED = "expired"

@dataclass(frozen=True, slots=True)
class Order:
    client_order_id: str          # idempotency key, unique
    ticker: str
    side: Literal["buy", "sell"]
    qty: int
    limit_price: Decimal | None
    stop_price: Decimal | None
    bracket_group_id: str | None  # ties entry+stop+target (OCO) together
    state: OrderState
    created_ts: datetime
    # broker_order_id filled in after ack (Wave 2)

# execution/ledger.py
class Ledger:
    def record_intent(self, order: Order) -> None: ...        # BEGIN IMMEDIATE; unique client_order_id
    def mark_sent(self, client_order_id: str, broker_order_id: str) -> None: ...
    def apply_fill(self, fill: Fill) -> Position: ...          # accumulates; returns new position
    def open_orders(self) -> list[Order]: ...                  # for restart recovery (Wave 4)
    def position(self, ticker: str) -> Position | None: ...
```

## Acceptance criteria

- [ ] `orders`/`fills`/`positions` tables exist; the migration applies and downgrades cleanly; `client_order_id` is `UNIQUE`.
- [ ] `record_intent` is idempotent: a second call with the same `client_order_id` is a no-op/raises, never a duplicate row.
- [ ] State machine enforces legal transitions only; illegal ones raise; partial fills accumulate filled/remaining exactly.
- [ ] Intent is committed before any "sent" mark; a simulated crash after intent leaves a recoverable `intended` order (read back via `open_orders`).
- [ ] All prices `Decimal`, quantities `int`; no `float`; rows tz-aware UTC.
- [ ] mypy `--strict`, ruff, full pytest green; CI green.

## Test strategy

Pure/unit + SQLite-backed ledger tests, no network. Property-style test: any sequence of legal events leaves ledger position == sum of signed fills. Idempotency test: replaying the same intent/fill stream twice yields one set of rows.

## Tasks

- [ ] 1. `execution/types.py` value objects + `OrderState`/`OrderStateLit`.
- [ ] 2. `execution/state_machine.py` pure transitions + partial accumulation.
- [ ] 3. journal tables + Alembic migration (mirror engine.py transactional rigor).
- [ ] 4. `execution/ledger.py` transactional writer, idempotent on `client_order_id`.
- [ ] 5. Unit tests: state machine, ledger idempotency, intent-before-send, recovery read-back.
- [ ] 6. ruff / mypy --strict / pytest green; CI green.

## Claude Code prompt

```
Implement plans/waves/WAVE-01-execution-ledger.md in the ross-trading repo on a new
feature branch. Build the execution write-path foundation BEFORE any broker code: a new
execution/ package with Order/Fill/Position value objects (frozen, slotted, Decimal
prices, int qty), a pure order state machine (intended->sent->acked->partial->
filled/canceled/rejected/expired with illegal transitions raising and exact partial-fill
accumulation), and a transactional Ledger that persists order intent (unique
client_order_id idempotency key) inside the existing BEGIN IMMEDIATE discipline from
journal/engine.py before anything is "sent". Add orders/fills/positions tables to
journal/models.py with FKs and CheckConstraints mirroring the existing scanner-decision
tables, plus one Alembic migration. No network or broker calls in this wave; validate with
SQLite-backed unit tests. Work task-by-task, checking boxes. Done when the advance_gate
holds and CI is green.
```
