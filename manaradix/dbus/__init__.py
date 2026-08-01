"""D-Bus service and client for manaradix.

A system-bus service (``org.mageia.manaradix``) exposing the core
services (snapshots, policies, config, filesystem operations) with
per-method polkit authorization at four tiers mirroring the
``org.mageia.manaradix.*`` polkit actions, plus a thin client library
for programmatic access.

The service is bus-activated on demand (never enabled at boot): the
systemd unit and the ``/usr/share/dbus-1/system-services/`` activation
file shipped under ``data/systemd/`` and ``data/dbus/``.
"""
