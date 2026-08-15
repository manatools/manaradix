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
    def test_no_findings_passive(self):
        assert icon_state([]) == (SNI_STATUS_PASSIVE, ICON_NORMAL)

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
