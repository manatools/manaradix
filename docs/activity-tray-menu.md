# Activity: the tray icon became a menu

A short write-up of the tray-icon change shipped after 0.2.2
(October 2026), in the "what, why, how" style used for the project's
release posts.  Free to adapt for websites and blog posts.

---

## The problem

Clicking the manaradix tray icon launched the GUI.  That was the
applet's very first feature — "quick GUI access" — and it was the wrong
default for two reasons.

The first is cost.  The GUI elevates: it goes through `pkexec`, so a
click meant a password prompt, a new privileged process, and a window
on screen.  A misclick in a crowded system tray is cheap; a misclick
that starts an authenticated system utility is not.

The second is discoverability.  Over three releases the applet grew
real actions — *Check now*, then *About*, alongside *Quit* — but they
all lived in the context menu, behind a right click.  The obvious
gesture did the one thing the user was least likely to want, and the
rest of the applet was invisible unless they thought to right-click a
16-pixel icon.

## The change

A click on the icon now pops up the applet menu:

- **Open manaradix** — launches the GUI (what the click used to do);
- **Check now** — re-runs every enabled health check immediately;
- **About** — a notification with the running version, the one-line
  description, and the project website;
- **Quit** — stops the applet.

The GUI is still one click away; it is simply no longer the *only*
thing the icon can do, and it no longer happens by accident.

## How it works

The StatusNotifierItem specification has a property for exactly this:
`ItemIsMenu`.  When an item sets it, the tray host is told not to call
`Activate` at all and to pop up the menu named by the `Menu` property
instead.  The applet's item now exports `ItemIsMenu = true`, and the
`Activate` / `SecondaryActivate` / `ContextMenu` callbacks that used to
point at `_open_gui` are left unwired — the dbusmenu is the single
entry point, so a host that ignores `ItemIsMenu` simply does nothing on
a plain click and still shows the menu on right-click.  The property is
a `StatusIcon` constructor argument (`item_is_menu`, default `True`)
rather than a constant, so the launcher behaviour stays one keyword
away and a test can assert both branches.

The menu layout itself did not change — it was already the right four
items.  What did change is the amount of the `com.canonical.dbusmenu`
interface the hosts actually exercise once the menu is on the primary
click path, and that turned up a latent bug worth recording.

**Two methods called `Get`.**  The exported `/MenuBar` object implements
both `org.freedesktop.DBus.Properties` (`Get(ss)`, for `version` and
`status`) and `com.canonical.dbusmenu` (`Get(ias)`, for one item's
properties).  dbus-python resolves an incoming call by walking the
Python MRO for an attribute with the method's *name* — so two `def
Get` in one class body left only the second, and `Properties.Get` on
the menu object answered `UnknownMethod`.  The fix is structural: the
Properties pair moved into a base class, and the MRO walk now skips the
derived (dbusmenu) `Get` when the requested interface does not match.
A regression test resolves both through `dbus.service._method_lookup`
and asserts each lands on the right interface.

**The popup handshake.**  Hosts (libdbusmenu-qt, plasmashell) call
`AboutToShow` before every popup and `GetGroupProperties` to batch-read
the item properties.  Both were missing; an `UnknownMethod` there is
logged by the host and, in some versions, ends the popup.  The applet
now answers `AboutToShow` with `false` (the layout is static, so there
is nothing to refresh) and implements `GetGroupProperties` over the
same pure `MenuModel` the single-item `Get` uses — with an empty id
list meaning "all items", which is what the hosts send.  A
`LayoutUpdated` signal is declared for completeness.

All of the wire-level work is covered without a bus: the tests execute
the real dbus-python decorators under `importorskip` so a signature
drift fails CI, and drive the resulting object directly.

## The two bugs after the first fix

The menu *still* did not open on Plasma after the first two releases of
this change, and the trail to the root cause is worth recording because
every step looked like a working system.

**The Menu property.**  The StatusNotifierItem specification types the
`Menu` property as `(so)` — a service name plus an object path.  We
exported exactly that.  But plasma's `StatusNotifierItemSource` reads
it with `properties["Menu"].value<QDBusObjectPath>().path()`, and that
cast on a struct returns an *empty path with no error* — so plasma
never created its menu importer, never read a single byte of our
dbusmenu, and fell back to `ContextMenu()` on every click.  The
working items on a real desktop (KStatusNotifierItem, mgaapplet) export
a bare object path; we do now too.  Reading the host's source settled
in one afternoon what two more round-trip "fixes" would not have.

**The GetLayout shape.**  With the importer finally running, the next
failure was silent in a different way.  `com.canonical.dbusmenu.xml`
declares `GetLayout`'s reply as `u(ia{sv}av)` — a uint32 revision and
the root item as a *plain struct*, with only the *children*
variant-wrapped.  Our first reply was flat (`i`, `a(ia{sv})`), which
plasma could not cast; the corrected reply wrapped the root in a
variant too, which made `QDBusPendingReply<uint, DBusMenuLayoutItem>`
itself invalid — `reply.isValid()` false, empty menu emitted, nothing
logged anywhere.  A menu that imports cleanly, answers every call, and
shows nothing is worse to debug than one that errors loudly.

The tool that finally cracked it was not bus monitoring (Qt sends
addressed-to-unique-name traffic that passively attached monitors
miss) but a server-side trace: hook
`dbus.service.Object._message_cb` in the applet itself and log every
inbound call.  Against the fixed code plasma's true handshake became
visible — `GetAll`, `GetLayout`, then on every click `AboutToShow`
followed by `Event`, and selecting *Open manaradix* dispatching
through to the GUI.  The same trace, pointed at a GNOME session
(`gnome-shell-extension-appindicator`), showed the identical import
sequence — the canonical shape is host-agnostic by construction, and
the menu now works on both desktops.

## The icon

With the menu working, a cosmetic-but-real problem surfaced on GNOME:
the logo is black line-work on a transparent background, and GNOME's
dark top bar swallowed it — the icon was there, clickable, and
invisible.  The applet now ships `manaradix-tray`, the glyph on a
light rounded plate with a thin border, and uses it as its normal
icon.  The attention variant already carried a filled red mark and
needed nothing; the plain logo remains for surfaces that draw their
own background (menu items, the About page, the window icon).

## Design note

Why not open the GUI *on its About page* from the About item, or
launch the GUI on a double click and show the menu on a single one?
Both would reintroduce the thing being removed — a gesture whose
outcome is a privileged process — for a marginal gain.  The applet is a
read-only observer whose job is to tell the user something is wrong and
then get out of the way.  A menu is the honest interface for that: four
labelled choices, nothing implicit, and the expensive one clearly
marked.
