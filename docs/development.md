# Development

## Setup

A Python virtual environment is **required** for development.
`--system-site-packages` is required: runtime deps such as
`python-manatools` and `btrfs-progs` are system packages and must stay
visible from the venv.

```bash
git clone <repo-url> manaradix
cd manaradix

python -m venv --system-site-packages .venv
source .venv/bin/activate

# Editable install with dev tools (pytest, coverage, interrogate)
pip install -e ".[dev]"

# Docs tooling (mkdocs + mkdocstrings)
pip install -e ".[docs]"

# Verify
manaradix --help
```

## Tests

```bash
pytest                              # full suite (561 tests, mocked subprocess)
pytest tests/test_btrfs_ops.py -v   # a single file
pytest --cov=manaradix --cov-report=term
```

The tests use mocked subprocess calls — no live btrfs filesystem is
required. An integration suite in `tests/test_integration_btrfs.py`
validates the parsers and the full snapshot lifecycle against a real
loopback btrfs filesystem; it needs root and is skipped in the normal run:

```bash
sudo -E .venv/bin/pytest tests/test_integration_btrfs.py -v
```

Docstring coverage is enforced with `interrogate`:

```bash
interrogate manaradix
```

## Documentation

The site lives in `docs/` (MkDocs + Material theme, API reference via
mkdocstrings):

```bash
mkdocs serve        # live preview at http://127.0.0.1:8000
mkdocs build        # static site into site/
```

The full technical specification is `spec.md`; design docs for individual
subsystems live under `docs/` (see the [documentation map](index.md)).

## Translations

User-visible strings are marked with `_()` for gettext translation.

```bash
# Generate / update the template
xgettext --language=Python --keyword=_ --from-code=UTF-8 \
    --output=locale/manaradix.pot \
    --package-name=manaradix --package-version=0.2.2 \
    manaradix/module.py manaradix/utils/pkexec.py manaradix/i18n.py \
    manaradix/core/*.py manaradix/dbus/*.py manaradix/tray/*.py \
    manaradix/ui/renderer.py manaradix/ui/navigation.py \
    manaradix/ui/pages/*.py

# Create a new translation (example: Spanish)
msginit --locale=es --input=locale/manaradix.pot \
    --output-file=locale/es.po

# Update an existing translation after regenerating the template
msgmerge --update locale/it.po locale/manaradix.pot

# Compile a translation for testing
msgfmt locale/it.po -o locale/it/LC_MESSAGES/manaradix.mo

# Test with a specific locale
LANG=it_IT.UTF-8 MANARADIX_LOCALE_DIR=./locale manaradix snapshot list
```

Italian and Chinese (simplified) translations ship at `locale/it.po` and
`locale/zh_CN.po`; other languages are welcome.  The test suite enforces
that every pot entry is translated in the .po files, that they compile
cleanly (`msgfmt --check`), and spot-checks actual lookups from compiled
.mo files (`tests/test_i18n.py`).  System-installed `.mo` files go to
`/usr/share/locale/<lang>/LC_MESSAGES/manaradix.mo`, compiled and
installed by the RPM spec (`msgfmt` + `%find_lang`).

Mageia contributors translate manaradix on
[Transifex](https://explore.transifex.com/MageiaLinux/mageia/) (resource
`manaradix` in the `mageia` project, same as dnfdragora and manafirewall).
`locale/.tx/config` maps the resource to `locale/<lang>.po`; with the
[tx CLI](https://developers.transifex.com/docs/cli):

```bash
cd locale
tx push -s          # upload manaradix.pot after regenerating it
tx pull -a          # fetch every translation into locale/<lang>.po
```

The RPM spec compiles every `locale/*.po`, so pulled languages ship
without spec changes.

Desktop-file strings (`Name`, `GenericName`, `Comment`, `Keywords` of
`data/desktop/*.desktop`) are a second resource, `manaradix_desktop`
(`locale/desktop/.tx/config`), handled like dnfdragora's: the
translations are written straight into the committed desktop files by
`tools/desktoppo.py` (needs `polib`, in the `dev` extra), so nothing runs
at build time.

```bash
cd locale/desktop && tx pull -a && cd ../..   # fetch locale/desktop/<lang>.po
python3 tools/desktoppo.py -d data/desktop    # regenerate the pot, merge the
                                              # .po files, add Key[lang]= lines
cd locale/desktop && tx push -s               # upload the pot after a desktop
                                              # string changes
```

## Packaging (Mageia)

```bash
bm -sl manaradix.spec       # SRPM packaging (source-only)
iurt                        # clean-chroot build
rpmlint RPMS/*.rpm          # lint checks before submission
mgarepo submit              # submit to Mageia Cauldron
```

## Architecture principles

- **Frontend-agnostic** — business logic lives in pure-Python modules
  with zero UI imports; the `aui` layer reads model objects and dispatches
  events.
- **CLI-first** — every operation is a library call; the GUI and CLI entry
  point are thin shells over the same core services.
- **Idempotent** — re-running a config apply produces the same state.
- **Dry-run** — all destructive operations support `dry_run=True`.
- **Journaled** — `SnapshotManager` keeps an in-memory journal of applied
  changes for potential rollback.
