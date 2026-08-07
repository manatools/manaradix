# Automated snapshots (systemd timers)

Manaradix generates and manages systemd timer/service units that take
snapshots on a schedule and automatically prune old ones.

## Setting it up

1. **Create a config file** at `/etc/manaradix/config.toml` — or apply a
   [setup profile](profiles.md), which does this for you.

2. **Apply the policy** — this writes the systemd units and enables the
   timers:

   ```python
   from manaradix.core.policy_engine import PolicyEngine
   from manaradix.core.config_store import ConfigStore

   store = ConfigStore()
   store.load()
   policy = store.get_policy("/")
   engine = PolicyEngine()
   engine.apply_policy(policy)
   ```

   Or from the GUI: **Policies → select a subvolume → Apply Policy** (the
   Triggers tab shows the resulting systemd units with their live state and
   lets you enable/disable any of them individually).

## Unit naming and behavior

The generated units (under `/etc/systemd/system/`) follow the pattern
`manaradix-snapshot-<subvol>-<schedule>.{service,timer}` and invoke the
CLI subcommands:

```
manaradix snapshot create --subvolume <X> --type timed --schedule <S>
manaradix snapshot prune --subvolume <X> --schedule <S>
```

- **Timers** — hourly (`*-*-* *:00:00`), daily (`*-*-* 00:00:00`),
  weekly (`Mon *-*-* 00:00:00`), monthly (`*-*-01 00:00:00`).
- **Boot snapshots** — a oneshot service (`WantedBy=multi-user.target`)
  instead of a timer, firing once per boot.
- Each service unit ends with `manaradix activity record` so the run
  result is tracked; `manaradix activity status` reports the current state
  of every scheduled tier (also visible in the GUI and over D-Bus).

## Pruning

Pruning keeps the N most recent snapshots for the schedule, N coming from
the retention policy (`keep_hourly`, `keep_daily`, ... — see
[Configuration](configuration.md)):

```bash
manaradix snapshot prune --subvolume / --schedule daily
manaradix snapshot prune --subvolume / --schedule daily --dry-run
```

For the full design of the schedule/activity machinery (lifecycle, run
records, configuration surfaces), see [Design & internals](activities.md).
