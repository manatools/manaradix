# Usage tracker

How manaradix measures snapshot disk usage without freezing the GUI.

## Why it exists

The Dashboard's *Snapshot Usage* section shows how much space snapshots
take (total and exclusive).  The only way to get those numbers is
`btrfs filesystem du`, which walks every extent of every snapshot.  On a
busy root that takes minutes and used to run inside the GUI event loop,
so pressing the button froze the whole window.

## Design

```
manaradix-usage.timer (daily)          Dashboard "Force Usage Data Update"
        |                                      |  systemctl start --no-block
        v                                      v
manaradix-usage.service  ->  manaradix usage collect   (root)
        |
        v   writes
/var/lib/manaradix/usage.sqlite   <--- read-only ---  GUI, CLI, D-Bus GetUsage
```

* **Collector** (`UsageCollector`, `manaradix/core/usage_store.py`): finds
  the btrfs mounts (one per filesystem), lists their snapshots and runs a
  single `btrfs filesystem du -s --raw` over the snapshot directory plus
  every snapshot path.  One pass gives the per-mount aggregate and the
  per-snapshot rows.
* **Store** (`UsageStore`): SQLite file, three tables (`aggregates`,
  `snapshot_usage`, `meta`).  A collection marks `status=running`, then
  replaces the data in one transaction and sets `ok`, or records
  `failed:<message>`.  A failure never discards the previous numbers.
* **Reader** (`UsageReader`): opens the file read-only
  (`file:...?mode=ro`), so the unprivileged GUI cannot modify it and a
  crashed writer cannot corrupt what readers see.  A missing file means
  state `idle` ("never measured").
* **Dashboard**: renders whatever is stored plus a status line --
  *Last updated*, *Collecting usage data...*, *Last collection failed*, or
  *No usage data collected yet*.  Building the page never runs `btrfs`.

## Units

| Unit | Role |
|---|---|
| `manaradix-usage.service` | `Type=oneshot`, runs `manaradix usage collect` as root at idle I/O priority |
| `manaradix-usage.timer` | daily, `Persistent=true`, up to 1 h random delay; enabled on first install only |

## Interfaces

| Where | What |
|---|---|
| CLI | `manaradix usage collect` / `status` / `show` (see [CLI reference](cli.md)) |
| D-Bus | `GetUsage` (read tier), `RefreshUsage` (manage-filesystem tier) |

## Known limits

* Data is as old as the last run; the dashboard says so instead of
  hiding it.  The button updates it on demand.
* `usage show` and the store already hold per-snapshot sizes; the
  Dashboard only displays the aggregate row for `/` so far.
