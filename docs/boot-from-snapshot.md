# Boot from snapshot (GRUB integration)

Manaradix can install a GRUB script (`/etc/grub.d/41_manaradix`) that adds
a "Manaradix Snapshots" submenu to the bootloader, listing read-only
snapshots as bootable entries.

```bash
# From the GUI: Settings → GRUB Boot Menu → Install GRUB Script

# Or programmatically:
from manaradix.core.grub_integration import GrubIntegration

grub = GrubIntegration()
grub.install_script()
grub.update_grub()
```

Each entry boots with `rootflags=subvol=.snapshots/<id>/root ro`, giving
you a read-only recovery environment. The script only lists snapshots of
the root subvolume; pre-upgrade and other read-only snapshot types appear
in the menu.

Note: this requires GRUB (the Mageia bootloader); other bootloaders are
detected and the option is hidden in the setup wizard accordingly.
