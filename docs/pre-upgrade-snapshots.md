# Pre-upgrade snapshots

Automatic snapshot of `/` **before** package updates run — the safety
net for `urpmi --auto-update`, `dnf upgrade`, and distribution upgrades
(mga10 → mga11, mga → cauldron). Design intent and decisions.

- **Created:** 2026-08-05 (manaradix 0.1.x)

---

## 1. Goal

When a package update starts — a single package, a full `urpmi
--auto-update`, or a whole distribution upgrade — the system takes a
read-only btrfs snapshot of `/` **before the first file changes**. If
the upgrade breaks the system, the user boots the pre-upgrade snapshot
from the GRUB recovery menu, or restores it with the swap method.

Enabling it is a **profile decision**: all three shipped setup profiles
(desktop, server, kiosk) turn it on (plus a Settings toggle); the
mechanism itself ships with the RPM and is inert until the config flag
is set.

## 2. Why an rpm file trigger — and not a package-manager hook

Mageia's urpmi has **no user-facing pre/post-transaction hook**
([bug 32423 — "Provide URPMI hooks"](https://bugs.mageia.org/show_bug.cgi?id=32423),
still NEW; the maintainers' own position is that this belongs in RPM).
A dnf plugin would only cover dnf. PATH wrappers around `urpmi`/`dnf`
are fragile and miss direct `rpm -i`.

But `urpmi`, `dnf`, and `rpm` are all frontends that execute the same
**rpm transaction engine** — so one rpm-level mechanism covers every
path. RPM provides exactly the primitive needed:

- **`%transfiletriggerun -- <paths>`** runs **once at the start of a
  transaction, before any file is removed or installed**, whenever the
  transaction removes/upgrades files matching the path prefixes
  ([rpm file triggers](http://rpm.org/docs/4.20.x/manual/file_triggers.html),
  [rpm-scriptlets(7)](https://rpm.org/docs/6.1.x/man/rpm-scriptlets.7)).
  Transaction order: `%pretrans` (per-package) → `%transfiletriggerun`
  → file changes → `%posttrans` → `%transfiletriggerin` (after).

The trigger is registered when the manaradix package is installed and
stays registered until it is removed. Mageia ≥ 9 is usr-merged, so the
pattern `/usr /etc` matches essentially every transaction — an upgrade
matches because the *old* versions of the files are removed.

This is exactly the "before the upgrade starts" hook, on every frontend:

| Frontend | Hook fires? |
|----------|-------------|
| `urpmi --auto-update` / `urpmi pkg...` | yes — drives rpm transactions |
| `dnf upgrade` (if installed) | yes — same rpm transactions |
| `rpm -Uvh` / `rpm -e` | yes |
| GUI applets (`mgaapplet`, …) | yes — they drive urpmi |

## 3. The trigger script — contract first

`data/libexec/manaradix-pre-upgrade`, installed to
`%{_libexecdir}/manaradix/pre-upgrade`. It is invoked by rpm as root
inside every matching transaction, and it obeys one non-negotiable
rule:

> **The trigger must never fail the rpm transaction.** rpm aborts the
> whole transaction when a trigger exits non-zero. Every error path
> exits 0; failures are logged (stderr is captured by rpm) and nothing
> more.

Properties:

- **Gated by config**: it calls the CLI
  (`manaradix snapshot create --subvolume / --type pre-upgrade
  --schedule pre-upgrade`), and the CLI itself no-ops with exit 0 when
  `snapshot_before_upgrade` is off in `/etc/manaradix/config.toml` or
  the root filesystem is not btrfs — one source of truth, testable in
  the CLI. `MANARADIX_BIN` overrides the CLI path (test hook).
- **Bounded runtime**: wrapped in `timeout 300` with `nice -n 19` and
  `ionice -c3` — a slow snapshot must not stall the transaction, and
  must not starve it.
- **Throttled**: an update run often splits into several rpm
  transactions (`--split-length`, removals, …). A timestamp marker in
  `/run/manaradix/last-pre-upgrade` (override `MANARADIX_RUN_DIR`)
  allows at most one pre-upgrade snapshot per
  `MANARADIX_THROTTLE_SECONDS` (default 600). `/run` is tmpfs, so a
  reboot re-arms the throttle — a dist-upgrade spread across reboots
  still snapshots each boot's first transaction.
- **No pkexec**: the script runs as root inside rpm; the CLI's
  elevation guard (`needs_elevation(...) and not is_root()`,
  module.py:939) skips pkexec for root.

## 4. Data model

| Change | File | Effect |
|--------|------|--------|
| `SnapshotType.PRE_UPGRADE = "pre-upgrade"` | `model/snapshot.py` | classification tag for pre-upgrade snapshots |
| add to `is_read_only` | `model/snapshot.py:45` | pre-upgrade snapshots are read-only → appear in the GRUB recovery menu, exportable via `btrfs send` |
| `RetentionPolicy.keep_pre_upgrade: int = 3` | `model/policy.py` | retention tier: keep the 3 most recent pre-upgrade snapshots; validated like the other `keep_*` fields |

## 5. Configuration

Two new settings in `/etc/manaradix/config.toml` (`core/config_store.py`):

```toml
[global]
snapshot_before_upgrade = true   # master switch; default false (opt-in)

[policies."/"]
retention.keep_pre_upgrade = 3   # per-policy retention, like keep_boot
```

- `snapshot_before_upgrade` defaults to **false** — the RPM ships the
  trigger always installed but inert; only a profile or the Settings
  toggle turns it on. Users who never opt in never see an unexpected
  snapshot.
- `keep_pre_upgrade` lives on `RetentionPolicy` (per subvolume) so the
  existing prune machinery — `snapshot prune --schedule`, policy-driven
  keep counts — works unchanged.

## 6. CLI

`module.py`:

- `_SCHEDULE_TO_RETENTION["pre-upgrade"] = "keep_pre_upgrade"` — one
  entry makes both `snapshot create --schedule pre-upgrade` and
  `snapshot prune --schedule pre-upgrade` legal (the `--schedule`
  choices come from this dict).
- `_cmd_snapshot_create` gates `--type pre-upgrade` on the config flag:
  when `snapshot_before_upgrade` is false, it logs and returns 0
  **without creating anything**. This is what makes the trigger a no-op
  on non-btrfs roots (create would fail anyway) and for users who
  disabled the feature.
- after creating a pre-upgrade snapshot, the handler immediately prunes
  the tier (`keep_pre_upgrade` most recent), mirroring what the
  systemd timer units do via `ExecStartPost` (policy_engine.py:146) —
  the pre-upgrade tier has no systemd unit, it is event-driven.

## 7. Setup profiles & wizard

All three shipped profiles (`data/templates/{desktop,server,kiosk}.toml`)
enable the behavior — a server is where headless dist-upgrades are most
likely, and a kiosk gets an upgrade-time recovery point without any
periodic snapshots; the wizard adapts it like any other
layout-dependent feature:

```toml
[global]
boot_from_snapshot = true
snapshot_dir = ".snapshots"
snapshot_before_upgrade = true   # all shipped profiles opt in

[[policies]]
subvolume = "/"
required = true
schedules = ["daily", "weekly", "boot"]
keep_pre_upgrade = 3             # NEW keep_* key (templates._KEEP_FIELDS)
```

- `templates.py`: `SetupProfile.snapshot_before_upgrade: bool = False`,
  parsed from `[global]`; `keep_pre_upgrade` added to `_KEEP_FIELDS`
  (profiles introduce no new vocabulary — it maps onto
  `RetentionPolicy` exactly like the other `keep_*` keys).
- `setup_wizard.build_plan`: the flag is **effective only when the root
  filesystem is btrfs** (same gating as `boot_from_snapshot`); when it
  is dropped, a human-readable note is appended to the plan.
- `setup_wizard.apply_profile`: writes the *effective* value into the
  config store next to `boot_from_snapshot`; included in the plan and
  summary dicts for the wizard UI.
- The wizard installs **nothing** for this feature — the trigger is
  already on the system; the config flag is the whole activation.

## 8. UI

- **Settings page** (`ui/pages/settings.py`): a "Snapshot before system
  upgrades" checkbox in Global Configuration (next to boot-from-
  snapshot), saved via the existing Save Settings flow.
- **Policies page** (`ui/pages/policies.py`): `keep_pre_upgrade` joins
  `_RETENTION_KEYS`, so the retention summary and editor show "Keep
  pre-upgrade" like the other tiers.

## 9. RPM packaging

`manaradix.spec`:

```spec
%transfiletriggerun -- /usr /etc
%{_libexecdir}/manaradix/pre-upgrade
```

plus `install -D -m 0755 data/libexec/manaradix-pre-upgrade
%{buildroot}%{_libexecdir}/manaradix/pre-upgrade` in `%install` and the
path in `%files`. No new Requires — the script only needs the CLI it
already requires.

## 10. The recovery flow

1. `urpmi --auto-update` against mga11 media starts → rpm fires the
   trigger → read-only snapshot of `/` is taken **before** the first
   file changes.
2. The upgrade fails or breaks the system.
3. Reboot → GRUB → *Manaradix snapshot … pre-upgrade* → the machine
   boots the untouched pre-upgrade system (read-only root).
4. Restore via the swap method, or rescue data from the mounted
   snapshot.

The GRUB integration needs no change: `41_manaradix` lists read-only
snapshots, and `PRE_UPGRADE` is read-only (`is_read_only`).

## 11. Behavior notes & edge cases

- **Fires on removals too** (files matching `/usr /etc` are removed in
  uninstall transactions) — a snapshot before `rpm -e` is harmless and
  often useful; retention keeps the tier small.
- **The transaction that upgrades manaradix itself**: the *old* trigger
  and the *old* CLI run (the trigger fires before file changes), then
  the new package registers the next trigger. Works.
- **Non-btrfs root**: create fails → CLI logs and exits 0 → trigger
  exits 0 → transaction proceeds. The flag can never turn an update
  into a failure.
- **Broken CLI**: `[ -x /usr/bin/manaradix ]` guard plus `|| true` —
  even a missing/corrupt CLI cannot block an update.
- **Volume**: every transaction with the flag on costs one snapshot
  (throttled to one per 10 minutes per update run, kept to the 3 most
  recent). Cheap on btrfs; bounded by retention.

## 12. Design properties

- **One hook, all frontends**: the rpm file trigger covers urpmi, dnf,
  and rpm uniformly — no per-package-manager plugins.
- **Before, not after**: `%transfiletriggerun` runs before any file
  changes; the snapshot always captures the pre-upgrade state.
- **Never a failure point**: the trigger's exit-0 contract means the
  snapshot feature can degrade (config off, non-btrfs, broken CLI) but
  can never block or abort a package transaction.
- **Opt-in, profile-driven**: shipped inert; the setup profiles and the
  Settings toggle activate it; retention bounds the cost.
- **Reuses the whole pipeline**: type, schedule tier, retention, prune,
  GRUB menu, wizard adaptation — no new machinery.
