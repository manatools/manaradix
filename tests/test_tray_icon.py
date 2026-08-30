"""Tests for the SNI tray icon: pure state + signal emission."""

from unittest.mock import MagicMock

import pytest

from manaradix.core.health import HealthFinding
from manaradix.tray.status_icon import (
    ICON_ATTENTION,
    ICON_NORMAL,
    SNI_STATUS_ACTIVE,
    SNI_STATUS_NEEDS_ATTENTION,
    SNI_STATUS_PASSIVE,
    StatusIcon,
    icon_available,
    icon_state,
)


def _finding(severity: str, finding_id: str = "space:/") -> HealthFinding:
    return HealthFinding(
        check="space", finding_id=finding_id, severity=severity,
        summary="test", details="test body",
    )


class TestIconState:
    def test_no_findings_active_by_default(self):
        # default: the icon stays visible and clickable when healthy
        assert icon_state([]) == (SNI_STATUS_ACTIVE, ICON_NORMAL)

    def test_no_findings_passive_with_hide_when_ok(self):
        assert icon_state([], hide_when_ok=True) == (
            SNI_STATUS_PASSIVE, ICON_NORMAL)

    def test_warning_active(self):
        assert icon_state([_finding("warning")]) == (
            SNI_STATUS_ACTIVE, ICON_NORMAL)

    def test_critical_needs_attention(self):
        status, icon = icon_state([_finding("critical")])
        assert status == SNI_STATUS_NEEDS_ATTENTION
        # the attention icon is only used when actually shipped
        assert icon == (ICON_ATTENTION if icon_available(ICON_ATTENTION)
                        else ICON_NORMAL)

    def test_critical_wins_over_warnings(self):
        status, _icon = icon_state([_finding("warning"), _finding("critical")])
        assert status == SNI_STATUS_NEEDS_ATTENTION


class TestStatusIcon:
    def test_defaults(self):
        icon = StatusIcon()
        assert icon.status == SNI_STATUS_PASSIVE
        assert icon.icon_name == ICON_NORMAL

    def test_set_status_no_signal_when_unchanged(self):
        icon = StatusIcon()
        icon._object = MagicMock()
        icon.set_status(SNI_STATUS_PASSIVE)
        icon._object.NewStatus.assert_not_called()

    def test_set_status_emits_signal_on_change(self):
        icon = StatusIcon()
        icon._object = MagicMock()
        icon.set_status(SNI_STATUS_ACTIVE)
        assert icon.status == SNI_STATUS_ACTIVE
        icon._object.NewStatus.assert_called_once_with(SNI_STATUS_ACTIVE)

    def test_set_icon_emits_signal(self):
        icon = StatusIcon()
        icon._object = MagicMock()
        icon.set_icon(ICON_ATTENTION)
        icon._object.NewIcon.assert_called_once()

    def test_set_icon_unchanged_no_signal(self):
        icon = StatusIcon()
        icon._object = MagicMock()
        icon.set_icon(ICON_NORMAL)
        icon._object.NewIcon.assert_not_called()

    def test_set_tooltip_emits_signal_only_on_change(self):
        icon = StatusIcon()
        icon._object = MagicMock()
        icon.set_tooltip("Title", "Body")
        icon.set_tooltip("Title", "Body")  # unchanged
        assert icon._object.NewToolTip.call_count == 1

    def test_state_pure_without_export(self):
        icon = StatusIcon()
        icon.set_status(SNI_STATUS_NEEDS_ATTENTION)
        icon.set_icon(ICON_ATTENTION)
        icon.set_tooltip("T", "B")
        assert icon.status == SNI_STATUS_NEEDS_ATTENTION
        assert icon.icon_name == ICON_ATTENTION
        assert (icon._tooltip_title, icon._tooltip_body) == ("T", "B")
        assert icon._object is None

    def test_export_assigns_object(self):
        icon = StatusIcon(service_name="org.kde.StatusNotifierItem-1-1")
        icon.export(MagicMock(), watcher=False)
        assert icon._object is not None

    def test_export_claims_the_service_name(self):
        icon = StatusIcon(service_name="org.kde.StatusNotifierItem-1-1")
        bus = MagicMock()
        icon.export(bus, watcher=False)
        import dbus.bus
        bus.request_name.assert_called_once_with(
            "org.kde.StatusNotifierItem-1-1",
            dbus.bus.NAME_FLAG_DO_NOT_QUEUE)

    def test_export_real_dbus_decorator_signatures(self):
        """Regression: dbus-python validates ``in_signature`` against the
        positional parameters at decoration time; a mismatch raises
        ValueError while the object class is being defined (0.2.0 shipped
        a GetLayout crash of exactly this kind).  Real decorators only run
        with python3-dbus installed."""
        pytest.importorskip("dbus")
        icon = StatusIcon(service_name="org.kde.StatusNotifierItem-1-1")
        icon.export(MagicMock(), watcher=False)  # must not raise

    def test_exported_properties_track_state_changes(self):
        """Regression: the setters used to emit the New* signals without
        updating the exported property dict, so hosts re-reading the
        property after the signal always saw the export-time snapshot."""
        pytest.importorskip("dbus")
        icon = StatusIcon(service_name="org.kde.StatusNotifierItem-1-1")
        icon.export(MagicMock(), watcher=False)

        def get(prop):
            return icon._object.Get("org.kde.StatusNotifierItem", prop)

        assert get("Status") == SNI_STATUS_PASSIVE
        icon.set_status(SNI_STATUS_ACTIVE)
        assert get("Status") == SNI_STATUS_ACTIVE
        icon.set_icon(ICON_ATTENTION)
        assert get("IconName") == ICON_ATTENTION
        icon.set_tooltip("Manaradix: 1 issue(s)", "space problem")
        _name, _pixmap, title, body = get("ToolTip")
        assert title == "Manaradix: 1 issue(s)"
        assert body == "space problem"
