# Setup profiles

How manaradix turns a stock, unconfigured btrfs system into one with
automatic snapshot coverage in a single step — via role-aware, layout-
adapted **setup profiles** (desktop, server, kiosk, …). Design intent
and decisions: spec.md §15.

- **Created:** 2026-08-04 (manaradix 0.1.x)

---

## 1. What a profile is

A **setup profile** is a named, shippable TOML template describing a
desired snapshot baseline: global settings plus a list of per-subvolume
policies with schedules and retention. Profiles are never applied
blindly — every profile is **adapted to the actual system layout**
before anything is written: only subvolumes that actually exist as btrfs
targets get policies, and boot snapshots / the GRUB recovery menu are
only proposed when the hardware can actually support them.

| Code | Role |
|------|------|
| `SetupProfile` / `TemplatePolicy` | template dataclasses (`manaradix/core/templates.py`) |
| `load_profiles()` | template discovery + tolerant parsing |
| `needs_setup()` / `detect_role()` / `build_plan()` / `apply_profile()` | engine (`manaradix/core/setup_wizard.py`) |
| `data/templates/*.toml` | shipped profiles (installed to `/usr/share/manaradix/templates/`) |

## 2. When the wizard appears

The wizard is offered only when the system is **truly unconfigured**
(`needs_setup()`, setup_wizard.py:37):

- **no snapshot** exists on the default mount (`SnapshotManager.list_all`
  empty), **and**
- **no snapshot policy** is configured (`ConfigStore.list_policies`
  empty).

Any snapshot or policy from *any* source counts as configured — the
wizard never nags a system that already has protection. Errors while
probing are treated as "no wizard" (an unreadable btrfs state must not
turn into nagging either). When the condition holds, the GUI prepends a
Setup Wizard page to the navigation (`ui/navigation.py:41`) and the CLI
reports `setup status: Setup needed`.

## 3. Profile anatomy

One profile = one TOML file (parsed with `tomlkit`, no YAML):

```toml
# data/templates/desktop.toml (abridged)
id = "desktop"                  # required — unique id, overrides by this
name = "Desktop workstation"    # required — shown in the dropdown
description = "..."             # optional — shown under the name
role = "desktop"                # "desktop" | "server" | "any"

[global]
boot_from_snapshot = true       # only effective on btrfs root + GRUB
snapshot_dir = ".snapshots"     # snapshots live in <mount>/.snapshots

[[policies]]
subvolume = "/"                 # mount point the policy applies to
required = true                 # missing subvolume → shown as unavailable
schedules = ["daily", "weekly", "boot"]
keep_daily = 7                  # optional — model defaults fill the rest

[[policies]]
subvolume = "/home"             # only applied when /home is a btrfs target
required = false                # optional: missing → dropped silently
schedules = ["daily"]
```

| Field | Valid values | Default | Notes |
|-------|--------------|---------|-------|
| `id` | string | — | required; duplicates are overridden by later dirs |
| `name` | string | — | required; translated at runtime |
| `description` | string | `""` | shown by `setup list-profiles` |
| `role` | `desktop` \| `server` \| `any` | `any` | `any` profiles are never auto-selected |
| `global.boot_from_snapshot` | bool | `false` | effective only on btrfs root + GRUB |
| `global.snapshot_before_upgrade` | bool | `false` | snapshot `/` before package updates; effective only on a btrfs root (see [pre-upgrade-snapshots.md](pre-upgrade-snapshots.md)) |
| `global.snapshot_dir` | string | `.snapshots` | snapshot directory name |
| `policies[].subvolume` | string | — | required per entry |
| `policies[].schedules` | list of `hourly` \| `daily` \| `weekly` \| `monthly` \| `boot` | — | at least one required |
| `policies[].keep_*` | non-negative int | model defaults | map onto `RetentionPolicy` (10/7/4/3/5/3) |
| `policies[].required` | bool | `true` | behavior when the subvolume is missing |

The `keep_*` keys map directly onto `RetentionPolicy` — templates
introduce **no new policy vocabulary**; everything they declare is
exactly what the Policies page and `PolicyEngine` already understand.

### Tolerant parsing

A malformed file is logged and **skipped**, never fatal — a broken
admin template must not block the wizard (templates.py:143):

- missing `id` or `name` → file skipped;
- unknown `role` → treated as `any`;
- non-integer `keep_*` → default kept; non-boolean `required` → `true`;
- unknown schedule name → dropped; policy left with no valid schedules →
  policy skipped; policy without `subvolume` → skipped.

## 4. Shipped profiles

`data/templates/` ships three profiles (installed by the spec to
`/usr/share/manaradix/templates/`):

| Profile | role | Policies |
|---------|------|----------|
| `desktop` | `desktop` | `/` daily+weekly+boot+pre-upgrade, `keep_pre_upgrade=3` (required); `/home` daily, `keep_daily=7` (optional) |
| `server` | `server` | `/` hourly+daily+weekly+monthly+boot+pre-upgrade, `keep_pre_upgrade=3` (required); `/srv` and `/var` daily, `keep_daily=7` (both optional) |
| `kiosk` | `any` | `/` boot only, `keep_boot=10`, plus pre-upgrade `keep_pre_upgrade=3` (required) — one recovery point per boot, no periodic snapshots |

All three profiles set `global.snapshot_before_upgrade = true` —
snapshot `/` before every package update (see
[pre-upgrade-snapshots.md](pre-upgrade-snapshots.md)).

## 5. Search path & precedence

`load_profiles()` walks directories in order (**later dirs override
profiles with the same `id`**):

1. `$MANARADIX_TEMPLATE_DIRS` (colon-separated) — test/embedder hook;
2. the repo `data/templates/` — used when running from a checkout
   (editable installs);
3. `/usr/share/manaradix/templates/` — shipped by the RPM;
4. `/etc/manaradix/templates.d/` — **admin overrides**, the extension
   point for user- or vendor-supplied profiles.

Override is by `id` only: a custom `/etc/manaradix/templates.d/desktop.toml`
fully replaces the shipped desktop profile; a profile with a new `id`
just joins the list. The wizard works even when a directory is missing
or unreadable.

`manaradix/core/template_strings.py` re-declares the shipped profiles'
name/description strings so xgettext can extract them — profile names
are translated like everything else.

## 6. Role detection & default selection

`detect_role()` (setup_wizard.py:89) classifies the machine without
privileges:

1. `systemctl get-default` — `graphical.target` → `desktop`, anything
   else → `server`;
2. fallback when systemd can't be queried: installed X sessions
   (`/usr/share/xsessions` non-empty) → `desktop`, else `server`.

`select_default_profile(profiles, role)` picks the wizard's proposed
profile: first profile whose `role` matches, else the first
`role="any"` profile, else the first profile, else `None`.
`role="any"` profiles (kiosk) are **never** auto-selected — they are
always a deliberate user choice. The user can switch profiles with the
dropdown before applying.

## 7. Layout adaptation (`build_plan`)

Before proposing anything, the wizard probes the system
(`system_layout()`: `btrfs_mounts()` targets, root-is-btrfs,
`has_system_grub()`; probe errors degrade to "nothing available", never
crash) and adapts the profile:

- **A policy applies iff its subvolume is a mounted btrfs target.**
  Optional policies on missing subvolumes are dropped silently; required
  ones are kept but annotated "Not available on this system" in the plan
  table and skipped at apply.
- **`boot` schedules are honored only for the `/` policy on a btrfs
  root.** A separate ESP `/boot` on vfat/ext4 is fine — GRUB reads btrfs
  snapshots itself.
- **`boot_from_snapshot` is effective only when the root is btrfs AND a
  GRUB configuration exists** (`GrubIntegration.has_system_grub()`: any
  of `/boot/grub2/grub.cfg`, `/boot/grub/grub.cfg`,
  `/boot/efi/EFI/*/grub.cfg`).
- **`snapshot_before_upgrade` is effective only when the root is
  btrfs** — the pre-upgrade trigger (an rpm `%transfiletriggerun`)
  snapshots `/`, which only makes sense on a btrfs root; otherwise the
  plan notes "Pre-upgrade snapshots are not available".
- **No btrfs at all** → the wizard page shows an informational message
  and only `Skip`; `setup status` reports the same.

The result is a plan dict consumed by both the GUI table and the
apply flow: `{profile_id, snapshot_dir, boot_from_snapshot_effective,
applicable, layout, policies, notes}`. `applicable` is false when no
policy survives adaptation — the Apply button is hidden and
`apply_profile` raises. The plan also carries human-readable `notes`
explaining what was skipped and why (boot dropped, GRUB menu not
available), which the wizard renders under the plan table.

## 8. Applying a profile (`apply_profile`)

One call performs the whole setup (setup_wizard.py:222):

1. **Resolve** the profile — explicit `id`, or the default for the
   detected role. Unknown id, no profiles, or an inapplicable profile →
   `ValueError` (surfaced as the CLI error / a "Setup Failed" page).
2. **Build the adapted plan** (as above).
3. **Write global config**: `snapshot_dir` and the *effective*
   `boot_from_snapshot` and `snapshot_before_upgrade` into
   `/etc/manaradix/config.toml`; `default_subvolume` becomes `/` when
   the root is btrfs, else the first available policy's subvolume.
4. **Register each available policy** in the config store and apply it
   via `PolicyEngine.apply_policy` — the systemd units are the core of
   the setup, so a **unit failure is fatal** (unlike GRUB below).
5. **Install the GRUB recovery script** (`install_script()` +
   `update_grub()`) when `boot_from_snapshot_effective`. A GRUB failure
   is logged, **not fatal** — a snapshot policy is still better than
   none.

`dry_run=True` builds and logs the whole plan without writing anything
(including the units and GRUB steps). Returns a summary dict for the
UI/CLI: `{config_written, profile_id, grub_effective, policies,
grub_installed}`.

**Idempotent**: re-running produces the same state — config is
overwritten, units converge (see [activities.md](activities.md) §3.3),
the GRUB script is regenerated.

## 9. Configuration surfaces

### 9.1 GUI — Setup Wizard page

When `needs_setup`, `build_navigation()` inserts the wizard at the front
(`ui/pages/setup.py`). It shows:

- a **profile dropdown** (auto-selected default from the detected role,
  switchable — switching re-builds the plan);
- the **plan table**: per-policy subvolume / schedules / retention,
  snapshot directory, boot-from-snapshot status, each annotated "Not
  available on this system" where the layout does not support it;
- **notes** explaining skipped parts;
- **Apply Setup** (hidden when the selected profile is inapplicable) and
  **Skip**.

Applying shows a per-policy summary (schedules + retention, GRUB menu
installed or not). Skipping shows a note that everything can be
configured later from Policies / Settings, and the wizard is not
re-offered within the same session. Edge states are handled: no profiles
on the system, or no btrfs at all, each get their own informational
page with only `Skip`.

### 9.2 CLI

| Command | Purpose |
|---------|---------|
| `manaradix setup status` | read-only: is setup needed, detected role, default profile |
| `manaradix setup list-profiles` | read-only: available profiles with role + description |
| `manaradix setup apply [--profile ID] [--dry-run]` | apply a profile (pkexec-elevated, same class as `snapshot create`) |

`setup status` distinguishes "setup not needed" from "setup needed but
no btrfs on this system" and prints the runnable command.

### 9.3 D-Bus

The system service exposes `SetupStatus` (read tier) and `SetupApply`
(manage-policies tier) as thin delegations to the same functions — the
daemon contains no setup logic of its own.

## 10. Authoring a custom profile

Drop a TOML file into `/etc/manaradix/templates.d/` (create the
directory yourself — the RPM only ships the system templates; a missing
directory is simply skipped). Either a profile with a new `id`, or an
override of a shipped one. Example — a laptop profile:

```toml
id = "laptop"
name = "Laptop"
role = "any"
description = "Modest periodic coverage for a mobile workstation."

[global]
boot_from_snapshot = true

[[policies]]
subvolume = "/"
schedules = ["daily", "boot"]
keep_daily = 5
keep_boot = 3
```

It appears in the dropdown / `setup list-profiles` on the next run. A
syntax error only logs a warning and skips the file — the remaining
profiles still work. (A profile is only *proposed*; applying is always
an explicit user action.)

## 11. Design properties

- **One-step coverage**: unconfigured btrfs → full automatic snapshot
  baseline in a single explicit action; nothing is applied implicitly.
- **Layout-honest**: never proposes what the machine cannot deliver —
  optional subvolumes, boot snapshots and the GRUB menu are all gated on
  real probing.
- **No new vocabulary**: profiles reuse the policy/retention model and
  the `PolicyEngine` — what a profile declares is exactly what the
  Policies page edits.
- **Tolerant by construction**: broken templates, missing directories,
  probe errors and GRUB failures degrade gracefully and never block
  use.
- **Extensible**: `/etc/manaradix/templates.d/` overrides and extends
  the shipped set without touching the RPM.
