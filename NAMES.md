# Matches by file name

Until a theme's first change on the page, and whenever you press **Match
by file name**, each IngeTrazo icon and cursor is paired with the theme's
file named after its key, IngeTrazo's own name for it, case aside:

- **Icons:** `<key>.svg`, anywhere in the theme. These are the names of
  the files in `Icônes originales/`: `line.svg` replaces the Line icon,
  `orbit.svg` the Orbit icon… A theme made from copies of the originals is
  paired on its own.
- **Cursors:** `<key>.svg` in a folder named `cursors`, anywhere in the
  theme: `cursors/line.svg` replaces the Line tool's cursor. In guide mode
  (Tape Measure, Protractor), `cursors/tape+.svg` and
  `cursors/protractor+.svg`. A file in a `cursors` folder is never paired
  with an icon.

A name found in several folders is taken from the folder holding the most
of these names (the theme's set rather than a few spares), then from the
shallowest.

The tools with a cursor: `select`, `line`, `freehand`, `rectangle`,
`rectangle_center`, `rotated_rect`, `circle`, `polygon`, `arc`, `arc3`,
`center_arc`, `pie`, `rotate`, `scale`, `flip`, `followme`, `pushpull`,
`offset`, `fillet`, `move`, `paint`, `eyedropper`, `dimension`, `geopath`,
`orbit`, `pan`, `text`, `text3d`, `eraser`, `tape`, `protractor`,
`section`, `zoom`, `zoom_window`, `position_camera`, `walk`,
`look_around`, `first_person` (as of IngeTrazo 0.5.7; the page lists the
current ones).

## Where a cursor clicks

It depends on the tool the cursor serves, and is found in the drawing
itself:

- the tip toward the bottom left (a pencil, a pipette, a tape's hook, a
  leader): the drawing tools from `line` to `pie`, `rectangle_center`,
  `geopath`, `dimension`, `text`, `eyedropper`, `tape`, `eraser`;
- the point toward the top left (an arrow): `select`, `scale`, `offset`,
  `followme`;
- the drop under a paint pot: `paint`.

Any other cursor clicks where IngeTrazo's own cursor for that tool does
(the centre of the move cross, the magnifier's lens…). If none is found,
it clicks at its centre. In guide mode, without a cursor of its own
(`tape+`), the plain cursor gets a small «+».
