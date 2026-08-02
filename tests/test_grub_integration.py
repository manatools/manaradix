"""Unit tests for GrubIntegration."""

import os
import stat
from unittest.mock import MagicMock, patch

import pytest

from manaradix.core.grub_integration import (
    GrubIntegration,
    GrubIntegrationError,
)


# ---------------------------------------------------------------------------
# generate_grub_script
# ---------------------------------------------------------------------------


class TestGenerateGrubScript:
    def test_default_paths(self) -> None:
        grub = GrubIntegration()
        script = grub.generate_grub_script()
        assert "#!/bin/sh" in script
        assert 'SNAPSHOT_DIR="/.snapshots"' in script
        assert "submenu" in script
        assert "41_manaradix" in script

    def test_custom_snapshot_dir(self) -> None:
        grub = GrubIntegration()
        script = grub.generate_grub_script(
            snapshot_dir="/mnt/data/.snapshots",
            subvol_name="home",
        )
        assert 'SNAPSHOT_DIR="/mnt/data/.snapshots"' in script
        assert 'home' in script
        # relpath should strip leading /
        assert "mnt/data/.snapshots" in script

    def test_includes_menu_structure(self) -> None:
        grub = GrubIntegration()
        script = grub.generate_grub_script()
        assert 'submenu "Manaradix Snapshots" {{' in script.lower() or \
               'submenu "Manaradix Snapshots" {' in script
        assert "menuentry" in script
        assert "rootflags=subvol=" in script
        assert "findmnt" in script

    def test_strips_leading_slash_for_relpath(self) -> None:
        grub = GrubIntegration()
        script = grub.generate_grub_script(snapshot_dir="/.snapshots")
        # relpath is inlined into the snap_path bash variable
        # (e.g. snap_path=".snapshots/${snap_id}/root")
        assert 'snap_path=".snapshots/${snap_id}/root"' in script.replace(" ", "")

    def test_submenu_guarded_by_snapshot_count(self) -> None:
        """Empty snapshot dir must not emit an empty submenu block.

        GRUB's lexer rejects an empty ``submenu { }`` with "Incorrect
        command" / syntax error, breaking update-grub entirely.
        """
        grub = GrubIntegration()
        script = grub.generate_grub_script()

        open_guard = 'if [ "$count" -eq 0 ]; then'
        close_guard = 'if [ "$count" -gt 0 ]; then'
        assert open_guard in script
        assert close_guard in script
        # submenu open comes after its guard; close after the loop
        assert script.index(open_guard) < script.index('submenu "Manaradix Snapshots"')
        assert script.index(close_guard) > script.index("done")
        assert script.index('submenu "Manaradix Snapshots"') < script.index("done")


# ---------------------------------------------------------------------------
# install_script
# ---------------------------------------------------------------------------


class TestInstallScript:
    def test_dry_run_does_not_write(self, tmp_path) -> None:
        script_path = str(tmp_path / "41_test")
        grub = GrubIntegration(script_path=script_path)
        grub.install_script(dry_run=True)
        assert not os.path.exists(script_path)

    def test_writes_and_makes_executable(self, tmp_path) -> None:
        script_path = str(tmp_path / "41_test")
        grub = GrubIntegration(script_path=script_path)

        grub.install_script()

        assert os.path.isfile(script_path)

        content = open(script_path).read()
        assert "#!/bin/sh" in content
        assert "41_manaradix" in content

        # Should be executable
        file_stat = os.stat(script_path)
        assert file_stat.st_mode & stat.S_IXUSR
        assert file_stat.st_mode & stat.S_IXGRP
        assert file_stat.st_mode & stat.S_IXOTH

    def test_creates_parent_directory(self, tmp_path) -> None:
        script_path = str(tmp_path / "subdir" / "41_test")
        grub = GrubIntegration(script_path=script_path)

        grub.install_script()

        assert os.path.isfile(script_path)

    def test_custom_subvol_name(self, tmp_path) -> None:
        script_path = str(tmp_path / "41_test")
        grub = GrubIntegration(script_path=script_path)

        grub.install_script(subvol_name="home")

        content = open(script_path).read()
        assert "home" in content
        # The for loop checks for the subvol dir
        assert "subvol_name}" in content or "home" in content


# ---------------------------------------------------------------------------
# uninstall_script
# ---------------------------------------------------------------------------


class TestUninstallScript:
    def test_removes_installed_script(self, tmp_path) -> None:
        script_path = str(tmp_path / "41_test")
        grub = GrubIntegration(script_path=script_path)
        grub.install_script()
        assert os.path.isfile(script_path)

        grub.uninstall_script()
        assert not os.path.exists(script_path)

    def test_dry_run_does_not_remove(self, tmp_path) -> None:
        script_path = str(tmp_path / "41_test")
        grub = GrubIntegration(script_path=script_path)
        grub.install_script()

        grub.uninstall_script(dry_run=True)
        assert os.path.isfile(script_path)

    def test_no_error_when_not_installed(self) -> None:
        grub = GrubIntegration(script_path="/nonexistent/path/41_grub")
        # Should not raise
        grub.uninstall_script()


# ---------------------------------------------------------------------------
# is_installed
# ---------------------------------------------------------------------------


class TestIsInstalled:
    def test_false_when_not_installed(self, tmp_path) -> None:
        script_path = str(tmp_path / "41_nonexistent")
        grub = GrubIntegration(script_path=script_path)
        assert grub.is_installed() is False

    def test_true_when_installed(self, tmp_path) -> None:
        script_path = str(tmp_path / "41_test")
        grub = GrubIntegration(script_path=script_path)
        grub.install_script()
        assert grub.is_installed() is True

    def test_false_when_not_executable(self, tmp_path) -> None:
        script_path = str(tmp_path / "41_test")
        grub = GrubIntegration(script_path=script_path)

        # Write file without making it executable
        os.makedirs(os.path.dirname(script_path), exist_ok=True)
        with open(script_path, "w") as fh:
            fh.write("#!/bin/sh\necho test\n")
        # Remove execute bits
        os.chmod(script_path, stat.S_IRUSR | stat.S_IWUSR)

        assert grub.is_installed() is False


# ---------------------------------------------------------------------------
# update_grub
# ---------------------------------------------------------------------------


class TestUpdateGrub:
    @patch("subprocess.run")
    def test_uses_update_grub_when_present(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with patch("os.path.exists", return_value=True):
            grub = GrubIntegration()
            grub.update_grub()
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args[0] == "/usr/sbin/update-grub"

    @patch("subprocess.run")
    def test_falls_back_to_grub2_mkconfig(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with patch("os.path.exists", return_value=False):
            grub = GrubIntegration()
            grub.update_grub()
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert "grub2-mkconfig" in args

    def test_dry_run_does_not_execute(self) -> None:
        grub = GrubIntegration()
        # Should not raise — just logs
        grub.update_grub(dry_run=True)

    @patch("subprocess.run")
    def test_custom_bin(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with patch("os.path.exists", return_value=True):
            grub = GrubIntegration(update_grub_bin="/usr/local/bin/my-update-grub")
            grub.update_grub()
            args = mock_run.call_args[0][0]
            assert args[0] == "/usr/local/bin/my-update-grub"

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_file_not_found_raises(self, mock_run: MagicMock) -> None:
        with patch("os.path.exists", return_value=False):
            grub = GrubIntegration()
            with pytest.raises(GrubIntegrationError, match="Neither update-grub"):
                grub.update_grub()

    @patch("subprocess.run")
    def test_called_process_error_raises(self, mock_run: MagicMock) -> None:
        import subprocess

        mock_run.side_effect = subprocess.CalledProcessError(
            1, "grub-mkconfig", stderr=b"error message"
        )
        with patch("os.path.exists", return_value=True):
            grub = GrubIntegration()
            with pytest.raises(GrubIntegrationError, match="update-grub failed"):
                grub.update_grub()


# ---------------------------------------------------------------------------
# has_system_grub
# ---------------------------------------------------------------------------


class TestHasSystemGrub:
    def test_grub2_bios_cfg(self, tmp_path) -> None:
        grub = GrubIntegration()
        os.makedirs(tmp_path / "boot" / "grub2")
        (tmp_path / "boot" / "grub2" / "grub.cfg").write_text("menu")
        assert grub.has_system_grub(str(tmp_path)) is True

    def test_grub_bios_cfg(self, tmp_path) -> None:
        grub = GrubIntegration()
        os.makedirs(tmp_path / "boot" / "grub")
        (tmp_path / "boot" / "grub" / "grub.cfg").write_text("menu")
        assert grub.has_system_grub(str(tmp_path)) is True

    def test_efi_cfg(self, tmp_path) -> None:
        grub = GrubIntegration()
        os.makedirs(tmp_path / "boot" / "efi" / "EFI" / "mageia")
        (tmp_path / "boot" / "efi" / "EFI" / "mageia" / "grub.cfg").write_text("menu")
        assert grub.has_system_grub(str(tmp_path)) is True

    def test_no_grub_config(self, tmp_path) -> None:
        grub = GrubIntegration()
        assert grub.has_system_grub(str(tmp_path)) is False

    def test_default_prefix_probes_root(self) -> None:
        # Without a prefix, real system paths are probed; on a machine
        # without GRUB this is False — the method must never raise.
        grub = GrubIntegration()
        result = grub.has_system_grub()
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# list_snapshot_entries
# ---------------------------------------------------------------------------


class TestListSnapshotEntries:
    def test_empty_when_not_installed(self, tmp_path) -> None:
        script_path = str(tmp_path / "41_nonexistent")
        grub = GrubIntegration(script_path=script_path)
        assert grub.list_snapshot_entries() == []

    def test_returns_directory_entries(self, tmp_path) -> None:
        script_path = str(tmp_path / "41_test")
        grub = GrubIntegration(script_path=script_path)
        grub.install_script()

        # list_snapshot_entries scans /.snapshots, not the script.
        # Test with a real (temp) snapshot directory.
        with patch("os.listdir", return_value=["snap1", "snap2"]):
            with patch("os.path.isdir", return_value=True):
                entries = grub.list_snapshot_entries()
                assert "snap1" in entries
                assert "snap2" in entries

    def test_oserror_returns_empty(self, tmp_path) -> None:
        script_path = str(tmp_path / "41_test")
        grub = GrubIntegration(script_path=script_path)
        grub.install_script()

        with patch("os.listdir", side_effect=OSError):
            entries = grub.list_snapshot_entries()
            assert entries == []

    def test_unreadable_script_returns_empty(self, tmp_path) -> None:
        script_path = str(tmp_path / "41_test")
        grub = GrubIntegration(script_path=script_path)
        grub.install_script()

        with patch("os.access", return_value=True):
            with patch("os.path.isfile", return_value=True):
                with patch("builtins.open", side_effect=OSError):
                    entries = grub.list_snapshot_entries()
                    assert entries == []
