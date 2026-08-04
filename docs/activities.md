# Activities: automated snapshot schedules

How manaradix turns "keep snapshots of this subvolume on a schedule" into
working systemd timers, and how the snapshots they produce are cleaned up.

- **Created:** 2026-08-04 (manaradix 0.1.x)

---

## 1. What an activity is

An **activity** is one automated snapshot job: a *subvolume × schedule*
pair. The engine knows five schedule tiers:

| Tier | Kind | When it fires | systemd mechanism |
|------|------|---------------|-------------------|
| `hourly` | calendar | at `*:00:00` of every hour (`*-*-* *:00:00`) | timer unit |
| `daily` | calendar | at `00:00:00` every day (`*-*-* 00:00:00`) | timer unit |
| `weekly` | calendar | at `00:00:00` on Mondays (`Mon *-*-* 00:00:00`) | timer unit |
| `monthly` | calendar | at `00:00:00` on the 1st (`*-*-01 00:00:00`) | timer unit |
| `boot` | trigger | once per boot, at late boot | oneshot service |

The four calendar tiers are **timer-based**: a systemd `.timer` fires a
paired `.service`. The boot tier is **trigger-based**: a oneshot service
enabled against `multi-user.target`, no timer at all.

The codebase vocabulary maps to this doc as follows:

| This doc | Code |
|----------|------|
| activity | one enabled schedule on one subvolume |
| policy | `SnapshotPolicy` — the per-subvolume configuration (`manaradix/model/policy.py`) |
| retention | `RetentionPolicy` — the `keep_*` counts (`manaradix/model/policy.py`) |
| engine | `PolicyEngine` — unit generation + systemctl (`manaradix/core/policy_engine.py`) |

## 2. The model

### 2.1 `SnapshotPolicy` — one per subvolume

```python
SnapshotPolicy(
    subvolume="/",              # path the policy applies to
    enabled=True,               # master switch for every schedule
    schedule_hourly=False,      # one boolean per tier
    schedule_daily=True,        # note: daily defaults to True
    schedule_weekly=False,
    schedule_monthly=False,
    schedule_boot=False,
    retention=RetentionPolicy(), # keep_* counts for pruning
)
```

- `active_schedules` is a derived property: the list of tier names whose
  flag is set (`["hourly", "daily"]`, …).
- `validate()` rejects empty subvolumes and policies with no enabled
  schedule ("at least one schedule must be enabled"). `PolicyEngine`
  refuses to apply an invalid policy.

### 2.2 `RetentionPolicy` — how many snapshots survive

| Field | Default | Meaning |
|-------|---------|---------|
| `keep_hourly` | 10 | hourly snapshots kept |
| `keep_daily` | 7 | daily snapshots kept |
| `keep_weekly` | 4 | weekly snapshots kept |
| `keep_monthly` | 3 | monthly snapshots kept |
| `keep_boot` | 5 | boot snapshots kept |

All values must be non-negative integers (`validate()`). A `0` means
"keep nothing" — every snapshot of that tier is deleted at the next
prune.

### 2.3 Persistence

Policies live in `/etc/manaradix/config.toml` (`ConfigStore`,
`manaradix/core/config_store.py`):

```toml
[global]
default_subvolume = "/"
snapshot_dir = ".snapshots"
boot_from_snapshot = false

[policies."/"]
enabled = true
schedule_hourly = true
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

Policies are keyed by subvolume path; loading fills in model defaults
for anything missing. The config file is the **source of truth for
retention** — the systemd units never encode counts.

## 3. Unit generation (`PolicyEngine`)

Units are written to `/etc/systemd/system/` and named

```
manaradix-snapshot-<sanitized-subvolume>-<schedule>.service
manaradix-snapshot-<sanitized-subvolume>-<schedule>.timer
```

The subvolume is sanitized into a unit-name fragment: leading `/`
stripped, `/` → `-`, any other invalid character → `-`, empty → `root`.

| Subvolume | Fragment | Daily timer unit |
|-----------|----------|------------------|
| `/` | `root` | `manaradix-snapshot-root-daily.timer` |
| `/home` | `home` | `manaradix-snapshot-home-daily.timer` |
| `/srv` | `srv` | `manaradix-snapshot-srv-daily.timer` |

### 3.1 Calendar tier: service + timer pair

The service unit (oneshot) drives the actual work through the CLI:

```ini
[Unit]
Description=Manaradix daily snapshot of /
After=local-fs.target

[Service]
Type=oneshot
ExecStart=/usr/bin/manaradix snapshot create --subvolume "/" --type timed --schedule daily
ExecStartPost=/usr/bin/manaradix snapshot prune --subvolume "/" --schedule daily
ExecStopPost=/usr/bin/manaradix activity record --subvolume "/" --schedule daily
Nice=19
IOSchedulingClass=idle
```

Three steps in one unit: **create** the snapshot, then **prune** (retention
enforcement, §5), then **record** the run (§6 — `ExecStopPost` runs at the
end of every fire, success *or* failure, with `$SERVICE_RESULT` set by
systemd). `Nice=19` + `IOSchedulingClass=idle` keep the work from
interfering with interactive use. The `cli_path` is configurable in the
`PolicyEngine` constructor (tests inject a fake path).

The timer unit that fires it:

```ini
[Unit]
Description=Manaradix daily snapshot timer for /

[Timer]
OnCalendar=*-*-* 00:00:00
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
```

- `Persistent=true` — a fire missed while the machine was off (or
  suspended) is made up for at the next boot.
- `RandomizedDelaySec=300` — up to five minutes of random jitter, so
  dozens of machines don't all fire at the same second.
- `WantedBy=timers.target` — `systemctl enable` + `start` makes the
  schedule live immediately.

### 3.2 Boot tier: oneshot service, no timer

```ini
[Service]
Type=oneshot
ExecStart=/usr/bin/manaradix snapshot create --subvolume "/" --type timed --schedule boot
ExecStartPost=/usr/bin/manaradix snapshot prune --subvolume "/" --schedule boot
ExecStopPost=/usr/bin/manaradix activity record --subvolume "/" --schedule boot
...
[Install]
WantedBy=multi-user.target
```

Enabled (not started) against `multi-user.target`: every boot reaches the
target and the snapshot is taken, before user sessions start.

### 3.3 Applying a policy (`apply_policy`)

For each of the four calendar tiers:

- **active** (tier in `policy.active_schedules` **and** `policy.enabled`):
  write the `.service` and `.timer`, `systemctl enable` + `start` the
  timer;
- **inactive**: `systemctl stop`/`disable` the timer (errors ignored),
  delete both unit files.

The boot tier follows the same rule with its oneshot service (`enable`
only — it has no timer to start). A stale unit from a previous
configuration is always cleaned up, so applying is **idempotent**:
re-running produces exactly the same state.

Returns the list of unit paths written (a leading `-` marks a removed
unit — this is what the dry-run preview prints). `dry_run=True` logs
"Would enable/disable …" and returns the paths without touching the
system.

### 3.4 Removing a policy (`remove_policy`)

Stops/disables every `manaradix-snapshot-<subvol>-*` unit (timer and
service, all five tiers) and deletes the files. Independent of the
config store — the UI calls `config_store.remove_policy()` separately.

### 3.5 Inspecting installed units

`list_generated_units()` scans `/etc/systemd/system` for
`manaradix-snapshot-*` files ending in `.timer` or `.service` and returns
them sorted. Used by the Policies page's "systemd Units" table.

### 3.6 Security

Unit names and `ExecStart` arguments are escaped for systemd
(`_escape_systemd_arg`, policy_engine.py:44): `;` (command separator),
`%` (specifier), `"` and `\` (quoting syntax) are escaped, control
characters stripped — a subvolume name can never inject a second command
into a root-running unit.

## 4. Lifecycle of one fire

```
systemd timer (OnCalendar) ──► manaradix-snapshot-<sub>-<tier>.service
                                      │
                                      ▼
                /usr/bin/manaradix snapshot create --type timed --schedule <tier>
                                      │
                                      ▼
              btrfs subvolume snapshot → <mount>/.snapshots/<timestamp>/<subvol-name>
              + info.json sidecar tagged with the tier (schedule=<tier>)
                                      │
                                      ▼
                /usr/bin/manaradix snapshot prune --schedule <tier>
                                      │
                                      ▼
              keep the keep_<tier> most recent, delete the rest
                                      │
                                      ▼   (ExecStopPost — runs whether
                                      │    the steps above succeeded or not)
              /usr/bin/manaradix activity record → /var/lib/manaradix/activity-runs.jsonl
```

The create step (`SnapshotManager.create`) tags the snapshot with its
tier in the JSON sidecar (`info.json` records `type: "timed"` and
`schedule: "hourly"` etc.) — that tag is what pruning filters on. A
timed snapshot is always read-only.

## 5. Retention / pruning

Pruning runs as the `ExecStartPost` of every activity unit, on the same
fire. `manaradix snapshot prune --subvolume X --schedule <tier>`
(`_cmd_snapshot_prune`, module.py:579):

1. Looks up the policy for `X` in the config store; no policy → nothing
   to prune (exit 0).
2. Resolves the retention count for the tier via
   `_SCHEDULE_TO_RETENTION` (`hourly → keep_hourly`, …); no count in the
   config → 0 kept.
3. Calls `SnapshotManager.prune(subvolume, schedule, keep_count)`:
   collects snapshots of that exact subvolume **and** tier, sorts by
   date newest-first, deletes everything beyond `keep_count`.

Notes:

- Tiers are pruned independently. A daily snapshot is never deleted by
  hourly pruning, and vice versa — each tier keeps its own `keep_*` most
  recent snapshots.
- The count comes from the config at *prune time*, so lowering a
  retention value is picked up at the next fire — no unit rewrite
  needed.
- `dry_run` is honored end to end and the CLI prints the deleted IDs
  (or "Nothing to prune (N kept, no excess).").
- `PolicyEngine.get_prune_commands()` exists only to validate a
  `RetentionPolicy` — the actual pruning is always the CLI-driven
  `SnapshotManager.prune`.

## 6. Observing activities

The GUI and `activity status` answer three questions per activity: is it
armed, when did it last fire, and did that fire succeed?  The evidence
comes from three read-only, unprivileged sources:

| Source | What it proves | Code |
|--------|----------------|------|
| `systemctl show` | the unit's live state (active/enabled, last/next trigger, failed) | `PolicyEngine.unit_status()` |
| run records | what actually happened at the end of each fire — **including failed ones** | `core/activity_status.py` |
| snapshot sidecars | a fire produced a snapshot (evidence from before records existed) | `SnapshotManager.list_all()` |

### 6.1 Run records (`/var/lib/manaradix/activity-runs.jsonl`)

Every unit ends with `ExecStopPost=... activity record` (see §3.1).  systemd
runs `ExecStopPost` at the end of every fire — success, failure, or
cancelled stop — and sets `$SERVICE_RESULT` / `$EXIT_STATUS` for it
(systemd.service(5)).  The command appends one JSON object per line:

```json
{"ts": "2026-08-04T10:30:11+02:00", "subvolume": "/", "schedule": "daily",
 "result": "success", "exit_status": "0"}
```

- `result` is derived from `$SERVICE_RESULT`: `success`/`clean` → `success`,
  `canceled` → `cancelled`, anything else (e.g. `exit-code`) → `failed`;
  no environment (a manual call) → `unknown`.  An explicit `--result`
  overrides it.  `exit_status` is `$EXIT_STATUS` when set.
- The file is **root-owned but world-readable (0644)** — the unprivileged
  GUI reads it, and it holds no secrets, only subvolume/schedule/
  timestamps/results.  Only root (and the units, which run as root) can
  write it.
- The file is **bounded**: after every append it is trimmed to the 1000
  newest records, rewritten atomically (tmp + rename) so a reader never
  sees a torn file.  Corrupt lines are skipped, never fatal.
- This is what makes **failed runs visible**: a failed create leaves no
  snapshot, so without the record the user could not distinguish "didn't
  run" from "ran and failed".

### 6.2 Per-activity status

`PolicyEngine.unit_status(subvolume, schedule)` (read-only `systemctl
show`, no privileges) returns the live state plus a derived `state`:

| `state` | Meaning |
|---------|---------|
| `ok` | units installed, enabled and active; last fire within ~2.5× the tier cadence (never fired yet counts as ok — `Persistent=true` timers catch up) |
| `missed` | timer active, but the last fire is older than ~2.5× the cadence — the machine was likely off during fires (hourly: 2.5 h, daily: 2.5 d, weekly: ~2.5 w, monthly: ~2.5 mo) |
| `failed` | the service is in the failed state — this fire (or a previous one) went wrong |
| `inactive` | installed but not enabled/active (e.g. left over after a policy was disabled) |
| `unknown` | unit files missing, or systemd does not know them (no `daemon-reload` since install, or systemd unreachable) |

The boot tier has no timer, so its state is simply `ok`/`failed`/
`inactive`/`unknown` from the enabled/failed state of its oneshot service.

`ActivityStatus.statuses()` (core/activity_status.py) aggregates one row
per **enabled** policy × active schedule, merging the unit state with the
newest run evidence: the newest run record for the pair (which also covers
failed fires), falling back to the newest snapshot tagged with the tier
when there is no record yet.  Every input degrades gracefully — missing
records file, unreadable config, or unreachable systemd produce fewer or
"unknown" rows, never a crash.

### 6.3 Surfaces

- **Dashboard — "Activity Status" section** (`ui/pages/dashboard.py`):
  one row per activity — Subvolume / Schedule / Status / Last run — with a
  non-successful last run marked `(failed)`, `(cancelled)` behind the
  timestamp.  Pull-based: it is re-read on every page re-render, so it is
  as fresh as the last user interaction.
- **Policies page — "systemd Units" table** (`ui/pages/policies.py`):
  every installed unit now shows its systemd `ActiveState`
  (active/inactive/failed/…) next to the file name, so a failed unit is
  visible right next to its policy.
- **CLI** — `manaradix activity status [--subvolume X] [--schedule S]`
  (read-only, never pkexec): same table for terminals and scripts.

## 7. Configuration surfaces

### 6.1 Policies page (GUI)

The Policies tab (`manaradix/ui/pages/policies.py`) shows:

- **Configured Policies** — a table of subvolume / enabled / schedules /
  retention (`Keep H/D/W/M/B`), row-selection drives the editor;
- an **editor form** for the selected policy (or an "Add Policy" form
  with a subvolume field when nothing is selected): `Enabled` checkbox,
  five `On <Tier>` checkboxes, five `keep_*` spinners (0–999), and
  **Apply Policy** / **Dry-Run Preview** / **Remove Policy** buttons;
- **systemd Units** — the installed unit files
  (`policy_engine.list_generated_units()`) with their live
  `ActiveState` (`policy_engine.unit_state()`, §6).

Applying writes the policy to the config store *and* runs
`apply_policy` in one action; removing calls `remove_policy` first, then
drops the config entry. After every callback the page re-renders from
fresh state, so the tables always reflect what just happened.

### 6.2 CLI

| Command | Purpose |
|---------|---------|
| `manaradix snapshot create --subvolume X --type timed --schedule <tier>` | create a timed snapshot tagged with the tier (what the units invoke) |
| `manaradix snapshot prune --subvolume X --schedule <tier> [--dry-run]` | enforce retention for a tier |
| `manaradix snapshot list` | lists snapshots with their type and tier |
| `manaradix activity status [--subvolume X] [--schedule S]` | per-activity status: state, last run, result (read-only) |
| `manaradix activity record --subvolume X --schedule S` | append a run record (invoked by the units as `ExecStopPost`; never pkexec — root by construction) |
| `manaradix setup apply` | bulk-configure activities from a setup profile (see [profiles.md](profiles.md)) |

Privileged operations re-exec via pkexec; the systemd units run as root
by construction.

### 6.3 D-Bus

The system service exposes the same operations programmatically:
`PolicyApply` / `PolicyRemove` (manage-policies tier, polkit
`org.mageia.manaradix.manage-policies`), `SnapshotCreate` / `SnapshotPrune`
(manage-snapshots tier). No business logic in the daemon — each method
delegates to the same core services.

## 8. Design properties

- **CLI-first**: units invoke the CLI; the engine itself only writes
  units and runs `systemctl`.
- **Idempotent**: `apply_policy` converges — enabling what's off, tearing
  down what's stale; re-running produces the same state.
- **Dry-run everywhere**: `apply_policy`, `remove_policy`, create and
  prune all support `dry_run=True` previews.
- **Config-driven retention**: counts live in `/etc/manaradix/config.toml`,
  evaluated per fire — tuning retention never touches systemd.
- **Observable**: every fire writes a run record and the GUI/CLI surface
  live unit state — failures, missed fires and cancellations are visible,
  not silent.  All status reads are unprivileged and degrade gracefully.
- **Journaling**: `SnapshotManager` records every created/pruned snapshot
  in its in-memory journal for potential rollback.
