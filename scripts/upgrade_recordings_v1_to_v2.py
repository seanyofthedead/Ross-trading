"""Offline, idempotent upgrade of v1 recordings to the v2 wire format.

Wave 0 bumped the recording schema to ``SCHEMA_VERSION = 2`` (per-message
``seq`` + the ``exchange_ts``/``vendor_ts``/``ingest_ts`` split). The v2
build still *decodes* v1 recordings forever (see
:mod:`ross_trading.data._codec`), so this script is **optional** -- run it
only when you want on-disk files re-stamped as native v2 (e.g. to stop
relying on synthesized defaults, or before archiving).

Guarantees:

- **Non-destructive.** The source tree is never modified; output goes to a
  separate directory (``--dest``, default ``<source>-v2``). Originals stay
  recoverable.
- **Idempotent.** The transform is deterministic and gzip is written with a
  fixed header, so re-running produces byte-identical output -- a no-op.
  Pointing ``--source`` at an already-v2 tree is also a no-op (v2 decodes
  to the same objects it re-encodes from).

Synthesized v1 defaults match the codec's contract: ``seq`` from file
order, ``exchange_ts = vendor_ts = ts``, ``ingest_ts = ts_recorded``.

Usage::

    python -m scripts.upgrade_recordings_v1_to_v2 --source ./recordings
    python scripts/upgrade_recordings_v1_to_v2.py --source ./recordings --dest ./recordings-v2
"""

from __future__ import annotations

import argparse
import gzip
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ross_trading.data._codec import (
    EventType,
    decode_bar,
    decode_correction,
    decode_envelope,
    decode_feed_gap,
    decode_float,
    decode_halt,
    decode_headline,
    decode_quote,
    decode_tape,
    encode_bar,
    encode_correction,
    encode_event,
    encode_feed_gap,
    encode_float,
    encode_halt,
    encode_headline,
    encode_quote,
    encode_tape,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

# gzip mtime=0 keeps the output header deterministic so a re-run produces
# byte-identical files (the idempotency guarantee).
_GZIP_MTIME = 0


def _upgrade_line(line: str, fallback_seq: int) -> str:
    """Decode one recorded line and re-encode it under SCHEMA_VERSION 2.

    v1 market-data payloads are upgraded with synthesized defaults; every
    other event type round-trips through its typed decoder/encoder.
    """
    env = decode_envelope(line)
    et = env.event_type
    if et is EventType.QUOTE:
        payload = encode_quote(
            decode_quote(
                env.payload,
                version=env.version,
                ts_recorded=env.ts_recorded,
                fallback_seq=fallback_seq,
            )
        )
    elif et is EventType.BAR:
        payload = encode_bar(
            decode_bar(
                env.payload,
                version=env.version,
                ts_recorded=env.ts_recorded,
                fallback_seq=fallback_seq,
            )
        )
    elif et is EventType.TAPE:
        payload = encode_tape(
            decode_tape(
                env.payload,
                version=env.version,
                ts_recorded=env.ts_recorded,
                fallback_seq=fallback_seq,
            )
        )
    elif et is EventType.HALT:
        payload = encode_halt(decode_halt(env.payload))
    elif et is EventType.CORRECTION:
        payload = encode_correction(decode_correction(env.payload))
    elif et is EventType.HEADLINE:
        payload = encode_headline(decode_headline(env.payload))
    elif et is EventType.FLOAT:
        payload = encode_float(decode_float(env.payload))
    elif et is EventType.FEED_GAP:
        payload = encode_feed_gap(decode_feed_gap(env.payload))
    else:  # pragma: no cover -- EventType is exhaustive above
        msg = f"unhandled event type: {et!r}"
        raise ValueError(msg)
    return encode_event(et, payload, env.ts_recorded)


def _read_lines(path: Path) -> Iterable[str]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if line:
                yield line


def _write_lines(path: Path, lines: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.GzipFile(path, "wb", mtime=_GZIP_MTIME) as gz:
        for line in lines:
            gz.write(line.encode("utf-8"))
            gz.write(b"\n")


def upgrade_recordings(source: Path, dest: Path) -> list[Path]:
    """Upgrade every ``<day>/<event>.jsonl.gz`` under ``source`` into ``dest``.

    Returns the list of written destination files (sorted). Mirrors the
    source's ``<day>/<event>`` layout; non-date directories and non-recording
    files are skipped.
    """
    if not source.exists():
        msg = f"source does not exist: {source}"
        raise FileNotFoundError(msg)
    if dest == source or source in dest.parents:
        msg = "dest must be outside source (the upgrade is non-destructive)"
        raise ValueError(msg)
    written: list[Path] = []
    for gz_path in sorted(source.rglob("*.jsonl.gz")):
        rel = gz_path.relative_to(source)
        out_path = dest / rel
        upgraded = [
            _upgrade_line(line, idx) for idx, line in enumerate(_read_lines(gz_path))
        ]
        _write_lines(out_path, upgraded)
        written.append(out_path)
    return written


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.upgrade_recordings_v1_to_v2",
        description="Upgrade v1 recordings to the v2 wire format (non-destructive).",
    )
    parser.add_argument(
        "--source", required=True, type=Path, metavar="DIR",
        help="Recordings root to upgrade: <DIR>/<YYYY-MM-DD>/<event>.jsonl.gz",
    )
    parser.add_argument(
        "--dest", type=Path, default=None, metavar="DIR",
        help="Output root (default: <source>-v2). Must be outside --source.",
    )
    return parser


def _main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    source: Path = args.source
    dest: Path = args.dest if args.dest is not None else source.with_name(source.name + "-v2")
    written = upgrade_recordings(source, dest)
    for path in written:
        print(path)
    print(f"upgraded {len(written)} file(s) into {dest}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
