# Execution build — wave plan (loop runner)

These waves turn the trade-execution design review into large, dependency-ordered units
you can run one-at-a-time in a Claude Code loop. Each `WAVE-NN-*.md` is self-contained:
goal, scope, files, key interfaces, acceptance criteria, tasks, test strategy, an
**advance gate**, and a **paste-ready prompt**.

They are deliberately *larger* than the repo's per-atom plans (e.g.
`plans/phase-4-pattern-detector-a1-entry-signal.md`). A wave is a coherent shippable
slice; inside a wave, the implementing agent may still work atom-by-atom.

## Loop contract

Each file has YAML frontmatter:

```yaml
wave: 0
depends_on: []           # wave ids that must be `status: done` first
advance_gate: "..."      # the objective condition to mark this wave done
status: not_started      # not_started | in_progress | done
```

A driver loop should:

1. Pick the lowest-numbered wave whose `status != done` **and** every `depends_on` is `done`.
2. Paste that wave's **"Claude Code prompt"** block into a fresh Claude Code run.
3. Let it implement task-by-task on a feature branch.
4. When the **advance gate** passes (CI green + the named test), flip `status: done` and loop.

## Order & gates (summary)

| Wave | Title | Depends on | Advance gate (short) |
|---|---|---|---|
| 0 | Ingestion contract correctness | — | `(ts,seq)` ordering + seq-gap detection + halt/correction events; live-vs-replay parity test green |
| 1 | Execution domain model & ledger | 0 | orders/fills/positions tables + state machine + idempotency, migration applies, unit tests green |
| 2 | Broker port + Fake + paper adapter | 1 | `BrokerClient` Protocol + `FakeBroker` + `AlpacaPaperBroker`; idempotent `submit_bracket`; contract tests green |
| 3 | Risk supervisor / kill switch | 1, 2 | `safety/` enforces max-loss, loser-lock, single-position, PDT/T+1; force-flatten; loop-gated; tests green |
| 4 | Reconciliation (broker truth) | 2, 3 | startup + cadence reconcile ledger vs broker; divergence → kill switch; recovery test green |
| 5 | Live signal→order wiring + event-driven exits | 2, 3, 4 | trading + position-management loops wired; exits consume order-event + quote streams (no polling); sizer Decimal/int; integration tests green on FakeBroker |
| 6 | Go-live gate: paid SIP data, parity CI, paper soak | 0, 5 | full-SIP feed wired; bit-identical live-vs-replay CI gate enforced; float daily-refresh verified; micro-stakes paper soak runbook |

**Must-do-before-live:** Waves 0–6 in order. Waves 0, 1, 3, 4 and the Wave 6 parity gate are
the non-negotiable correctness/safety set. Everything tagged "later hardening" inside a wave
can ship after first live capital.

> This is a READ-of-review → BUILD plan. The reviews it derives from:
> `docs/architecture.md` (§3.5–§3.8, D6), `.claude/decision-issues.json` (#10 broker,
> #11 data feed, #12 L2, #33 float cadence, #78 recordings).
