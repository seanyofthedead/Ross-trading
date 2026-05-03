"""Verify ``alembic -x sqlalchemy.url=...`` overrides the alembic.ini default.

Drift-audit follow-up (closure of PR #71). The CI workflow runs
``alembic -x sqlalchemy.url=sqlite:///$RUNNER_TEMP/journal.sqlite upgrade head``
to validate migrations against a temp DB. ``env.py`` consumes the
``-x`` argument via ``context.get_x_argument(as_dictionary=True)`` and
calls ``set_main_option`` on the config; nothing else in the test suite
exercises that branch (other tests inject a connection via
``Config.attributes['connection']`` and bypass the CLI). This test
runs the real CLI as a subprocess so a regression in env.py's
``-x`` handling fails CI rather than silently routing to the default
``sqlite:///journal.sqlite`` path.

Both tests run with ``cwd=tmp_path`` and a per-test copy of
``alembic.ini`` whose ``script_location`` has been absolute-ized; that
keeps the default ``sqlite:///journal.sqlite`` resolution relative to
``tmp_path`` so a regression cannot pollute the repo root.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DB_FILENAME = "journal.sqlite"
_REL_SCRIPT_LOCATION = "src/ross_trading/journal/migrations"
_ABS_SCRIPT_LOCATION = (_REPO_ROOT / _REL_SCRIPT_LOCATION).resolve()


def _alembic_command_present() -> bool:
    return shutil.which("alembic") is not None


def _isolated_alembic_ini(tmp_path: Path) -> Path:
    """Copy alembic.ini into *tmp_path* with an absolute ``script_location``.

    The repo's alembic.ini uses a path relative to the repo root, which
    breaks the moment the CLI is invoked from a different cwd. Tests
    rewrite ``script_location`` to an absolute path so cwd can be the
    tmp dir without breaking migration discovery -- and so a default
    ``sqlite:///journal.sqlite`` (relative URL) lands in tmp_path,
    visible to the assertions, not in the real repo root.
    """
    raw = (_REPO_ROOT / "alembic.ini").read_text(encoding="utf-8")
    rewritten = raw.replace(
        f"script_location = {_REL_SCRIPT_LOCATION}",
        f"script_location = {_ABS_SCRIPT_LOCATION.as_posix()}",
    )
    target = tmp_path / "alembic.ini"
    target.write_text(rewritten, encoding="utf-8")
    return target


@pytest.mark.skipif(
    not _alembic_command_present(),
    reason="alembic CLI not on PATH; CI installs it via .[dev]",
)
def test_x_args_url_overrides_alembic_ini_default(tmp_path: Path) -> None:
    """``-x sqlalchemy.url=`` must create the temp DB and skip the default path."""
    cfg = _isolated_alembic_ini(tmp_path)
    target = tmp_path / "alembic-x-arg-target.sqlite"
    fallback_default = tmp_path / _DEFAULT_DB_FILENAME

    result = subprocess.run(  # noqa: S603 -- args are repo-controlled constants + tmp paths
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(cfg),
            "-x",
            f"sqlalchemy.url=sqlite:///{target.as_posix()}",
            "upgrade",
            "head",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, (
        f"alembic upgrade head failed:\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert target.is_file(), (
        f"-x sqlalchemy.url override did not create the requested DB at "
        f"{target}; env.py's get_x_argument branch may have regressed."
    )
    assert not fallback_default.is_file(), (
        f"alembic.ini default path was created at {fallback_default} -- "
        f"-x override was silently ignored."
    )


@pytest.mark.skipif(
    not _alembic_command_present(),
    reason="alembic CLI not on PATH; CI installs it via .[dev]",
)
def test_no_x_args_uses_alembic_ini_default(tmp_path: Path) -> None:
    """No ``-x`` argument: the alembic.ini default path is used."""
    cfg = _isolated_alembic_ini(tmp_path)
    expected_target = tmp_path / _DEFAULT_DB_FILENAME

    result = subprocess.run(  # noqa: S603 -- args are repo-controlled constants + tmp paths
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(cfg),
            "upgrade",
            "head",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, (
        f"alembic upgrade head failed:\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert expected_target.is_file(), (
        f"Default ``sqlite:///journal.sqlite`` path was not honored; "
        f"expected DB at {expected_target}."
    )
