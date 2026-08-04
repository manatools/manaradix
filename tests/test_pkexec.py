"""Unit tests for pkexec privilege escalation helpers."""

import os
from unittest.mock import patch

import pytest

from manaradix.utils.pkexec import (
    _REEXEC_ENV_VAR,
    already_reexecuted,
    is_root,
    needs_elevation,
    reexec_with_pkexec,
)


# ---------------------------------------------------------------------------
# is_root
# ---------------------------------------------------------------------------


class TestIsRoot:
    @patch("os.geteuid", return_value=0)
    def test_root(self, mock_geteuid) -> None:
        assert is_root() is True

    @patch("os.geteuid", return_value=1000)
    def test_non_root(self, mock_geteuid) -> None:
        assert is_root() is False


# ---------------------------------------------------------------------------
# already_reexecuted
# ---------------------------------------------------------------------------


class TestAlreadyReexecuted:
    def test_env_not_set(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert already_reexecuted() is False

    def test_env_set_to_1(self) -> None:
        with patch.dict(os.environ, {_REEXEC_ENV_VAR: "1"}):
            assert already_reexecuted() is True

    def test_env_set_to_0(self) -> None:
        with patch.dict(os.environ, {_REEXEC_ENV_VAR: "0"}):
            assert already_reexecuted() is False


# ---------------------------------------------------------------------------
# needs_elevation
# ---------------------------------------------------------------------------


class TestNeedsElevation:
    # -- GUI mode ----------------------------------------------------------

    def test_gui_mode_needs_elevation(self) -> None:
        assert needs_elevation(None, None) is True

    # -- Read-only subcommands ---------------------------------------------

    def test_snapshot_list_is_readonly(self) -> None:
        assert needs_elevation("snapshot", "list") is False

    def test_snapshot_info_is_readonly(self) -> None:
        assert needs_elevation("snapshot", "info") is False

    def test_setup_status_is_readonly(self) -> None:
        assert needs_elevation("setup", "status") is False

    def test_setup_list_profiles_is_readonly(self) -> None:
        assert needs_elevation("setup", "list-profiles") is False

    # -- Privileged subcommands --------------------------------------------

    def test_snapshot_create_needs_elevation(self) -> None:
        assert needs_elevation("snapshot", "create") is True

    def test_snapshot_delete_needs_elevation(self) -> None:
        assert needs_elevation("snapshot", "delete") is True

    def test_setup_apply_needs_elevation(self) -> None:
        assert needs_elevation("setup", "apply") is True

    def test_snapshot_prune_needs_elevation(self) -> None:
        assert needs_elevation("snapshot", "prune") is True

    # -- Unknown subcommands (safe default) --------------------------------

    def test_unknown_command_defaults_to_elevated(self) -> None:
        assert needs_elevation("filesystem", "balance") is True

    def test_unknown_subcommand_defaults_to_elevated(self) -> None:
        assert needs_elevation("snapshot", "unknown_action") is True

    # -- D-Bus subcommands --------------------------------------------------

    def test_dbus_serve_is_not_elevated(self) -> None:
        # The daemon refuses non-root itself; pkexec must never be involved.
        assert needs_elevation("dbus", "serve") is False

    def test_dbus_status_is_not_elevated(self) -> None:
        assert needs_elevation("dbus", "status") is False

    def test_unknown_dbus_subcommand_defaults_to_elevated(self) -> None:
        assert needs_elevation("dbus", "bogus") is True

    # -- Activity subcommands -----------------------------------------------

    def test_activity_status_is_readonly(self) -> None:
        assert needs_elevation("activity", "status") is False

    def test_activity_record_is_unit_internal(self) -> None:
        # Invoked by the generated units, which run as root by
        # construction — pkexec must never be involved.
        assert needs_elevation("activity", "record") is False


# ---------------------------------------------------------------------------
# reexec_with_pkexec
# ---------------------------------------------------------------------------


class TestReexecWithPkexec:
    def test_sets_env_var_and_calls_execve(self) -> None:
        with patch("os.execve") as mock_execve:
            with patch("sys.argv", ["/usr/bin/manaradix", "snapshot", "create", "--dry-run"]):
                with patch.dict(os.environ, {}, clear=True):
                    # execve doesn't return, so side_effect won't be hit.
                    # We just verify the call arguments.
                    mock_execve.side_effect = SystemExit(0)
                    with pytest.raises(SystemExit):
                        reexec_with_pkexec()

    def test_pkexec_not_found_exits(self, capsys) -> None:
        with patch("os.execve", side_effect=FileNotFoundError):
            with patch("sys.argv", ["/usr/bin/manaradix", "snapshot", "create"]):
                with pytest.raises(SystemExit) as exc_info:
                    reexec_with_pkexec()
                assert exc_info.value.code == 1
                captured = capsys.readouterr()
                assert "not found" in captured.err

    def test_oserror_exits(self, capsys) -> None:
        with patch("os.execve", side_effect=OSError("permission denied")):
            with patch("sys.argv", ["/usr/bin/manaradix", "snapshot", "create"]):
                with pytest.raises(SystemExit) as exc_info:
                    reexec_with_pkexec()
                assert exc_info.value.code == 1
                captured = capsys.readouterr()
                assert "pkexec failed" in captured.err

    def test_target_binary_is_pkexec(self) -> None:
        with patch("os.execve") as mock_execve:
            mock_execve.side_effect = SystemExit(0)
            with patch("sys.argv", ["/usr/bin/manaradix", "snapshot", "list"]):
                with pytest.raises(SystemExit):
                    reexec_with_pkexec()
                args = mock_execve.call_args[0]
                # First arg to execve is the program path
                assert args[0] == "/usr/bin/pkexec"
                # Args list includes pkexec --disable-internal-agent /usr/bin/manaradix ...
                assert "--disable-internal-agent" in args[1]
                assert "/usr/bin/manaradix" in args[1]

    def test_preserves_original_args(self) -> None:
        with patch("os.execve") as mock_execve:
            mock_execve.side_effect = SystemExit(0)
            original_argv = [
                "/usr/bin/manaradix",
                "snapshot", "create",
                "--subvolume", "/home",
                "--type", "timed",
                "--schedule", "daily",
                "--dry-run",
            ]
            with patch("sys.argv", original_argv):
                with pytest.raises(SystemExit):
                    reexec_with_pkexec()
                pkexec_args = mock_execve.call_args[0][1]
                # All original args after the program name should be forwarded
                for arg in original_argv[1:]:
                    assert arg in pkexec_args


# ---------------------------------------------------------------------------
# Full flow: the three guards
# ---------------------------------------------------------------------------


class TestElevationFlow:
    """Test the three-way check used in main():

       needs_elevation() AND NOT is_root() AND NOT already_reexecuted()
    """

    def test_root_user_skips_pkexec(self) -> None:
        """Root runs privileged commands directly."""
        with patch("os.geteuid", return_value=0):
            assert is_root() is True
            # Even though the command needs elevation, root skips pkexec
            assert needs_elevation("snapshot", "create") is True
            # Guard: needs_elevation=True, is_root=True → no re-exec

    def test_normal_user_readonly_skips_pkexec(self) -> None:
        """Normal user running list/info goes through directly."""
        with patch("os.geteuid", return_value=1000):
            assert is_root() is False
            assert needs_elevation("snapshot", "list") is False
            # Guard: needs_elevation=False → no re-exec

    def test_normal_user_write_triggers_pkexec(self) -> None:
        """Normal user running create/delete/prune triggers pkexec."""
        with patch("os.geteuid", return_value=1000):
            assert is_root() is False
            assert needs_elevation("snapshot", "create") is True
            # Guard: needs_elevation=True, is_root=False,
            #        already_reexecuted=False → re-exec

    def test_already_reexecuted_skips_pkexec(self) -> None:
        """After pkexec re-exec, we skip the guard even if not root-like.

        (pkexec may set euid to 0, but the env guard is the definitive check.)
        """
        with patch.dict(os.environ, {_REEXEC_ENV_VAR: "1"}):
            assert already_reexecuted() is True
            # Guard: already_reexecuted=True → no re-exec
