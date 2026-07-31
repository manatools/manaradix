"""Snapshot policy and retention models.

Policies define automated snapshot schedules and cleanup rules.
They are persisted to ``/etc/manaradix/config.toml``.
"""

from dataclasses import dataclass, field


@dataclass
class RetentionPolicy:
    """How many snapshots to keep per timeframe.

    Attributes:
        keep_hourly: Number of hourly snapshots to retain.
        keep_daily: Number of daily snapshots to retain.
        keep_weekly: Number of weekly snapshots to retain.
        keep_monthly: Number of monthly snapshots to retain.
        keep_boot: Number of boot snapshots to retain.
    """

    keep_hourly: int = 10
    keep_daily: int = 7
    keep_weekly: int = 4
    keep_monthly: int = 3
    keep_boot: int = 5

    def validate(self) -> list[str]:
        """Validate retention values. Returns a list of error messages."""
        errors = []
        for field_name in (
            "keep_hourly",
            "keep_daily",
            "keep_weekly",
            "keep_monthly",
            "keep_boot",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, int) or value < 0:
                errors.append(f"{field_name} must be a non-negative integer, got {value!r}")
        return errors


@dataclass
class SnapshotPolicy:
    """Automated snapshot schedule for a subvolume.

    Attributes:
        subvolume: The subvolume path this policy applies to.
        enabled: Whether the policy is active.
        schedule_hourly: Take hourly snapshots.
        schedule_daily: Take daily snapshots.
        schedule_weekly: Take weekly snapshots.
        schedule_monthly: Take monthly snapshots.
        schedule_boot: Take a snapshot on every boot.
        retention: Retention settings for this policy.
    """

    subvolume: str
    enabled: bool = True
    schedule_hourly: bool = False
    schedule_daily: bool = True
    schedule_weekly: bool = False
    schedule_monthly: bool = False
    schedule_boot: bool = False
    retention: RetentionPolicy = field(default_factory=RetentionPolicy)

    @property
    def active_schedules(self) -> list[str]:
        """Return list of active schedule names (e.g. ``['hourly', 'daily']``)."""
        schedules = []
        if self.schedule_hourly:
            schedules.append("hourly")
        if self.schedule_daily:
            schedules.append("daily")
        if self.schedule_weekly:
            schedules.append("weekly")
        if self.schedule_monthly:
            schedules.append("monthly")
        if self.schedule_boot:
            schedules.append("boot")
        return schedules

    def validate(self) -> list[str]:
        """Validate policy; returns list of error messages."""
        errors = []
        if not self.subvolume:
            errors.append("subvolume must not be empty")
        if not self.active_schedules:
            errors.append("at least one schedule must be enabled")
        errors.extend(self.retention.validate())
        return errors
