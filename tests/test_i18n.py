"""Tests for the gettext i18n scaffolding.

The translation lookup is exercised in fresh interpreter processes so
gettext's per-domain cache cannot leak state between tests.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_LOCALE = Path(__file__).resolve().parent.parent / "locale"

pytestmark = pytest.mark.skipif(
    shutil.which("msgfmt") is None, reason="msgfmt (gettext) not available"
)


class TestPoValidity:
    @staticmethod
    def _normalize(text: str) -> str:
        """Join adjacent gettext string-literal lines (msgid/msgstr continuations)."""
        return re.sub(r'"\s*\n\s*"', "", text)

    def test_po_covers_all_pot_entries(self) -> None:
        pot = self._normalize((REPO_LOCALE / "manaradix.pot").read_text())
        po = self._normalize((REPO_LOCALE / "it.po").read_text())

        pot_ids = set(re.findall(r'^msgid "(.*)"$', pot, re.M)) - {""}
        po_ids = set(re.findall(r'^msgid "(.*)"$', po, re.M))
        po_strs = set(re.findall(r'^msgstr "(.*)"$', po, re.M)) - {""}

        missing = pot_ids - po_ids
        assert not missing, f"msgids missing from it.po: {missing}"
        assert len(po_strs) >= len(pot_ids), "some entries are untranslated"

    def test_po_compiles_cleanly(self, tmp_path) -> None:
        mo = tmp_path / "manaradix.mo"
        proc = subprocess.run(
            ["msgfmt", "--check", "-o", str(mo), str(REPO_LOCALE / "it.po")],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert mo.exists()


def _translate(locale_dir: Path, message: str) -> str:
    """Return the translation of *message* from a fresh interpreter."""
    env = {
        **os.environ,
        "LANG": "it_IT.UTF-8",
        "MANARADIX_LOCALE_DIR": str(locale_dir),
        "PYTHONIOENCODING": "utf-8",
    }
    proc = subprocess.run(
        [
            "python3", "-c",
            f"from manaradix.i18n import _; import sys; sys.stdout.write(_({message!r}))",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


class TestTranslationLookup:
    def test_translates_with_compiled_mo(self, tmp_path) -> None:
        mo_dir = tmp_path / "it" / "LC_MESSAGES"
        mo_dir.mkdir(parents=True)
        subprocess.run(
            ["msgfmt", "-o", str(mo_dir / "manaradix.mo"), str(REPO_LOCALE / "it.po")],
            check=True,
        )
        assert _translate(tmp_path, "No snapshots found.") == "Nessuno snapshot trovato."
        assert _translate(tmp_path, "Deleted snapshot: %s") == "Snapshot eliminato: %s"

    def test_fallback_identity_without_mo(self, tmp_path) -> None:
        assert _translate(tmp_path, "No snapshots found.") == "No snapshots found."
