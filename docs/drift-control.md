# Drift Control

This document defines what counts as **drift** in this repository, how the
Drift CI pipeline classifies it, how merges are blocked, and how a developer
fixes or waives a finding.

The pipeline is implemented in:

- `.github/workflows/drift-ci.yml` — workflow definition
- `.github/prompts/drift-pr-audit.md` — audit prompt for the Claude scanner
- `scripts/drift_ci_check.py` — severity-to-exit-code helper
- `.github/ISSUE_TEMPLATE/drift_issue.yml` — follow-up issue form
- `.github/pull_request_template.md` — drift checklist on every PR

## What "drift" means here

Drift is **divergence between a pull request and the project's stated
direction**. It is not style, not preference, and not micro-optimization.
The stated direction lives in:

- `docs/architecture.md`, particularly the **Resolved Decisions** appendix
  (D5/#39, ISSUE-008, the Indicators contract, …).
- `docs/ground_truth.md` — the Phase 2 recall gate procedure and schema.
- `README.md` — public-facing scope and current phase.
- `plans/` (active) — in-flight implementation plans.
- `pyproject.toml` — tooling and dependency contracts.
- The source tree itself (existing abstractions, indicator template, journal
  schema).

A change that updates ground-truth artifacts in the same PR as the code is
**not drift** — it is the project moving deliberately. Reversing a Resolved
Decision silently is drift even when the new behavior is reasonable.

## Severity levels

Each finding has one severity. The CI helper uses these to decide whether to
block merge.

### BLOCKER — fails CI; never merges without explicit override

- Violates core product direction (e.g., adds short-side trading without an
  architecture update).
- Breaks an architecture contract (e.g., removes the float ≤ 20M cap, adds
  news as a hard filter, forks `Scanner` instead of parameterizing it).
- Removes required tests or weakens existing assertions.
- Creates a security exposure (committed secrets, `pull_request_target`
  abuse, shell-injection in CI).
- Makes documentation materially false.
- Introduces a forbidden dependency (e.g., TA-Lib runtime — see the
  **Indicators contract** in `docs/architecture.md`).

### HIGH — fails CI unless waived

- Adds an inconsistent implementation pattern (e.g., a second sort order
  alongside the canonical ranker key).
- Duplicates core logic (re-implementing `float_tier_weight`, etc.).
- Ships a partial feature without unit tests.
- Bypasses an established abstraction (writing directly to the journal DB
  instead of through the writer).
- Creates real maintainability risk that the next contributor will pay for.

### MEDIUM — does not fail CI; surfaced as a comment / follow-up issue

- Behavior changed but the architecture doc / plan was not updated.
- Minor UX inconsistency in CLI / report output.
- Incomplete edge-case handling that is not safety-critical.
- Non-critical test gaps (happy path is covered, an error branch is not).

### LOW — informational only

- Naming inconsistency.
- Minor cleanup that the author chose to defer.
- Small documentation improvements.

## How CI enforces drift control

Every PR triggers `.github/workflows/drift-ci.yml`. The workflow:

1. Installs the project (`pip install -e ".[dev]"`).
2. Runs the **baseline** checks — ruff, mypy strict, pytest unit + integration,
   `alembic upgrade head` against a temp SQLite DB.
3. Runs the **drift scan**: `anthropics/claude-code-action@v1` reads
   `.github/prompts/drift-pr-audit.md`, evaluates the PR against the
   ground-truth artifacts above, and writes `drift-report.json` plus
   `drift-report.md` into the workspace.
4. Runs `scripts/drift_ci_check.py`, which:
   - Parses the JSON.
   - Reads the PR's labels (specifically `drift-waiver`).
   - Decides whether the workflow should fail.
   - Writes a structured summary to `$GITHUB_STEP_SUMMARY`.
   - Exits non-zero on a merge-blocking finding.
5. Uploads `drift-report.json` and `drift-report.md` as workflow artifacts.
6. Posts (or refreshes) a PR comment with the human-readable summary on
   non-fork PRs. Forked PRs see the step summary only — the workflow does
   not expose `CLAUDE_CODE_OAUTH_TOKEN` to fork builds.

Merge is blocked when any of the following is true:

- A baseline check fails (lint, type, tests, migrations).
- The drift report contains a BLOCKER finding.
- The drift report contains a HIGH finding and the PR does **not** carry
  the `drift-waiver` label.
- The drift scan could not complete (missing JSON, parse error, or missing
  Claude credentials on a non-fork PR). Treated as merge-blocking per the
  pipeline's "missing required context" rule.

MEDIUM and LOW findings never block merge. They surface in the step summary
and PR comment so a follow-up issue can be filed using
`.github/ISSUE_TEMPLATE/drift_issue.yml`.

## How to fix drift

The two most common fixes:

1. **Adjust the PR.** Re-use the existing abstraction; restore the test you
   removed; update the documented behavior; drop the new dependency. Push a
   new commit; CI re-runs.
2. **Update the ground truth.** If the PR is the right direction and the
   docs are stale, update `docs/architecture.md` (and the Resolved Decisions
   appendix, if you are reversing a decision) **in the same PR**. The audit
   compares the PR diff against the docs *as the PR proposes them*, so
   doc-and-code together is not drift.

If neither path is acceptable for the current PR, request a waiver.

## Waiver process

Waivers exist for HIGH-severity drift that cannot be fixed inside the
current PR but should not block delivery (typical case: a refactor that
unblocks a feature, with a documented follow-up).

To request a waiver:

1. Apply the `drift-waiver` label to the PR.
2. Fill in the three lines under **Drift waiver** in the PR template:
   why the waiver is needed, who approved it, and the follow-up issue link.
3. File the follow-up issue using
   `.github/ISSUE_TEMPLATE/drift_issue.yml`. The issue is the obligation
   the waiver buys; the PR is the green light, the issue is the IOU.

The CI pipeline accepts the `drift-waiver` label and demotes HIGH findings
to non-blocking. **BLOCKER findings are not waived by the label.** A
BLOCKER override requires a maintainer to either:

- amend the offending change so it is no longer a BLOCKER, or
- update the ground-truth artifact so the change is consistent (then it is
  no longer drift), or
- in extraordinary cases, set the workflow input
  `allow_blocker_waiver=true` on a `workflow_dispatch` re-run, with the
  rationale recorded in the PR. This is the only sanctioned path; do not
  edit the workflow file to bypass the check.

## Workflow self-modification

The Claude action used by the drift scan refuses to run with repository
secrets when the PR modifies `.github/workflows/drift-ci.yml` or
`.github/prompts/drift-pr-audit.md` — the workflow file in the PR must
match the version on `main`, or the action self-skips as a security
measure against secret abuse.

When the pipeline detects this case it writes a synthetic "warning" drift
report so the PR is not blocked by an infrastructure constraint that
cannot be satisfied. **The audit did not actually run.** Reviewers of any
PR that touches these two files must read the diff manually and confirm
that no policy is being weakened (e.g., severity rules, waiver semantics,
the prompt's evidence requirement). Treat it as you would a change to
branch protection: small, visible, justified.

## See also

- `docs/architecture.md` — the canonical product and architecture spec.
- `docs/ground_truth.md` — the Phase 2 recall gate procedure.
- `.github/workflows/ci.yml` — baseline CI that runs alongside drift CI.
