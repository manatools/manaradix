"""Unit tests for the pre-upgrade snapshot trigger script
(``data/libexec/manaradix-pre-upgrade``).

The script is the body of the rpm ``%transfiletriggerun``: rpm runs it
as root at the start of every package transaction touching ``/usr`` or
``/etc``.  Its contract is that it **never fails the transaction** —
rpm aborts the whole transaction when a trigger exits non-zero, so
every error path must exit 0.

The tests run the script with a fake CLI (no btrfs, no root needed) and
the env-var test hooks (``MANARADIX_BIN``, ``MANARADIX_RUN_DIR``,
``MANARADIX_THROTTLE_SECONDS``); production rpm scriptlets run with a
clean environment, so the defaults are what matter there.
"""

import stat
import subprocess
import time
from pathlib import Path

TRIGGER = (
    Path(__file__).resolve().parents[1] / "data" / "libexec" / "manaradix-pre-upgrade"
)


def make_fake_cli(tmp_path: Path) -> Path:
    """A fake ``manaradix`` that records its argv and exits 0 (or
    ``$FAKE_CLI_EXIT``)."""
    log = tmp_path / "invocations.log"
    cli = tmp_path / "fake-manaradix"
    cli.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{log}"\n'
        'exit "${FAKE_CLI_EXIT:-0}"\n',
        encoding="utf-8",
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    return cli


def run_trigger(
    tmp_path: Path,
    *,
    cli: Path | None,
    throttle: str = "600",
    cli_exit: str = "0",
) -> subprocess.CompletedProcess:
    """Run the trigger script in isolation with the test hooks set."""
    env = {
        "PATH": "/usr/bin:/bin",
        "MANARADIX_BIN": str(cli) if cli is not None else "/nonexistent/manaradix",
        "MANARADIX_RUN_DIR": str(tmp_path / "run"),
        "MANARADIX_THROTTLE_SECONDS": throttle,
        "FAKE_CLI_EXIT": cli_exit,
    }
    return subprocess.run(
        [str(TRIGGER)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


class TestTriggerContract:
    def test_missing_cli_exits_zero(self, tmp_path) -> None:
        proc = run_trigger(tmp_path, cli=None)
        assert proc.returncode == 0

    def test_invokes_cli_with_pre_upgrade_args(self, tmp_path) -> None:
        cli = make_fake_cli(tmp_path)
        proc = run_trigger(tmp_path, cli=cli)
        assert proc.returncode == 0

        log = (tmp_path / "invocations.log").read_text()
        assert "snapshot" in log
        assert "create" in log
        assert "--subvolume" in log
        assert "/" in log
        assert "--type" in log
        assert "pre-upgrade" in log
        assert "--schedule" in log

    def test_cli_failure_still_exits_zero(self, tmp_path) -> None:
        """A failed snapshot must not abort the rpm transaction."""
        cli = make_fake_cli(tmp_path)
        proc = run_trigger(tmp_path, cli=cli, cli_exit="1")
        assert proc.returncode == 0

    def test_writes_throttle_marker(self, tmp_path) -> None:
        cli = make_fake_cli(tmp_path)
        run_trigger(tmp_path, cli=cli)
        assert (tmp_path / "run" / "last-pre-upgrade").is_file()

    def test_throttled_within_window(self, tmp_path) -> None:
        """An update run may split into several rpm transactions; only one
        pre-upgrade snapshot per window."""
        cli = make_fake_cli(tmp_path)
        run_trigger(tmp_path, cli=cli)
        run_trigger(tmp_path, cli=cli)
        log = (tmp_path / "invocations.log").read_text()
        assert log.count("snapshot") == 1

    def test_throttle_zero_disables_throttle(self, tmp_path) -> None:
        cli = make_fake_cli(tmp_path)
        run_trigger(tmp_path, cli=cli, throttle="0")
        run_trigger(tmp_path, cli=cli, throttle="0")
        log = (tmp_path / "invocations.log").read_text()
        assert log.count("snapshot") == 2

    def test_throttle_expires(self, tmp_path) -> None:
        """Once the window passes, the next transaction snapshots again."""
        cli = make_fake_cli(tmp_path)
        run_trigger(tmp_path, cli=cli, throttle="1")
        # Backdate the marker beyond the 1-second window.
        marker = tmp_path / "run" / "last-pre-upgrade"
        stale = int(time.time()) - 5
        marker.write_text(str(stale), encoding="utf-8")
        run_trigger(tmp_path, cli=cli, throttle="1")
        log = (tmp_path / "invocations.log").read_text()
        assert log.count("snapshot") == 2
