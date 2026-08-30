# Activity: the tray menu's About item

A short write-up of the tray-menu extension shipped after 0.2.0
(August 2026), in the "what, why, how" style used for the project's
release posts.  Free to adapt for websites and blog posts.

---

## The problem

The tray menu answered two questions — "open the GUI" and "run the
checks now" — plus Quit.  The question it could not answer was the one
every other entry point answers: *what is this thing, and which version
is running?*  A user who inherited the applet from the package
installation (it autostarts at login) had no way to identify it from
the tray; the About information existed only inside the GUI.

## The change

The `com.canonical.dbusmenu` layout gained an **About** item between
*Check now* and the separator.  Activating it raises a freedesktop
notification carrying what the GUI's About page says in miniature:

- the running version (`Manaradix <version>` as the summary);
- the one-line description (the same translated string the About page
  uses, so no new translation work);
- the project website;
- an **Open manaradix** action button — the notification reuses the
  existing action wiring, so clicking it launches the GUI and closes
  the notification.

No new translation strings were needed: *About*, the description, and
*Open manaradix* were already in the catalogue, and the version summary
is brand-plus-number (untranslatable by design).  A failed notification
daemon (rare, but possible in a minimal session) is logged and
swallowed — a cosmetic action must never take the watchdog down.

## Design note

Why a notification rather than launching the GUI on its About page?
The applet is a read-only observer; reaching "open the GUI *at a
specific page*" would need a new CLI/renderer contract, and the
notification is self-sufficient — identification is a one-glance need,
while the full About page (author, license, GPL notice) stays one
*Open manaradix* click away.  The renderer already re-opens the GUI
through the same `_open_gui` seam the icon Activate and notification
actions use, so the menu now covers the full loop: launch, check,
identify, quit.
