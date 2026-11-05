# Activity: a donut chart for internal filesystem usage

A short write-up of the Dashboard change that followed the chart widgets
landing in python-manatools (`feat/charts`, November 2026), in the
"what, why, how" style used for the project's release posts.  Free to
adapt for websites and blog posts.

---

## The problem

The Dashboard's *Internal Filesystem Usage* section was a table: one row
per btrfs chunk type (Data, Metadata, System) per filesystem, each with
its allocated size, used bytes and a "Used %" column.  Accurate, but it
answered the wrong question.  "92.7% used" on the Data row reads like a
full disk, when it only means the *allocated* data chunks are nearly
full — the device itself may still be 90% unallocated.  Getting the real
picture meant mentally adding three rows and comparing them against a
device size the table never showed.

## The change

The table is now a **donut chart** per filesystem, drawn with the new
`YChartPie` widget from python-manatools.  The slices split the whole
device:

- **Data**, **Metadata**, **System** — bytes actually used by each
  chunk type;
- **Allocated, unused** — space inside allocated chunks that holds no
  data yet (the gap the old "Used %" column was really measuring);
- **Unallocated** — raw device space no chunk claims yet, the headroom
  btrfs can still hand out.

The hole of the donut shows the overall used share (`9.1%`), the
legend lists every slice with a human size (`17.6 GiB`) and its share,
and hovering a slice shows the same as a tooltip.  Colors are fixed per
slice (Okabe-Ito, colorblind-safe), so Data is always the same blue on
every filesystem.  The chart renders natively on all frontends: QPainter
on Qt, cairo on GTK, a bar-style legend on ncurses.

With more than one btrfs filesystem mounted, a *Filesystem* dropdown
picks which one the chart shows — one donut in a half-width cell reads
far better than three squeezed together.

## How it works

- `btrfs filesystem usage -b` already printed `Device unallocated:`; the
  parser now keeps it.  It is a raw figure, so DUP/RAID chunks (which
  occupy twice their logical size) are accounted for correctly; older
  output without it falls back to `device size − allocated`.
- manaradix's declarative UI layer gained a `Widget.pie()` descriptor
  (label, slices, donut ratio, center text, value formatter).  Pages
  stay pure data; only the renderer touches the widget.
- The renderer maps the descriptor onto `createChartPie()`.  When the
  installed python-manatools has no chart support, it renders the
  slices as a plain *Section / Size / %* table instead, so the
  Dashboard keeps working on older packages.

## Translations

Two new strings — *Allocated, unused* and *Unallocated* — ship
translated in Italian and Chinese.
