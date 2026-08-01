"""Path resolution for shipped manaradix assets (logo, ...).

Assets live in ``data/images/`` in the repository and are installed to
``/usr/share/manaradix/images/`` by the RPM spec.  Like the setup
profiles (``core/templates.py``), the repo checkout wins when running
from a development tree.
"""

from __future__ import annotations

from pathlib import Path

_LOGO_NAME = "manaradix.svg"
_LOGO_PNG_NAME = "manaradix.png"

#: Repo checkout location of the shipped logo (editable installs).
_REPO_IMAGES = Path(__file__).resolve().parents[2] / "data" / "images"

SYSTEM_IMAGE_DIR = Path("/usr/share/manaradix/images")


def _asset_path(name: str) -> str | None:
    for directory in (_REPO_IMAGES, SYSTEM_IMAGE_DIR):
        candidate = directory / name
        if candidate.is_file():
            return str(candidate)
    return None


def logo_path() -> str | None:
    """Absolute path to the manaradix logo (SVG), or None when not shipped.

    The SVG is used for the application/window icon.
    """
    return _asset_path(_LOGO_NAME)


def logo_png_path() -> str | None:
    """Absolute path to a rasterized logo (PNG), or None when not shipped.

    The manatools Qt image widget renders vector icons at a fixed 32×32
    (SVG icons expose no ``availableSizes``), so widgets that need the
    logo at its real size use the PNG raster instead.
    """
    return _asset_path(_LOGO_PNG_NAME)
