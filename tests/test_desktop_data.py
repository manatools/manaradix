"""Desktop entry + AppStream metainfo consistency with the app id."""

from __future__ import annotations

import configparser
import xml.etree.ElementTree as ET
from pathlib import Path

from manaradix.core.assets import APP_ID

DATA = Path(__file__).resolve().parents[1] / "data"
DESKTOP = DATA / "desktop" / f"{APP_ID}.desktop"
METAINFO = DATA / "metainfo" / f"{APP_ID}.metainfo.xml"
SPEC = Path(__file__).resolve().parents[1] / "manaradix.spec"


def _desktop_entry() -> configparser.SectionProxy:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # keep Key[lang] case
    parser.read(DESKTOP, encoding="utf-8")
    return parser["Desktop Entry"]


def test_app_id_is_reverse_dns() -> None:
    assert APP_ID == "org.mageia.manaradix"


def test_desktop_file_named_after_app_id() -> None:
    assert DESKTOP.is_file()
    assert not (DATA / "desktop" / "manaradix.desktop").exists()
    entry = _desktop_entry()
    assert entry["Exec"] == "manaradix"
    assert entry["Icon"] == "manaradix"


def test_metainfo_matches_desktop_file() -> None:
    root = ET.parse(METAINFO).getroot()
    assert root.get("type") == "desktop-application"
    assert root.findtext("id") == APP_ID
    launchable = root.find("launchable")
    assert launchable is not None
    assert launchable.get("type") == "desktop-id"
    assert launchable.text == DESKTOP.name
    assert root.findtext("provides/binary") == "manaradix"


def test_metainfo_release_matches_version() -> None:
    from manaradix import __version__

    root = ET.parse(METAINFO).getroot()
    versions = [r.get("version") for r in root.iterfind("releases/release")]
    assert versions and versions[0] == __version__


def test_spec_installs_both_files() -> None:
    spec = SPEC.read_text(encoding="utf-8")
    assert f"%{{_datadir}}/applications/{APP_ID}.desktop" in spec
    assert f"%{{_metainfodir}}/{APP_ID}.metainfo.xml" in spec
