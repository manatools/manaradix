"""D-Bus interface constants and the method-to-action mapping.

The service exposes a single object at ``/org/mageia/manaradix`` with
interface ``org.mageia.manaradix`` on the system bus.  Every method is
authorized against one of the four ``org.mageia.manaradix.*`` polkit
actions; ``METHOD_ACTIONS`` is the single source of truth used by both
the service (authorization) and the client (error mapping) and is
cross-checked against the shipped polkit policy file by tests.
"""

DBUS_SERVICE_NAME = "org.mageia.manaradix"
DBUS_OBJECT_PATH = "/org/mageia/manaradix"
DBUS_INTERFACE = "org.mageia.manaradix"

# Error names: org.mageia.manaradix.Error.<Kind>
ERROR_PREFIX = "org.mageia.manaradix.Error"
ERROR_AUTH_REQUIRED = f"{ERROR_PREFIX}.AuthRequired"
ERROR_NOT_FOUND = f"{ERROR_PREFIX}.NotFound"
ERROR_INVALID_ARGS = f"{ERROR_PREFIX}.InvalidArgs"
ERROR_FAILED = f"{ERROR_PREFIX}.Failed"

# Polkit action ids (must match data/polkit/org.mageia.manaradix.policy).
ACTION_READ = "org.mageia.manaradix.read"
ACTION_MANAGE_SNAPSHOTS = "org.mageia.manaradix.manage-snapshots"
ACTION_MANAGE_POLICIES = "org.mageia.manaradix.manage-policies"
ACTION_MANAGE_FILESYSTEM = "org.mageia.manaradix.manage-filesystem"

# D-Bus method name -> polkit action id.  All four tiers:
#   read (no auth), manage-snapshots, manage-policies, manage-filesystem.
METHOD_ACTIONS: dict[str, str] = {
    # Tier 1: read
    "Ping": ACTION_READ,
    "ListSnapshots": ACTION_READ,
    "GetSnapshot": ACTION_READ,
    "ListSubvolumes": ACTION_READ,
    "FilesystemUsage": ACTION_READ,
    "FilesystemShow": ACTION_READ,
    "BtrfsMounts": ACTION_READ,
    "BalanceStatus": ACTION_READ,
    "ScrubStatus": ACTION_READ,
    "GetDeviceStats": ACTION_READ,
    "GetCompression": ACTION_READ,
    "ListPolicies": ACTION_READ,
    "GetConfig": ACTION_READ,
    "SetupStatus": ACTION_READ,
    "ListSetupProfiles": ACTION_READ,
    "GrubIsInstalled": ACTION_READ,
    "GrubListEntries": ACTION_READ,
    # Tier 2: manage snapshots
    "CreateSnapshot": ACTION_MANAGE_SNAPSHOTS,
    "DeleteSnapshot": ACTION_MANAGE_SNAPSHOTS,
    "PruneSnapshots": ACTION_MANAGE_SNAPSHOTS,
    "RenameSnapshot": ACTION_MANAGE_SNAPSHOTS,
    "RestoreSnapshot": ACTION_MANAGE_SNAPSHOTS,
    "BrowseSnapshot": ACTION_MANAGE_SNAPSHOTS,
    "Unbrowse": ACTION_MANAGE_SNAPSHOTS,
    "ExportSnapshot": ACTION_MANAGE_SNAPSHOTS,
    "ImportSnapshot": ACTION_MANAGE_SNAPSHOTS,
    # Tier 3: manage policies
    "ApplyPolicy": ACTION_MANAGE_POLICIES,
    "RemovePolicy": ACTION_MANAGE_POLICIES,
    "SetConfig": ACTION_MANAGE_POLICIES,
    "SetupApply": ACTION_MANAGE_POLICIES,
    "GrubInstall": ACTION_MANAGE_POLICIES,
    "GrubUninstall": ACTION_MANAGE_POLICIES,
    "GrubUpdate": ACTION_MANAGE_POLICIES,
    # Tier 4: manage filesystem
    "BalanceStart": ACTION_MANAGE_FILESYSTEM,
    "BalanceCancel": ACTION_MANAGE_FILESYSTEM,
    "ScrubStart": ACTION_MANAGE_FILESYSTEM,
    "ScrubCancel": ACTION_MANAGE_FILESYSTEM,
    "Defragment": ACTION_MANAGE_FILESYSTEM,
    "SetCompression": ACTION_MANAGE_FILESYSTEM,
    "CreateSubvolume": ACTION_MANAGE_FILESYSTEM,
    "DeleteSubvolume": ACTION_MANAGE_FILESYSTEM,
    "SetDefaultSubvolume": ACTION_MANAGE_FILESYSTEM,
}
