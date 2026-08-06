# Documentation index

User-facing and design documentation for manaradix. The same content is
also rendered as an MkDocs site (`mkdocs serve` / `mkdocs build` from the
repository root).

## User guide

| Document | Contents |
|---|---|
| [Install](install.md) | Installing from Mageia repositories, system requirements |
| [Quick start](quickstart.md) | GUI launch, setup wizard, first snapshots |
| [CLI reference](cli.md) | All commands, options, exit codes, privilege model |
| [Configuration](configuration.md) | `/etc/manaradix/config.toml` reference |
| [Automated snapshots](automated-snapshots.md) | systemd timers, unit naming, pruning |
| [Boot from snapshot](boot-from-snapshot.md) | GRUB recovery menu |
| [Setup profiles](profiles.md) | Desktop/server/kiosk profiles, template schema |
| [Pre-upgrade snapshots](pre-upgrade-snapshots.md) | The rpm file trigger mechanism |

## Interfaces

| Document | Contents |
|---|---|
| [D-Bus interface](api/dbus.md) | Service, 41 methods, polkit tiers, busctl examples |
| [Python API reference](api/python.md) | Generated from docstrings (mkdocstrings) |

## Development

| Document | Contents |
|---|---|
| [Development](development.md) | Venv setup, tests, translations, packaging |
| [Design & internals](activities.md) | Schedule/activity machinery design |
| [Roadmap & issues](SECURITY_AND_PERFORMANCES.md) | Living security/perf/bug/feature tracker |

## Repo-level documents

- [`spec.md`](../spec.md) — full technical specification and design
  decisions
- [`README.md`](../README.md) — project overview (also served as the
  package description)
- [`CLAUDE.md`](../CLAUDE.md) — contributor orientation for Claude Code
  sessions
