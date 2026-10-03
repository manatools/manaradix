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

## Design note

Why not open the GUI *on its About page* from the About item, or
launch the GUI on a double click and show the menu on a single one?
Both would reintroduce the thing being removed — a gesture whose
outcome is a privileged process — for a marginal gain.  The applet is a
read-only observer whose job is to tell the user something is wrong and
then get out of the way.  A menu is the honest interface for that: four
labelled choices, nothing implicit, and the expensive one clearly
marked.
