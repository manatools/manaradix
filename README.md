# Manaradix — Btrfs Snapshot Management for Mageia

A system utility for **Mageia 10** and **Cauldron** providing a unified
graphical and text interface for managing Btrfs snapshots, subvolumes,
and filesystem parameters. Built on top of `python-manatools` using the `aui`
(Abstract UI) backend so a single codebase renders in Qt, GTK, and ncurses
frontends.

Conceptually similar to openSUSE's `btrfsmaintenance` + `btrfs-assistant` and
Garuda's `btrfs-assistant`, but tailored to Mageia's infrastructure and
packaging conventions.

## System Requirements

| Dependency | Minimum | Notes |
|---|---|---|
| Python | 3.11+ | Core runtime |
| `btrfs-progs` | 6.7+ | `/usr/bin/btrfs` for all filesystem operations |
| `python-manatools` | Mageia 10 official repos | UI abstraction layer (optional — only needed for the GUI; includes the `aui` backend) |
| systemd | 255+ | Timers for automated snapshots |
| polkit | 0.120+ | Privilege escalation for non-root users |
| grub2 | — | Boot-from-snapshot support |

## Development Setup

A Python virtual environment is **required** for development. All commands
below assume an activated venv.

```bash
# Clone the repository
git clone <repo-url> manaradix
cd manaradix

# Create and activate a virtual environment.
# --system-site-packages is required: runtime deps such as python-manatools
# and btrfs-progs are system packages and must stay visible from the venv.
python -m venv --system-site-packages .venv
source .venv/bin/activate       # Linux / macOS
# .venv\Scripts\activate        # Windows (untested)

# Install in editable mode with dev tools
pip install -e ".[dev]"

# Verify the install
manaradix --help

# Run the test suite (208 tests)
pytest

# Run a single test file
pytest tests/test_snapshot_manager.py -v

# Run with coverage
pytest --cov=manaradix --cov-report=term
```

The tests use mocked subprocess calls — no live btrfs filesystem is required.

An integration suite in `tests/test_integration_btrfs.py` validates the
parsers and the full snapshot lifecycle (create, list, rename, prune,
browse, restore) against a real loopback btrfs filesystem.  It needs root
and is skipped in the normal run:

```bash
sudo -E .venv/bin/pytest tests/test_integration_btrfs.py -v
```
Python 3.11+ is the only hard Python dependency; `btrfs-progs`, `systemd`,
`polkit`, `python-manatools`, and `grub2` are system-level runtime
dependencies needed only when running the actual tool.  The UI renderer is
tested against a fake widget factory, so no Qt/GTK/curses install is needed
to run the suite.

If an existing venv predates the `--system-site-packages` switch, enable
system package visibility by setting `include-system-site-packages = true`
in `.venv/pyvenv.cfg` (no recreation needed).

### Python dependencies

| Package | Version | Purpose |
|---|---|---|
| `tomlkit` | ≥ 0.12.0 | TOML config file parsing (`/etc/manaradix/config.toml`) |
| `pytest` | ≥ 8.0 | Test runner (dev only) |
| `pytest-cov` | ≥ 5.0 | Coverage reporting (dev only) |
| `python-manatools` | Mageia 10 official repos | UI abstraction layer (optional — only needed for the GUI; `python3-manatools` on Mageia) |

The Overview page shows a dashboard with filesystem usage, a table of all
btrfs filesystems (label, uuid, devices, sizes, mount points), recent
snapshots, and active policies.

## Quick Start

```bash
# Activate the venv first
source .venv/bin/activate

# Launch the GUI (requires python-manatools)
manaradix

# Or pick a specific frontend
manaradix --frontend ncurses

# Enable debug logging
manaradix --debug

# Use a custom config path
manaradix --config /etc/manaradix/config.toml
```

The GUI always re-executes via `pkexec` (polkit) so snapshot operations work
from a normal user session.  By default the UI backend is auto-detected —
manatools recovers the display environment that pkexec strips, so the
elevated GUI can still reach your desktop (Qt > GTK > ncurses).  Pass
`--frontend {qt,gtk,ncurses}` to force a backend; graphical frontends are
probed first and fall back to ncurses with a warning if they cannot start.
The flag maps to the `MUI_BACKEND` environment variable understood by
python-manatools; you can set it directly instead
(`MUI_BACKEND=ncurses manaradix`).

## CLI Usage

When no subcommand is given, `manaradix` launches the GUI. Subcommands run
headless and are suitable for scripts and systemd service units.

### Create a snapshot

```bash
# Manual read-only snapshot of /
manaradix snapshot create

# Timed daily snapshot (tagged for retention pruning)
manaradix snapshot create --subvolume / --type timed --schedule daily

# Read-write snapshot with a description
manaradix snapshot create --subvolume /home --type manual \
    --read-write --description "Before dist-upgrade"

# Preview without executing
manaradix snapshot create --dry-run
```

### List snapshots

```bash
# All snapshots on /
manaradix snapshot list

# Filter by subvolume
manaradix snapshot list --subvolume /home
```

### Inspect a snapshot

```bash
manaradix snapshot info --id 2026-01-15_14-00-00
```

### Delete a snapshot

```bash
manaradix snapshot delete --id 2026-01-15_14-00-00

# Preview
manaradix snapshot delete --id 2026-01-15_14-00-00 --dry-run
```

### Prune old snapshots

The prune command uses the retention policy from your config file. It keeps
the N most recent snapshots for the given schedule and deletes the rest.

```bash
# Prune daily snapshots for /, keeping the most recent <keep_daily>
manaradix snapshot prune --subvolume / --schedule daily

# Preview what would be deleted
manaradix snapshot prune --subvolume / --schedule daily --dry-run
```

This is the command invoked automatically by the systemd timer units — you
normally don't run it by hand.

## Automated Snapshots (systemd Timers)

Manaradix can generate and manage systemd timer/service units that take
snapshots on a schedule and automatically prune old ones.

1. **Create a config file** at `/etc/manaradix/config.toml`:

   ```toml
   [global]
   default_subvolume = "/"
   snapshot_dir = ".snapshots"
   boot_from_snapshot = false

   [policies."/"]
   enabled = true
   schedule_hourly = false
   schedule_daily = true
   schedule_weekly = false
   schedule_monthly = false
   schedule_boot = false

   [policies."/".retention]
   keep_hourly = 10
   keep_daily = 7
   keep_weekly = 4
   keep_monthly = 3
   keep_boot = 5
   ```

2. **Apply the policy** — this writes systemd units and enables the timers:

   ```python
   from manaradix.core.policy_engine import PolicyEngine
   from manaradix.core.config_store import ConfigStore
   from manaradix.model.policy import SnapshotPolicy

   store = ConfigStore()
   store.load()
   policy = store.get_policy("/")
   engine = PolicyEngine()
   engine.apply_policy(policy)
   ```

   Or from the GUI: **Policies → select a subvolume → Apply Policy**.

The generated units (under `/etc/systemd/system/`) follow the naming pattern
`manaradix-snapshot-<subvol>-<schedule>.{service,timer}` and invoke the CLI
subcommands:

```
manaradix snapshot create --subvolume <X> --type timed --schedule <S>
manaradix snapshot prune --subvolume <X> --schedule <S>
```

Boot snapshots use a oneshot service (`WantedBy=multi-user.target`) instead
of a timer, firing once per boot.

## Boot-from-Snapshot (GRUB Integration)

Manaradix can install a GRUB script (`/etc/grub.d/41_manaradix`) that adds a
"Manaradix Snapshots" submenu to the bootloader, listing read-only snapshots
as bootable entries.

```bash
# From the GUI: Settings → GRUB Boot Menu → Install GRUB Script

# Or programmatically:
from manaradix.core.grub_integration import GrubIntegration
grub = GrubIntegration()
grub.install_script()
grub.update_grub()
```

Each entry boots with `rootflags=subvol=.snapshots/<id>/root ro`, giving you
a read-only recovery environment.

## Snapshot Storage Convention

Snapshots are stored under `<mount>/.snapshots/<timestamp>/<subvol-name>/`
with a JSON sidecar at `info.json`:

```
/.snapshots/
└── 2026-01-15_14-00-00/
    ├── info.json          # {"id": "...", "subvolume": "/", "type": "timed", ...}
    └── root/              # The actual btrfs snapshot subvolume
```

## Configuration Reference

Default location: `/etc/manaradix/config.toml` (TOML format).

### `[global]`

| Key | Type | Default | Description |
|---|---|---|---|
| `default_subvolume` | string | `"/"` | Default subvolume path |
| `snapshot_dir` | string | `".snapshots"` | Snapshot storage relative to mount |
| `boot_from_snapshot` | bool | `false` | Enable GRUB snapshot boot menu |

### `[policies."<subvolume>"]`

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Whether the policy is active |
| `schedule_hourly` | bool | `false` | Take hourly snapshots |
| `schedule_daily` | bool | `true` | Take daily snapshots |
| `schedule_weekly` | bool | `false` | Take weekly snapshots |
| `schedule_monthly` | bool | `false` | Take monthly snapshots |
| `schedule_boot` | bool | `false` | Snapshot on every boot |

### `[policies."<subvolume>".retention]`

| Key | Type | Default | Description |
|---|---|---|---|
| `keep_hourly` | int | `10` | Hourly snapshots to retain |
| `keep_daily` | int | `7` | Daily snapshots to retain |
| `keep_weekly` | int | `4` | Weekly snapshots to retain |
| `keep_monthly` | int | `3` | Monthly snapshots to retain |
| `keep_boot` | int | `5` | Boot snapshots to retain |

## Snapshot Types

| Type | Description | Read-only |
|---|---|---|
| `manual` | User-created snapshot | No |
| `readonly` | User-created read-only snapshot | Yes |
| `timed` | Created by a systemd timer schedule | Yes |
| `boot` | Created at boot time | Yes |
| `pre-restore` | Safety snapshot taken before a restore | Yes |
| `pre-install` | Snapshot taken before package installation | Yes |

## Polkit Privilege Model

Four action levels under the `org.mageia.manaradix.*` namespace:

| Action | Who can perform | Description |
|---|---|---|
| `read` | Any user | Query btrfs info (list snapshots, usage) |
| `manage-snapshots` | `auth_admin_keep` | Create, delete, restore, export snapshots |
| `manage-policies` | `auth_admin_keep` | Modify systemd timer units |
| `manage-filesystem` | `auth_admin_keep` | Scrub, balance, defrag, compression |

The policy file is installed at
`/usr/share/polkit-1/actions/org.mageia.manaradix.policy`.

## Project Structure

```
manaradix/
├── pyproject.toml
├── README.md
├── spec.md                          # Full technical specification
├── data/
│   ├── polkit/                      # Polkit policy XML
│   └── desktop/                     # .desktop launcher
├── manaradix/
│   ├── __init__.py
│   ├── module.py                    # AppContext, run_app(), main() CLI
│   ├── model/                       # Data classes: Snapshot, Subvolume, Policy
│   ├── core/                        # Business logic (no UI imports)
│   │   ├── btrfs_ops.py             # /usr/bin/btrfs CLI wrapper
│   │   ├── snapshot_manager.py      # Snapshot lifecycle + journal
│   │   ├── policy_engine.py         # systemd unit generation
│   │   ├── grub_integration.py      # /etc/grub.d/41_manaradix
│   │   └── config_store.py          # /etc/manaradix/config.toml
│   ├── ui/                          # Declarative UI (toolkit-agnostic)
│   │   ├── navigation.py
│   │   ├── model.py                 # PageModel / Widget descriptor factories
│   │   ├── renderer.py              # Descriptor → manatools YUI widgets
│   │   └── pages/
│   │       ├── overview.py          # Dashboard
│   │       ├── snapshots.py         # Snapshot management
│   │       ├── policies.py          # Policy editor
│   │       └── settings.py          # Global settings + maintenance
│   └── utils/
└── tests/
    ├── test_models.py
    ├── test_btrfs_ops.py
    ├── test_snapshot_manager.py
    ├── test_config_store.py
    ├── test_policy_engine.py
    ├── test_grub_integration.py
    ├── test_module.py
    ├── test_pkexec.py
    ├── test_ui_model.py
    └── test_ui_renderer.py
```

## Architecture Principles

- **Frontend-agnostic** — Business logic lives in pure-Python modules with
  zero UI imports. The `aui` layer reads model objects and dispatches events.
- **CLI-first** — Every operation is a library call; the GUI and CLI entry
  point are thin shells over the same core services.
- **Idempotent** — Re-running a config apply produces the same state.
- **Dry-run** — All destructive operations support `dry_run=True`.
- **Journaled** — `SnapshotManager` keeps an in-memory journal of applied
  changes for potential rollback.

## Translations

User-visible strings are marked with `_()` for gettext translation.

```bash
# Generate / update the template
xgettext --language=Python --keyword=_ --from-code=UTF-8 \
    --output=locale/manaradix.pot \
    --package-name=manaradix --package-version=0.1.0 \
    manaradix/module.py manaradix/utils/pkexec.py manaradix/i18n.py \
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

An Italian translation ships at `locale/it.po`; other languages are welcome.
The translation template is at `locale/manaradix.pot`.  System-installed
``.mo`` files go to ``/usr/share/locale/<lang>/LC_MESSAGES/manaradix.mo``,
compiled and installed by the RPM spec (``msgfmt`` + ``%find_lang``).

## License

GPL-2.0-or-later — see [LICENSE](LICENSE).
