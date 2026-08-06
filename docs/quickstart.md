# Quick start

## Launch the GUI

```bash
manaradix
```

Or pick a specific frontend:

```bash
manaradix --frontend ncurses     # qt | gtk | ncurses
manaradix --debug                # enable debug logging
```

The GUI always re-executes via `pkexec` (polkit) so snapshot operations work
from a normal user session. By default the UI backend is auto-detected —
manatools recovers the display environment that pkexec strips, so the
elevated GUI can still reach your desktop (Qt > GTK > ncurses). The
`--frontend` flag maps to the `MUI_BACKEND` environment variable understood
by python-manatools; you can set it directly instead
(`MUI_BACKEND=ncurses manaradix`).

## First run: the setup wizard

When the system has no snapshots and no snapshot policies (a stock Mageia
btrfs install), the GUI opens on a **Setup Wizard** tab proposing a setup
profile for this system — desktop, server, or kiosk — auto-selected from
the detected role (graphical → desktop, headless → server) and switchable
from a dropdown. The plan is adapted to the actual layout: only btrfs
subvolumes get policies, and boot snapshots / the GRUB recovery menu are
only proposed when the root filesystem is btrfs (and GRUB is present).
Apply it in one click, or configure everything manually from Policies and
Settings.

The same setup is available headless:

```bash
# Is setup needed?
manaradix setup status

# Which profiles are available?
manaradix setup list-profiles

# Apply the default profile (config, policies, systemd units, GRUB script)
manaradix setup apply

# Apply a specific profile, or preview what would be applied
manaradix setup apply --profile server
manaradix setup apply --dry-run
```

Profiles are TOML templates: the shipped ones live in
`/usr/share/manaradix/templates/`, admin overrides and extras in
`/etc/manaradix/templates.d/`. See [Setup profiles](profiles.md).

## Snapshot operations from the CLI

```bash
# Manual read-only snapshot of /
manaradix snapshot create

# Timed daily snapshot (tagged for retention pruning)
manaradix snapshot create --subvolume / --type timed --schedule daily

# Read-write snapshot with a description
manaradix snapshot create --subvolume /home --type manual \
    --read-write --description "Before dist-upgrade"

# List snapshots on /
manaradix snapshot list

# Inspect a snapshot
manaradix snapshot info --id 2026-01-15_14-00-00

# Delete one (or preview the deletion)
manaradix snapshot delete --id 2026-01-15_14-00-00
manaradix snapshot delete --id 2026-01-15_14-00-00 --dry-run
```

Every destructive operation supports `--dry-run`. The full command
reference is in [CLI reference](cli.md).
