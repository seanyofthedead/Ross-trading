<!--
Pull request template for ross-trading.
The Drift CI pipeline runs on every PR. See docs/drift-control.md for what it checks.
-->

## Summary

<!-- One paragraph: what changes and why. Link the issue / plan if there is one. -->

## Drift-control checklist

Tick the box if the statement is true. If a box cannot be ticked, explain in
the **Drift waiver** section below — or fix the PR until it can be ticked.

- [ ] No Resolved Decision in `docs/architecture.md` is reversed without
      updating the appendix in this same PR.
- [ ] Behavior changes are reflected in `docs/architecture.md`, `README.md`,
      or the relevant `plans/` document.
- [ ] New runtime dependencies are justified in the PR description and do
      not violate the Indicators contract (no TA-Lib runtime dependency).
- [ ] New code paths have unit tests; new cross-module behavior has an
      integration test marked `@pytest.mark.integration`.
- [ ] No secrets, API keys, or broker credentials are committed.
- [ ] Existing module boundaries are respected; logic that already lives in
      the codebase is reused, not re-implemented.

## Test plan

<!-- Bullet list of how the change was verified. -->

- [ ] `ruff check src tests`
- [ ] `mypy src tests`
- [ ] `pytest -m "not integration"`
- [ ] `pytest -m integration`
- [ ] `alembic upgrade head` (if migrations were touched)

## Drift waiver (only if needed)

<!--
Leave blank for PRs that pass the drift checklist.

If the Drift CI pipeline reports a HIGH-severity finding that you intend to
ship anyway, request a waiver:

  1. Apply the `drift-waiver` label to this PR.
  2. Fill in the three lines below.
  3. Link the follow-up issue that will resolve the drift.

BLOCKER findings are not waivable except by repository maintainer override
(see docs/drift-control.md).
-->

- **Why is the waiver needed?**
- **Who approved it?**
- **Follow-up issue:** <!-- e.g., #123 -->
