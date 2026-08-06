# Configuration

Default location: `/etc/manaradix/config.toml` (TOML). If the file is
missing, built-in defaults apply; it is created when settings are first
saved from the GUI, the CLI, or a setup profile.

## `[global]`

| Key | Type | Default | Description |
|---|---|---|---|
| `default_subvolume` | string | `"/"` | Default subvolume path |
| `snapshot_dir` | string | `".snapshots"` | Snapshot storage directory relative to the mount point |
| `boot_from_snapshot` | bool | `false` | Enable the GRUB boot-from-snapshot menu |
| `snapshot_before_upgrade` | bool | `false` | Take a pre-upgrade snapshot of the root subvolume before every rpm package transaction |

## `[policies."<subvolume>"]`

One table per subvolume (the key is the subvolume path, quoted):

| Key | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Whether the policy is active |
| `schedule_hourly` | bool | `false` | Take hourly snapshots |
| `schedule_daily` | bool | `true` | Take daily snapshots |
| `schedule_weekly` | bool | `false` | Take weekly snapshots |
| `schedule_monthly` | bool | `false` | Take monthly snapshots |
| `schedule_boot` | bool | `false` | Snapshot on every boot |

## `[policies."<subvolume>".retention]`

| Key | Type | Default | Description |
|---|---|---|---|
| `keep_hourly` | int | `10` | Hourly snapshots to retain |
| `keep_daily` | int | `7` | Daily snapshots to retain |
| `keep_weekly` | int | `4` | Weekly snapshots to retain |
| `keep_monthly` | int | `3` | Monthly snapshots to retain |
| `keep_boot` | int | `5` | Boot snapshots to retain |
| `keep_pre_upgrade` | int | `3` | Pre-upgrade snapshots to retain |

## Example

```toml
[global]
default_subvolume = "/"
snapshot_dir = ".snapshots"
boot_from_snapshot = true
snapshot_before_upgrade = true

[policies."/"]
enabled = true
schedule_hourly = false
schedule_daily = true
schedule_weekly = true
schedule_monthly = false
schedule_boot = true

[policies."/".retention]
keep_hourly = 10
keep_daily = 7
keep_weekly = 4
keep_monthly = 3
keep_boot = 5
keep_pre_upgrade = 3

[policies."/home"]
enabled = true
schedule_daily = true
```

## Snapshot types

| Type | Description | Read-only |
|---|---|---|
| `manual` | User-created snapshot | No |
| `readonly` | User-created read-only snapshot | Yes |
| `timed` | Created by a systemd timer schedule | Yes |
| `boot` | Created at boot time | Yes |
| `pre-restore` | Safety snapshot taken before a restore | Yes |
| `pre-install` | Snapshot taken before package installation | Yes |
| `pre-upgrade` | Taken by the rpm file trigger before a package transaction | Yes |

## Setup profiles

Rather than hand-editing the config, the [setup wizard](profiles.md)
applies one of the shipped profiles (TOML templates):

| Profile | Role | Policies |
|---|---|---|
| `desktop` | desktop | `/` (daily, weekly, boot), `/home` (daily) |
| `server` | server | `/` (hourly, daily, weekly, monthly, boot), `/srv` (daily), `/var` (daily) |
| `kiosk` | any | `/` (boot only) |

Templates are searched in `$MANARADIX_TEMPLATE_DIRS` → repo
`data/templates/` → `/usr/share/manaradix/templates/` →
`/etc/manaradix/templates.d/` (later directories override by profile id).
See [docs/profiles.md](profiles.md) for the template schema.
