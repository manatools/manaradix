# Activity: the dashboard stopped freezing

A short write-up of the snapshot-usage change shipped after 0.2.2
(October 2026), in the "what, why, how" style used for the project's
release posts.  Free to adapt for websites and blog posts.

---

## The problem

The Dashboard has a *Snapshot Usage* box: how many snapshots there are
and how much disk they really cost.  The count is free.  The sizes are
not -- the only honest source is `btrfs filesystem du`, which has to
walk every extent of every snapshot to work out which blocks are shared
and which are exclusive to one snapshot.

An earlier release already moved that call off page load and behind a
**Measure Usage** button.  But the button still ran it inside the GUI.
On a root with a few months of snapshots, one click meant a window that
did not repaint, did not react, and looked crashed for as long as btrfs
needed -- and the call had a 60-second cap, so some systems never got a
number at all.

## The change

The measurement moved out of the GUI entirely.

* A small collector, `manaradix usage collect`, does the measuring.
  A systemd timer runs it once a day at idle I/O priority, so it never
  competes with real work.
* The collector writes its result to a SQLite file.  The GUI only ever
  reads that file, read-only, and shows **when the numbers were last
  updated**.
* The button is now **Force Usage Data Update**.  It asks systemd to run
  the collector in the background and returns immediately; the section
  switches to *Collecting usage data...* and shows the new figures on the
  next refresh.

## How it hangs together

One `btrfs filesystem du` call measures the snapshot directory and every
snapshot in a single pass, so we get the totals and the per-snapshot
figures for the price of one walk.  The store keeps the previous numbers
if a run fails and records why, so a bad night shows up as a clear
message rather than a blank box.

The same data is available from the command line
(`manaradix usage status`, `usage show`) and over D-Bus (`GetUsage`,
`RefreshUsage`), so the tray applet and scripts can use it without
touching btrfs.

## What we learned

The fix for "slow" was not to make the call faster -- it was to stop
asking the user's window to wait for it.  Anything that can take minutes
belongs in a service that writes down its answer; the interface should
just report the latest one, honestly dated.
