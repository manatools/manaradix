"""Manaradix — Settings page.

Global configuration: boot-from-snapshot, snapshot directory,
compression defaults, and filesystem maintenance operations.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from manaradix.core.pre_upgrade import is_installed, install_script
from manaradix.i18n import _
from manaradix.ui.model import PageModel, Widget

if TYPE_CHECKING:
    from manaradix.module import AppContext

logger = logging.getLogger(__name__)


class SettingsPage:
    """Global settings and filesystem maintenance page."""

    page_id = "settings"
    title = _("Settings")

    def __init__(self, app_context: AppContext) -> None:
        self.app = app_context

    # -- build_model --------------------------------------------------------

    def build_model(self):
        """Return a ``PageModel`` for global settings.

        Two side-by-side columns (settings | system) so the page does not
        stretch the window past small screens.
        """
        sections = [
            self._global_settings_section(),
            self._compression_section(),
            self._maintenance_section(),
            self._grub_section(),
        ]
        return PageModel(
            sections=[s for s in sections if s is not None],
            columns=2,
        )

    # -- Section builders ---------------------------------------------------

    def _global_settings_section(self) -> dict:
        """Global configuration fields."""
        return {
            "title": _("Global Configuration"),
            "column": 0,
            "fields": [
                Widget.text(
                    id="setting_default_subvolume",
                    label=_("Default subvolume"),
                    value=self.app.config_store.default_subvolume,
                ),
                Widget.text(
                    id="setting_snapshot_dir",
                    label=_("Snapshot directory"),
                    value=self.app.config_store.snapshot_dir,
                ),
                Widget.checkbox(
                    id="setting_boot_from_snapshot",
                    label=_("Boot-from-snapshot (GRUB menu)"),
                    checked=self.app.config_store.boot_from_snapshot,
                    on_change=self.on_boot_from_snapshot_toggle,
                ),
                Widget.checkbox(
                    id="setting_snapshot_before_upgrade",
                    label=_("Snapshot / before system upgrades"),
                    checked=self.app.config_store.snapshot_before_upgrade,
                    on_change=self.on_snapshot_before_upgrade_toggle,
                ),
                Widget.button(
                    id="btn_save_settings",
                    label=_("Save Settings"),
                    callback=self.on_save_settings,
                ),
            ],
        }

    def _compression_section(self) -> dict | None:
        """Compression status and management."""
        subvol = self.app.config_store.default_subvolume
        try:
            algo = self.app.btrfs_ops.get_compression(subvol)
        except Exception as exc:
            logger.warning("Could not read compression for %s: %s", subvol, exc)
            algo = None

        current = algo if algo else "none"
        return {
            "title": _("Compression"),
            "column": 0,
            "fields": [
                Widget.label(
                    id="compression_current",
                    text=_("Compression for %s: %s") % (subvol, current),
                ),
                Widget.dropdown(
                    id="compression_algo",
                    label=_("Algorithm"),
                    options=["", "zstd", "lzo", "zlib"],
                    selected=algo or "",
                ),
                Widget.button(
                    id="btn_set_compression",
                    label=_("Apply Compression"),
                    callback=self.on_set_compression,
                ),
            ],
        }

    def _maintenance_section(self) -> dict:
        """Balance, scrub, and defrag controls."""
        # Read current statuses
        balance_status = "idle"
        scrub_status = "idle"
        try:
            bs = self.app.btrfs_ops.balance_status()
            if bs.running:
                balance_status = _("running (%s/%s relocated)") % (bs.relocated, bs.total)
        except Exception:
            pass
        try:
            ss = self.app.btrfs_ops.scrub_status()
            if ss.running:
                scrub_status = _("running (%s extents, %s errors)") % (ss.data_extents_scrubbed, ss.errors)
            elif ss.errors:
                scrub_status = _("errors: %s") % ss.errors
        except Exception:
            pass

        return {
            "title": _("Filesystem Maintenance"),
            "column": 1,
            "fields": [
                Widget.label(
                    id="balance_status",
                    text=_("Balance: %s") % balance_status,
                ),
                Widget.button(
                    id="btn_balance_start",
                    label=_("Start Balance"),
                    callback=self.on_start_balance,
                ),
                Widget.button(
                    id="btn_balance_cancel",
                    label=_("Cancel Balance"),
                    callback=self.on_cancel_balance,
                ),
                Widget.label(
                    id="scrub_status",
                    text=_("Scrub: %s") % scrub_status,
                ),
                Widget.button(
                    id="btn_scrub_start",
                    label=_("Start Scrub"),
                    callback=self.on_start_scrub,
                ),
                Widget.button(
                    id="btn_scrub_cancel",
                    label=_("Cancel Scrub"),
                    callback=self.on_cancel_scrub,
                ),
                Widget.button(
                    id="btn_defrag",
                    label=_("Defragment /"),
                    callback=self.on_defrag_root,
                ),
            ],
        }

    def _grub_section(self) -> dict:
        """GRUB integration controls."""
        installed = self.app.grub_integration.is_installed()

        return {
            "title": _("GRUB Boot Menu"),
            "column": 1,
            "fields": [
                Widget.label(
                    id="grub_status",
                    text=_("GRUB snapshot script: %s") % (_("installed") if installed else _("not installed")),
                ),
                Widget.button(
                    id="btn_grub_install",
                    label=_("Install GRUB Script"),
                    callback=self.on_install_grub,
                ),
                Widget.button(
                    id="btn_grub_uninstall",
                    label=_("Remove GRUB Script"),
                    callback=self.on_uninstall_grub,
                ),
                Widget.button(
                    id="btn_update_grub",
                    label=_("Update GRUB Config"),
                    callback=self.on_update_grub,
                ),
            ],
        }

    # -- Callbacks: global settings -----------------------------------------

    def on_save_settings(self) -> None:
        """Persist global settings to config file.

        In a full UI the widget values would be read from the form fields.
        """
        try:
            self.app.config_store.save()
            logger.info("Settings saved to %s", self.app.config_store._path)
        except Exception as exc:
            logger.error("Failed to save settings: %s", exc)

    def on_boot_from_snapshot_toggle(self, value: bool | None = None) -> None:
        """Toggle boot-from-snapshot and update GRUB if needed.

        Args:
            value: The checkbox's new state (handed by the renderer);
                None keeps the old toggle semantics for callers without
                a widget.
        """
        if value is None:
            value = not self.app.config_store.boot_from_snapshot
        self.app.config_store.boot_from_snapshot = bool(value)
        if self.app.config_store.boot_from_snapshot:
            try:
                self.app.grub_integration.install_script()
                self.app.grub_integration.update_grub()
            except Exception as exc:
                logger.error("Failed to enable GRUB integration: %s", exc)

    def on_snapshot_before_upgrade_toggle(self, value: bool | None = None) -> None:
        """Toggle pre-upgrade snapshots.

        The rpm trigger is always installed with the package; the config
        flag is the whole activation.  A development checkout has no
        RPM, though: enabling installs the missing trigger script on
        demand (a failed install leaves the flag off).  The change is
        persisted by the Save Settings button, like the other global
        settings.

        Args:
            value: The checkbox's new state (handed by the renderer);
                None keeps the old toggle semantics for callers without
                a widget.
        """
        if value is None:
            value = not self.app.config_store.snapshot_before_upgrade
        value = bool(value)
        if value and not is_installed():
            try:
                install_script()
            except Exception as exc:
                # Without the script the trigger would not fire: keep the
                # flag off rather than promising snapshots that cannot
                # happen.
                logger.error("Failed to install pre-upgrade trigger script: %s", exc)
                return
        self.app.config_store.snapshot_before_upgrade = value

    # -- Callbacks: compression ---------------------------------------------

    def on_set_compression(self) -> None:
        """Apply the selected compression algorithm.

        In a full UI the value would be read from the dropdown widget.
        """
        # The algorithm and path would come from the widgets in a full UI.
        logger.info("Compression apply requested (stub)")

    # -- Callbacks: maintenance ---------------------------------------------

    def on_start_balance(self) -> None:
        """Start a balance on the default subvolume."""
        try:
            self.app.btrfs_ops.balance_start()
            logger.info("Balance started")
        except Exception as exc:
            logger.error("Failed to start balance: %s", exc)

    def on_cancel_balance(self) -> None:
        """Cancel a running balance."""
        try:
            self.app.btrfs_ops.balance_cancel()
            logger.info("Balance cancelled")
        except Exception as exc:
            logger.error("Failed to cancel balance: %s", exc)

    def on_start_scrub(self) -> None:
        """Start a scrub."""
        try:
            self.app.btrfs_ops.scrub_start()
            logger.info("Scrub started")
        except Exception as exc:
            logger.error("Failed to start scrub: %s", exc)

    def on_cancel_scrub(self) -> None:
        """Cancel a running scrub."""
        try:
            self.app.btrfs_ops.scrub_cancel()
            logger.info("Scrub cancelled")
        except Exception as exc:
            logger.error("Failed to cancel scrub: %s", exc)

    def on_defrag_root(self) -> None:
        """Defragment the root subvolume."""
        try:
            self.app.btrfs_ops.defragment("/")
            logger.info("Defragmentation started on /")
        except Exception as exc:
            logger.error("Failed to defragment: %s", exc)

    # -- Callbacks: GRUB ----------------------------------------------------

    def on_install_grub(self) -> None:
        """Install the GRUB snapshot boot script."""
        try:
            self.app.grub_integration.install_script()
            logger.info("GRUB script installed")
        except Exception as exc:
            logger.error("Failed to install GRUB script: %s", exc)

    def on_uninstall_grub(self) -> None:
        """Remove the GRUB snapshot boot script."""
        try:
            self.app.grub_integration.uninstall_script()
            logger.info("GRUB script removed")
        except Exception as exc:
            logger.error("Failed to remove GRUB script: %s", exc)

    def on_update_grub(self) -> None:
        """Regenerate the GRUB configuration."""
        try:
            self.app.grub_integration.update_grub()
            logger.info("GRUB configuration updated")
        except Exception as exc:
            logger.error("Failed to update GRUB: %s", exc)
