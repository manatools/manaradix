"""Conversion between core model objects and D-Bus-friendly values.

Everything crossing the bus boundary passes through here: model
dataclasses become plain dicts (a{sv} / aa{sv}), and incoming
``dbus.*`` values are coerced back to plain Python types before they
reach the core services.

``dbus.Boolean`` is deliberately not a ``bool`` subclass and
``dbus.String`` / ``dbus.Int32`` are not ``str`` / ``int``, so the
coercion helpers below are mandatory — the core services are strict.
All functions in this module are pure and import no dbus-python, so
they are unit-testable without a bus.
"""

from __future__ import annotations

from datetime import datetime

from manaradix.model.policy import RetentionPolicy, SnapshotPolicy
from manaradix.model.snapshot import Snapshot
from manaradix.model.subvolume import Subvolume


def _s(value) -> str:
    """Coerce a (possibly ``dbus.String``) value to ``str``."""
    return str(value)


def _b(value) -> bool:
    """Coerce a (possibly ``dbus.Boolean``) value to ``bool``."""
    return bool(value)


def _i(value) -> int:
    """Coerce a (possibly ``dbus.Int32``) value to ``int``."""
    return int(value)


# ---------------------------------------------------------------------------
# Model -> D-Bus
# ---------------------------------------------------------------------------


def snapshot_to_dbus(snap: Snapshot) -> dict:
    """Serialize a :class:`~manaradix.model.snapshot.Snapshot`."""
    return {
        "id": snap.id,
        "subvolume": snap.subvolume,
        "path": snap.path,
        "date": snap.date.isoformat(),
        "type": snap.type.value,
        "description": snap.description,
        "schedule": snap.schedule,
        "is_read_only": snap.is_read_only,
    }


def subvolume_to_dbus(subvol: Subvolume) -> dict:
    """Serialize a :class:`~manaradix.model.subvolume.Subvolume`."""
    return {
        "id": subvol.id,
        "path": subvol.path,
        "parent_id": subvol.parent_id,
        "uuid": subvol.uuid,
        "parent_uuid": subvol.parent_uuid,
        "is_top_level": subvol.is_top_level,
    }


def balance_status_to_dbus(status) -> dict:
    """Serialize a balance status dataclass (total / relocated / running)."""
    return {
        "total": int(status.total),
        "relocated": int(status.relocated),
        "running": bool(status.running),
    }


def scrub_status_to_dbus(status) -> dict:
    """Serialize a scrub status dataclass (extents / errors / running).

    ``last_started`` is included only when known (ISO timestamp); a
    ``None`` would not be encodable in the ``a{sv}`` reply.
    """
    ret = {
        "data_extents_scrubbed": int(status.data_extents_scrubbed),
        "errors": int(status.errors),
        "running": bool(status.running),
    }
    last_started = getattr(status, "last_started", None)
    if last_started is not None:
        ret["last_started"] = last_started
    return ret


def policy_to_dbus(policy: SnapshotPolicy) -> dict:
    """Serialize a :class:`~manaradix.model.policy.SnapshotPolicy`."""
    ret = policy.retention
    return {
        "subvolume": policy.subvolume,
        "enabled": policy.enabled,
        "schedule_hourly": policy.schedule_hourly,
        "schedule_daily": policy.schedule_daily,
        "schedule_weekly": policy.schedule_weekly,
        "schedule_monthly": policy.schedule_monthly,
        "schedule_boot": policy.schedule_boot,
        "retention": {
            "keep_hourly": ret.keep_hourly,
            "keep_daily": ret.keep_daily,
            "keep_weekly": ret.keep_weekly,
            "keep_monthly": ret.keep_monthly,
            "keep_boot": ret.keep_boot,
            "keep_pre_upgrade": ret.keep_pre_upgrade,
        },
    }


def profile_to_dbus(profile) -> dict:
    """Serialize a setup profile (id / name / description / role)."""
    return {
        "id": profile.id,
        "name": profile.name,
        "description": profile.description,
        "role": profile.role,
    }


def setup_status_to_dbus(*, needs_setup: bool, role: str,
                         default_profile: dict | None, root_is_btrfs: bool,
                         grub_present: bool) -> dict:
    """Build the a{sv} payload for ``SetupStatus`` from precomputed values."""
    return {
        "needs_setup": bool(needs_setup),
        "role": role,
        "default_profile": default_profile,  # a{sv} or empty dict
        "root_is_btrfs": bool(root_is_btrfs),
        "grub_present": bool(grub_present),
    }


# ---------------------------------------------------------------------------
# D-Bus -> model
# ---------------------------------------------------------------------------


def policy_from_args(**kwargs) -> SnapshotPolicy:
    """Build a :class:`~manaradix.model.policy.SnapshotPolicy` from D-Bus args.

    Unknown keyword arguments are ignored so the D-Bus signature can grow
    without breaking older clients; missing ones fall back to the model
    defaults.  All values are coerced from dbus types.
    """
    retention = RetentionPolicy(
        keep_hourly=_i(kwargs.get("keep_hourly", 10)),
        keep_daily=_i(kwargs.get("keep_daily", 7)),
        keep_weekly=_i(kwargs.get("keep_weekly", 4)),
        keep_monthly=_i(kwargs.get("keep_monthly", 3)),
        keep_boot=_i(kwargs.get("keep_boot", 5)),
        keep_pre_upgrade=_i(kwargs.get("keep_pre_upgrade", 3)),
    )
    return SnapshotPolicy(
        subvolume=_s(kwargs["subvolume"]),
        enabled=_b(kwargs.get("enabled", True)),
        schedule_hourly=_b(kwargs.get("schedule_hourly", False)),
        schedule_daily=_b(kwargs.get("schedule_daily", True)),
        schedule_weekly=_b(kwargs.get("schedule_weekly", False)),
        schedule_monthly=_b(kwargs.get("schedule_monthly", False)),
        schedule_boot=_b(kwargs.get("schedule_boot", False)),
        retention=retention,
    )


def policy_to_dbus_roundtrip(policy: SnapshotPolicy) -> SnapshotPolicy:
    """Serialize and deserialize a policy (used by tests and the client)."""
    return policy_from_args(**policy_to_dbus(policy)["retention"],
                            subvolume=policy.subvolume,
                            enabled=policy.enabled,
                            schedule_hourly=policy.schedule_hourly,
                            schedule_daily=policy.schedule_daily,
                            schedule_weekly=policy.schedule_weekly,
                            schedule_monthly=policy.schedule_monthly,
                            schedule_boot=policy.schedule_boot)


def iso_to_datetime(value: str) -> datetime:
    """Parse an ISO-8601 timestamp back into a :class:`datetime`."""
    return datetime.fromisoformat(value)
