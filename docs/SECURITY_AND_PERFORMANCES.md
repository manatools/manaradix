# Security & Performances

Living tracker for security, performance, bug, feature, and UI/UX work on
manaradix. Every entry carries a priority so the queue is always sorted.

- **Created:** 2026-08-03 (manaradix 0.1.1)
- **Priority:** P0 = blocker/critical · P1 = high (next release) · P2 = medium ·
  P3 = low / nice-to-have
- **Status:** `Open` · `In Progress` · `Done` · `Deferred` · `Won't Fix`

## How to update

1. Add new rows to the matching table, ranked by priority (P0 first).
2. Re-rank when priorities shift — the list must stay sorted top-to-bottom.
3. Flip status to `Done` (with release note) when work lands.

---

## Security

| ID | Pri | Item | Status | Notes |
|----|-----|------|--------|-------|
| SEC-01 | P1 | Audit shell quoting in `/etc/grub.d/41_manaradix` generation and systemd unit names | Done | 2026-08-03: `_escape_systemd_arg` (policy_engine) — ExecStart args quoted, `;`/`%`/`"`/`\` escaped, control chars stripped; `_shell_escape_dq` (grub_integration) — `$`/backtick/`"`/`\` escaped on all interpolated values; runtime `snap_id` guard (`*[!0-9-]*` → skip). Fuzz tests in both test files. btrfs_ops/snapshot_manager verified clean (list-args, no `shell=True`). |
| SEC-02 | P2 | Harden daemon unit: `ProtectSystem=full/strict`, `ProtectHome` | Done | 2026-08-03: `ProtectSystem=full` + `ProtectHome=yes` + `ProtectKernelTunables/Modules/ControlGroups`, `RestrictSUIDSGID`, `LockPersonality`, `RestrictRealtime`, `ReadWritePaths=/etc/manaradix /etc/systemd/system /etc/grub.d /boot`. `strict` and `PrivateDevices` deliberately NOT used (btrfs ioctls need writable root mount; LUKS mapper nodes needed for browsing). Verified: tests + `systemd-analyze verify`. |
| SEC-03 | P2 | Async polkit `check_authorization` | Deferred | `check_authorization_sync` blocks the mainloop during a challenge dialog (spec.md §10). Async check is a future improvement. |
| SEC-04 | P2 | Verify polkit action coverage vs. all 41 D-Bus methods | In Progress | `METHOD_ACTIONS` cross-checked against the policy file by tests; re-run on every method addition. |
| SEC-05 | P3 | Reduce daemon root surface | Open | Daemon runs as root (required today: polkit + system config writes). Revisit if capabilities/`RunAs` ever apply. |

Done: daemon already runs with `NoNewPrivileges` + `PrivateTmp`; bus policy
gates methods by polkit tier; pkexec re-exec guarded by `MANARADIX_PKEXEC`
(loop prevention).

## Performance

| ID | Pri | Item | Status | Notes |
|----|-----|------|--------|-------|
| PERF-01 | P2 | Dashboard render spawns 3+ btrfs CLI subprocesses | Open | `btrfs subvolume list` + `filesystem usage` + snapshot sidecar reads per refresh. Cache when snapshot set is unchanged. |
| PERF-02 | P2 | N+1 JSON sidecar reads in `SnapshotManager.list_all()` | Open | One `info.json` read per snapshot. Fine at small scale; batch or memoize above ~100 snapshots. |
| PERF-03 | P3 | Full page re-render after every UI event (`build_model()`) | Open | Correct + simple; revisit only if a page grows heavy. |

## Bugs

No open bugs currently tracked (five D-Bus defects found in real-bus smoke
test were fixed and verified end-to-end). Add new rows here as they surface,
with reproduction steps in Notes.

| ID | Pri | Item | Status | Notes |
|----|-----|------|--------|-------|
| —  | —   | (empty — no known open bugs) | — | Template: `BUG-NN | P# | symptom | Open | repro + root cause` |

## Features

| ID | Pri | Item | Status | Notes |
|----|-----|------|--------|-------|
| FEA-01 | P1 | Integration tests on real btrfs in normal CI | Deferred | `tests/test_integration_btrfs.py` needs root + loopback fs; currently skipped (`sudo -E pytest ... -v`). Pre-release gate. |
| FEA-02 | P1 | Non-Italian translations | Open | Only `locale/it.po` (~100 strings) shipped. `msgfmt` + `%find_lang` wired in spec. |
| FEA-03 | P2 | Package-install snapshots | Pending | Spec Q6: urpmi hooks / dnf plugin — decision pending (`Both` proposed). |
| FEA-04 | P3 | RAID configuration UI | Won't Fix | Deferred to installer (spec.md §2). |
| FEA-05 | —  | Web frontend | Won't Fix | Released manatools has no web backend; spec Q5 closed. |

## UI / UX

| ID | Pri | Item | Status | Notes |
|----|-----|------|--------|-------|
| UX-01 | P2 | Qt window fit grows but never shrinks | Open | `_fit_window()`: dialog grows to largest content, never shrinks back. Leaf of `renderer.py`; needs Qt backend support. |
| UX-02 | P3 | Tree column headers untranslated | Won't Fix | Row dict keys are data keys by design — translation would break dispatch. |

---

## Changelog

- 2026-08-03 — file created; seeded from spec.md (§2, §10, §14, §15), CLAUDE.md
  known gaps, and current code state.
- 2026-08-03 — SEC-01 done: ExecStart + GRUB script escaping hardened, runtime
  snap_id filter, fuzz tests added (full suite 449 passed / 8 skipped).
- 2026-08-03 — SEC-02 done: daemon unit hardened (ProtectSystem=full +
  ProtectHome + carve-outs), spec.md §10 note updated, unit tests extended
  (452 passed / 8 skipped).
