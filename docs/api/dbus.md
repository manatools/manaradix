# D-Bus interface

A system-bus service exposes the core operations programmatically,
authorizing each call against the four polkit action tiers. The daemon
runs as root and is **bus-activated on demand** (systemd unit +
`/usr/share/dbus-1/system-services/` descriptor); it is never enabled at
boot and starts only when first called.

| Constant | Value |
|---|---|
| Service name | `org.mageia.manaradix` |
| Object path | `/org/mageia/manaradix` |
| Interface | `org.mageia.manaradix` |

## Errors

Failed calls raise D-Bus errors named `org.mageia.manaradix.Error.<Kind>`:

| Kind | Meaning |
|---|---|
| `AuthRequired` | The caller is not authorized for the polkit action |
| `NotFound` | The referenced snapshot/subvolume does not exist |
| `InvalidArgs` | Arguments failed validation |
| `Failed` | The underlying operation failed |

The `manaradix.dbus.client` module wraps the bus for Python callers,
translating these names into typed exceptions and offering a
`daemon_running()` probe that never activates the service.

## Methods by polkit tier

Every method maps 1:1 to a core library call (the daemon is a thin shell
over the same services as the CLI). Signatures and parameter types are
documented in the Python API reference
([`manaradix.dbus.wire`](../api/python.md)).

### Tier 1 — read (no authentication)

| Method | Purpose |
|---|---|
| `Ping` | Service version (used as a liveness check) |
| `ListSnapshots` | List snapshots for a mount point |
| `GetSnapshot` | Details of one snapshot |
| `ListSubvolumes` | List btrfs subvolumes |
| `FilesystemUsage` | `btrfs filesystem usage` per mount |
| `FilesystemShow` | All btrfs filesystems with devices and sizes |
| `BtrfsMounts` | btrfs mount points |
| `BalanceStatus` | Balance progress |
| `ScrubStatus` | Scrub progress |
| `GetCompression` | Compression setting of a subvolume |
| `ListPolicies` | Snapshot policies from the config |
| `GetConfig` | Global configuration |
| `SetupStatus` | Whether the setup wizard is needed |
| `ListSetupProfiles` | Available setup profiles |
| `GrubIsInstalled` | Whether the GRUB script is installed |
| `GrubListEntries` | Snapshot entries in the GRUB script |
| `GetDeviceStats` | Merged per-device health report: the five btrfs error counters plus `smart_*` fields when UDisks2 provides SMART data (used by the tray applet) |

### Tier 2 — manage snapshots (`auth_admin_keep`)

| Method | Purpose |
|---|---|
| `CreateSnapshot` | Create a snapshot |
| `DeleteSnapshot` | Delete a snapshot |
| `PruneSnapshots` | Prune a retention tier |
| `RenameSnapshot` | Rename / update a snapshot description |
| `RestoreSnapshot` | Restore (swap method) |
| `BrowseSnapshot` | Mount a snapshot read-only for browsing |
| `Unbrowse` | Unmount a browsed snapshot |
| `ExportSnapshot` | `btrfs send` |
| `ImportSnapshot` | `btrfs receive` |

### Tier 3 — manage policies (`auth_admin_keep`)

| Method | Purpose |
|---|---|
| `ApplyPolicy` | Apply a snapshot policy (writes systemd units) |
| `RemovePolicy` | Remove a policy |
| `SetConfig` | Update global configuration |
| `SetupApply` | Apply a setup profile |
| `GrubInstall` | Install the GRUB script |
| `GrubUninstall` | Remove the GRUB script |
| `GrubUpdate` | Regenerate the GRUB configuration |

### Tier 4 — manage filesystem (`auth_admin_keep`)

| Method | Purpose |
|---|---|
| `BalanceStart` / `BalanceCancel` | Start / cancel a balance |
| `ScrubStart` / `ScrubCancel` | Start / cancel a scrub |
| `Defragment` | Defragment a subvolume |
| `SetCompression` | Change compression of a subvolume |
| `CreateSubvolume` | Create a subvolume |
| `DeleteSubvolume` | Delete a subvolume |
| `SetDefaultSubvolume` | Set the default subvolume |

## Calling from the shell

```bash
# Is the service running? (never activates it)
manaradix dbus status

# Call a read method as any user
busctl --system call org.mageia.manaradix /org/mageia/manaradix \
    org.mageia.manaradix ListSnapshots s /

# A manage-tier call prompts for authorization (or fails with
# org.mageia.manaradix.Error.AuthRequired if denied)
busctl --system call org.mageia.manaradix /org/mageia/manaradix \
    org.mageia.manaradix CreateSnapshot sssbsssb / "" true manual "" / false
```

## From Python

```python
from manaradix.dbus.client import ManaradixClient

client = ManaradixClient()
for snap in client.list_snapshots("/"):
    print(snap["id"])
```

## Files shipped

- `data/systemd/manaradix-dbus.service` — unit (`Type=simple`, no
  `BusName=`, no `[Install]`; the bus daemon completes activation by
  watching the name itself)
- `data/dbus/org.mageia.manaradix.service` — bus-activation descriptor
  (`SystemdService=`)
- `data/dbus/org.mageia.manaradix.conf` — bus policy: anyone may call,
  polkit gates each method
- `data/polkit/org.mageia.manaradix.policy` — the four polkit actions
