---
wave: 2
title: Broker port + FakeBroker + Alpaca paper adapter
depends_on: [1]
advance_gate: "BrokerClient Protocol defined; FakeBroker (deterministic) and AlpacaPaperBroker implemented behind it; idempotent submit_bracket(client_order_id,...) maps the architecture §3.6 atomic OCO bracket; order events consumed via a delivery-guaranteed path (FIX or REST-reconciled, NOT bare JSON WS as source of truth); a shared broker contract test suite passes against both FakeBroker and AlpacaPaperBroker (paper); mypy --strict + ruff + pytest green."
status: not_started
---

# Wave 2 — Broker port + FakeBroker + Alpaca paper adapter

> **For agentic workers:** implement task-by-task; `- [ ]` checkboxes. The Ledger (Wave 1) is the source of internal truth; this wave gives it a wire.

**Goal:** Introduce one broker-agnostic seam and two implementations, so the same code path drives a deterministic fake (tests/replay) and a real paper account. Mirror the repo's existing Protocol pattern (`data/market_feed.py::MarketDataProvider`, `scanner/decisions.py::DecisionSink`). The single method that matters is an idempotent `submit_bracket(client_order_id, ...)` that realizes the §3.6 atomic OCO bracket (marketable-limit entry, hard stop, 2:1 limit target, ~50% scale-out).

**Why this shape:** A clean port means the start-here broker (Alpaca) and the agreed at-scale upgrade (IBKR) are drop-in swaps later. It also keeps live and replay parity: `FakeBroker` makes execution deterministically testable without a network.

## Decisions carried in

- **Start on Alpaca paper.** Native bracket/OCO, free paper endpoint mirroring live, REST `/orders`+`/positions` for reconciliation, commission-free at micro-stakes.
- **Do NOT trust Alpaca's order JSON WebSocket as truth.** It has no sequence numbers (events drop/reorder, per Alpaca's own docs). Consume order updates via **FIX** *or* treat the WS as a hint and make REST `/orders`+`/positions` authoritative (reconciliation lands in Wave 4). The port must allow either transport behind the same interface.
- **`client_order_id` flows through unchanged** from the Ledger as the broker-side idempotency key.
- **Bracket caveats are explicit.** Alpaca brackets are RTH-only / TIF DAY|GTC; pre-market Gap-and-Go entries need manual leg management — document this on the adapter and surface it as a capability flag.
- **Broker is swappable.** Nothing above the port may import an Alpaca symbol; IBKR/others implement the same Protocol later.

## Scope

In: `execution/broker.py` (Protocol + shared value types), `execution/brokers/fake.py`, `execution/brokers/alpaca.py`, a reusable broker **contract test suite** parametrized over implementations, config for keys/endpoints.
Out: risk checks (Wave 3), the reconciliation loop (Wave 4 — this wave only exposes the query methods it will use), loop wiring (Wave 5), live (non-paper) trading.

## Files to add / change

| Action | Path | Purpose |
|---|---|---|
| Create | `src/ross_trading/execution/broker.py` | `BrokerClient` Protocol + request/result value objects + `BrokerCapabilities`. |
| Create | `src/ross_trading/execution/brokers/__init__.py` | Adapter subpackage. |
| Create | `src/ross_trading/execution/brokers/fake.py` | Deterministic in-memory broker: configurable fills/partials/rejects/latency for tests. |
| Create | `src/ross_trading/execution/brokers/alpaca.py` | Alpaca paper adapter: submit_bracket, cancel, flatten, query orders/positions; FIX-or-REST order events. |
| Edit | `src/ross_trading/execution/ledger.py` | Hook broker acks/fills into ledger transitions (`mark_sent`, `apply_fill`). |
| Create | `tests/contract/test_broker_contract.py` | One suite asserting both adapters honor the Protocol semantics. |
| Create | `tests/unit/test_fake_broker.py` | Fake's scripted scenarios (partial, reject, dup submit). |
| Edit | `pyproject.toml` | Add the chosen Alpaca SDK + (if FIX) a FIX engine dep. |

## Key interfaces (sketch)

```python
# execution/broker.py
class BrokerClient(Protocol):
    capabilities: BrokerCapabilities          # supports_native_bracket, rth_only_bracket, order_event_transport
    def submit_bracket(self, req: BracketRequest) -> BrokerAck: ...   # idempotent on req.client_order_id
    def cancel(self, client_order_id: str) -> None: ...
    def flatten(self, ticker: str) -> BrokerAck: ...                  # market exit, used by kill switch (Wave 3)
    def order_events(self) -> AsyncIterator[OrderEvent]: ...          # FIX seq#'d, or WS hint
    def get_open_orders(self) -> list[BrokerOrder]: ...               # reconciliation truth (Wave 4)
    def get_positions(self) -> list[BrokerPosition]: ...              # reconciliation truth (Wave 4)

@dataclass(frozen=True, slots=True)
class BracketRequest:
    client_order_id: str
    ticker: str
    qty: int
    entry_limit: Decimal      # marketable-limit
    stop_price: Decimal
    target_price: Decimal     # 2:1
    scale_out_qty: int        # ~50%
    tif: Literal["day", "gtc"]
```

## Acceptance criteria

- [ ] `BrokerClient` Protocol defined; nothing above the port imports a vendor symbol.
- [ ] `submit_bracket` is idempotent on `client_order_id`: a duplicate submit returns the same ack, never two broker orders.
- [ ] `FakeBroker` can script: full fill, partial-then-fill, reject, cancel, and out-of-order/dropped events — deterministically.
- [ ] `AlpacaPaperBroker` places a real bracket on the paper account and round-trips order events via FIX or via WS+REST-reconcile (WS alone is never treated as truth).
- [ ] `BrokerCapabilities` exposes `rth_only_bracket` (true for Alpaca) so callers handle pre-market entries explicitly.
- [ ] The shared contract suite passes against both adapters; mypy `--strict`, ruff, pytest green; CI green.

## Test strategy

The contract suite is the centerpiece: the same scenarios run against `FakeBroker` (always) and `AlpacaPaperBroker` (paper, behind a network marker/skip). Assert idempotency, bracket leg creation, partial-fill event fidelity, and that querying orders/positions returns broker truth. Fake-only tests cover adversarial event delivery.

## Tasks

- [ ] 1. `execution/broker.py` Protocol + request/result value objects + capabilities.
- [ ] 2. `FakeBroker` with scriptable fills/partials/rejects/event-ordering.
- [ ] 3. `AlpacaPaperBroker`: submit_bracket/cancel/flatten/query; pick FIX or WS+REST and wire order events.
- [ ] 4. Hook broker acks/fills into the Wave-1 ledger transitions.
- [ ] 5. Shared broker contract suite + fake-only adversarial tests.
- [ ] 6. ruff / mypy --strict / pytest green; CI green.

## Claude Code prompt

```
Implement plans/waves/WAVE-02-broker-port.md in the ross-trading repo on a new feature
branch. Define a broker-agnostic BrokerClient Protocol (mirroring the MarketDataProvider /
DecisionSink Protocol pattern already in the repo) with an idempotent
submit_bracket(client_order_id, ...) that realizes the architecture §3.6 atomic OCO
bracket, plus cancel, flatten, order_events, get_open_orders, get_positions. Implement two
adapters behind it: a deterministic in-memory FakeBroker (scriptable fills, partials,
rejects, and out-of-order/dropped events) and an AlpacaPaperBroker against the paper
endpoint. Do NOT treat Alpaca's order JSON WebSocket as source of truth (it has no sequence
numbers); consume order events via FIX, or treat the WS as a hint with REST /orders and
/positions authoritative. Wire broker acks/fills into the Wave-1 Ledger. Nothing above the
port may import a vendor symbol. Write one shared contract test suite parametrized over
both adapters. Work task-by-task; done when the advance_gate holds and CI is green.
```
