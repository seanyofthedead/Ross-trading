"""Drift CI enforcement helper.

Reads ``drift-report.json`` produced by the drift audit step, applies severity
rules, honors the ``drift-waiver`` PR label, writes a human-readable summary to
``$GITHUB_STEP_SUMMARY``, and exits non-zero when drift should block merge.

Pure stdlib; no third-party dependencies.

Exit codes:
  0  pipeline passes (no blockers, or HIGH covered by waiver)
  1  merge-blocking drift found
  2  drift report missing or unparseable (treated as merge-blocking per spec
     Phase 2.5: "Drift scan cannot complete due to missing required context")
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

SEVERITIES = ("BLOCKER", "HIGH", "MEDIUM", "LOW")
SEVERITY_ORDER = {s: i for i, s in enumerate(SEVERITIES)}
WAIVER_LABEL = "drift-waiver"


class ReportError(RuntimeError):
    """Raised when the drift report is missing, malformed, or invalid."""


def _load_report(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ReportError(f"drift report not found at {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReportError(f"drift report is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ReportError("drift report root must be a JSON object")
    findings = data.get("findings", [])
    if not isinstance(findings, list):
        raise ReportError("'findings' must be an array")
    for f in findings:
        if not isinstance(f, dict):
            raise ReportError("each finding must be an object")
        sev = f.get("severity")
        if sev not in SEVERITIES:
            raise ReportError(
                f"finding has invalid severity {sev!r}; expected one of {SEVERITIES}"
            )
    return data


def _label_set(labels_arg: str | None) -> set[str]:
    if not labels_arg:
        return set()
    return {x.strip() for x in labels_arg.split(",") if x.strip()}


def _bucket(findings: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {s: [] for s in SEVERITIES}
    for f in findings:
        out[f["severity"]].append(f)
    return out


def _decide(
    buckets: dict[str, list[dict[str, Any]]],
    labels: set[str],
    *,
    allow_blocker_waiver: bool,
) -> tuple[bool, str]:
    """Return (merge_blocking, reason)."""
    has_waiver = WAIVER_LABEL in labels
    if buckets["BLOCKER"]:
        if has_waiver and allow_blocker_waiver:
            return False, "BLOCKER findings present but waiver explicitly allows them"
        return True, f"{len(buckets['BLOCKER'])} BLOCKER finding(s) present"
    if buckets["HIGH"]:
        n = len(buckets["HIGH"])
        if has_waiver:
            return False, f"{n} HIGH finding(s) covered by '{WAIVER_LABEL}' label"
        return True, f"{n} HIGH finding(s) without '{WAIVER_LABEL}' label"
    return False, "no merge-blocking drift detected"


def _format_finding(f: dict[str, Any]) -> str:
    fid = f.get("id", "DRIFT-???")
    title = f.get("title", "(no title)")
    cat = f.get("category", "Unknown")
    why = f.get("why_this_is_drift", "")
    fix = f.get("required_correction", "")
    evidence = f.get("evidence") or []
    ev_md = ", ".join(f"`{e}`" for e in evidence) if evidence else "_none provided_"
    issue_title = f.get("suggested_issue_title", "")
    parts = [
        f"### {fid} — {title}",
        f"**Category:** {cat}",
        f"**Evidence:** {ev_md}",
        "",
        f"**Why this is drift:** {why}",
        "",
        f"**Required correction:** {fix}",
    ]
    if issue_title:
        parts.append(f"**Suggested follow-up issue:** _{issue_title}_")
    return "\n".join(parts)


def _build_summary(
    report: dict[str, Any],
    buckets: dict[str, list[dict[str, Any]]],
    labels: set[str],
    merge_blocking: bool,
    reason: str,
) -> str:
    lines: list[str] = []
    lines.append("# Drift CI Report")
    lines.append("")
    status_icon = "BLOCKED" if merge_blocking else "OK"
    overall = report.get("overall_status", "unknown")
    lines.append(f"**Status:** {status_icon} ({overall})")
    lines.append(f"**Decision:** {reason}")
    if WAIVER_LABEL in labels:
        lines.append(f"**Waiver:** `{WAIVER_LABEL}` label present on PR")
    lines.append("")
    lines.append("## Counts by severity")
    lines.append("")
    lines.append("| Severity | Count |")
    lines.append("| --- | --- |")
    for sev in SEVERITIES:
        lines.append(f"| {sev} | {len(buckets[sev])} |")
    lines.append("")
    auditor_md = report.get("summary_markdown")
    if auditor_md:
        lines.append("## Auditor summary")
        lines.append("")
        lines.append(str(auditor_md).strip())
        lines.append("")
    for sev in SEVERITIES:
        items = buckets[sev]
        if not items:
            continue
        lines.append(f"## {sev} findings ({len(items)})")
        lines.append("")
        for f in items:
            lines.append(_format_finding(f))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _append_step_summary(text: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    try:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(text)
    except OSError as exc:
        print(f"warning: failed to write step summary: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("drift-report.json"),
        help="path to drift-report.json (default: drift-report.json)",
    )
    parser.add_argument(
        "--labels",
        default=os.environ.get("PR_LABELS", ""),
        help="comma-separated PR labels (default: $PR_LABELS)",
    )
    parser.add_argument(
        "--allow-blocker-waiver",
        action="store_true",
        default=os.environ.get("DRIFT_ALLOW_BLOCKER_WAIVER", "false").lower() == "true",
        help="permit drift-waiver to override BLOCKER (off by default)",
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=Path("drift-report.md"),
        help="write the rendered Markdown summary here too",
    )
    args = parser.parse_args(argv)

    try:
        report = _load_report(args.report)
    except ReportError as exc:
        msg = (
            f"# Drift CI Report\n\n"
            f"**Status:** BLOCKED (scan-failed)\n"
            f"**Decision:** drift scan could not complete: {exc}\n\n"
            f"This is treated as merge-blocking per the drift-control policy "
            f"(`docs/drift-control.md`).\n"
        )
        _append_step_summary(msg)
        print(msg, file=sys.stderr)
        return 2

    findings = report.get("findings", [])
    buckets = _bucket(findings)
    labels = _label_set(args.labels)
    merge_blocking, reason = _decide(
        buckets, labels, allow_blocker_waiver=args.allow_blocker_waiver
    )

    summary = _build_summary(report, buckets, labels, merge_blocking, reason)
    _append_step_summary(summary)
    try:
        args.output_md.write_text(summary, encoding="utf-8")
    except OSError as exc:
        print(f"warning: failed to write {args.output_md}: {exc}", file=sys.stderr)

    print(summary)
    return 1 if merge_blocking else 0


if __name__ == "__main__":
    sys.exit(main())
