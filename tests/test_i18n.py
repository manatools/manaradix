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
    def _parse(text: str) -> dict[str, str]:
        """Parse a .po/.pot into {msgid: msgstr}, multiline-aware."""
        lines = text.splitlines()
        entries: dict[str, str] = {}
        i, n = 0, len(lines)
        while i < n:
            if lines[i].startswith("msgid"):
                mid = [lines[i][6:]] if lines[i].startswith("msgid ") else []
                i += 1
                while i < n and lines[i].startswith('"'):
                    mid.append(lines[i])
                    i += 1
                mstr: list[str] = []
                if i < n and lines[i].startswith("msgstr"):
                    rest = lines[i][7:]
                    if rest.strip():
                        mstr.append(rest)
                    i += 1
                    while i < n and lines[i].startswith('"'):
                        mstr.append(lines[i])
                        i += 1
                entries["".join(mid)] = "".join(mstr)
            else:
                i += 1

        def unquote(s: str) -> str:
            return "".join(re.findall(r'"([^"]*)"', s))

        return {unquote(k): unquote(v) for k, v in entries.items()}

    def test_po_covers_all_pot_entries(self) -> None:
        pot = self._parse((REPO_LOCALE / "manaradix.pot").read_text())
        po = self._parse((REPO_LOCALE / "it.po").read_text())

        missing = set(pot) - set(po)
        assert not missing, f"msgids missing from it.po: {missing}"
        untranslated = [k for k in pot if not po.get(k)]
        assert not untranslated, f"untranslated entries: {untranslated}"

    def test_po_compiles_cleanly(self, tmp_path) -> None:
        mo = tmp_path / "manaradix.mo"
        proc = subprocess.run(
            ["msgfmt", "--check", "-o", str(mo), str(REPO_LOCALE / "it.po")],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert mo.exists()


class TestTemplateMetadata:
    """The shipped setup-profile name/description strings live in TOML
    files that xgettext cannot scan; manaradix/core/template_strings.py
    anchors them.  The pot must cover them and the po must translate
    them."""

    @staticmethod
    def _template_metadata() -> list[str]:
        import tomlkit

        repo_root = Path(__file__).resolve().parent.parent
        metadata: list[str] = []
        for toml_path in sorted((repo_root / "data" / "templates").glob("*.toml")):
            doc = tomlkit.parse(toml_path.read_text(encoding="utf-8"))
            for key in ("name", "description"):
                value = doc.get(key)
                if value:
                    metadata.append(str(value))
        return metadata

    def test_metadata_present_in_pot(self) -> None:
        pot = TestPoValidity._parse((REPO_LOCALE / "manaradix.pot").read_text())
        missing = [m for m in self._template_metadata() if m not in pot]
        assert not missing, f"template metadata missing from pot: {missing}"

    def test_metadata_translated_in_po(self) -> None:
        po = TestPoValidity._parse((REPO_LOCALE / "it.po").read_text())
        untranslated = [m for m in self._template_metadata() if not po.get(m)]
        assert not untranslated, f"untranslated template metadata: {untranslated}"


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
