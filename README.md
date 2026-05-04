# Ross-trading

Autonomous AI trading agent mimicking Ross Cameron's small-cap momentum process.
Currently in Phase 2: scanner + journaling, no live trading.

## Status

[![CI](https://github.com/seanyofthedead/Ross-trading/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/seanyofthedead/Ross-trading/actions/workflows/ci.yml)
[![Drift CI](https://github.com/seanyofthedead/Ross-trading/actions/workflows/drift-ci.yml/badge.svg?branch=main)](https://github.com/seanyofthedead/Ross-trading/actions/workflows/drift-ci.yml)

Pull requests are gated by [Drift CI](docs/drift-control.md): baseline checks (ruff, mypy, pytest, alembic) plus a Claude-powered drift audit that compares the diff against `docs/architecture.md`, `docs/ground_truth.md`, and the active `plans/`. See `docs/drift-control.md` for severity rules and the waiver process.

## Quickstart

```bash
git clone https://github.com/seanyofthedead/Ross-trading.git
cd Ross-trading
pip install -e ".[dev]"
pytest
alembic upgrade head
python -m ross_trading.journal.report --date YYYY-MM-DD
```

## Architecture

See [`docs/architecture.md`](docs/architecture.md) for the full design (modules, decision logic, validation approach, resolved decisions).

## Plans

In-flight implementation plans live in [`plans/`](plans/); merged plans are archived under [`plans/archive/`](plans/archive/).

## Disclaimer

Cameron is explicit that his results are not typical and that most day traders lose money. Mimicking his process does not entitle the agent to mimic his outcomes. Treat everything in this repository as an engineering spec, not investment advice. Paper-trade for months before allocating real capital.
