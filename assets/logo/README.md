# The mark

**A prism, solid.** One beam goes in, three rays come out, and one of them is
brighter than the rest — separate, then choose. That is the product in one
image, and it is the half most routers skip: everything else in the category
draws the switching and nothing draws the measuring.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="mark-dark.svg">
  <img src="mark-light.svg" alt="" width="72">
</picture>

## The construction

Every file is generated from one set of coordinates, so the favicon and the
README header cannot drift apart.

| | |
|---|---|
| triangle | equilateral, side 40, optically centred on a 64 grid — apex `(32,11)`, base `(12,45.5)–(52,45.5)` |
| beam in | enters the **left face** at `y=32`. Never a vertex — light striking a corner is the one thing a prism does not do |
| rays out | leave the **right face** from a single point, `(44,32)` |
| fan | symmetric, ±14 over 18 |
| stroke weights | two: `3.2` structure, `5.4` chosen |
| colours | two: one structural, one accent |

Everything sits on the `y=32` centreline, so the mark is optically level rather
than leaning.

## The files

| File | Use it for |
|---|---|
| `mark-light.svg` · `mark-dark.svg` | anywhere the ground is fixed and known — **these are the default** |
| `lockup-light.svg` · `lockup-dark.svg` | mark plus wordmark — **only where the page can load the typeface**, i.e. inlined or in a design tool. Not through `<img>` |
| `favicon.svg` | browser tab |
| `mark.svg` | **inline only** — structure is `currentColor`, accent is `--sr-accent` |
| `mark-mono.svg` | **inline only** — one ink, for a stamp, an etch, or a terminal |
| `contact-sheet.html` | open it to see every file at every size on both grounds |

**An SVG shown through `<img>` is sandboxed: it cannot fetch a webfont and it
cannot see the host page's colour.** Both halves of that have now bitten this
repo once each.

- **The lockups set the wordmark as live text**, so through `<img>` it falls back
  to whatever face the reader happens to have — a different wordmark per machine.
  In a README, use the **mark** above a real Markdown heading; GitHub renders the
  heading in its own font and every reader sees the same thing.
- **`mark.svg` and `mark-mono.svg` must be inlined, never loaded through `<img>`** —
  `currentColor` falls back to black there, which is how the mono mark rendered
  black-on-black the first time this sheet was built.

Anything that has to go through `<img>` uses `mark-light.svg` or `mark-dark.svg`,
which carry their own values and no text.

## Rules

- **Two colours, never three.** The unchosen rays are the structural colour at 40% opacity. A third hue makes the mark argue with itself about which ray was chosen.
- **The accent ray is the message.** If only one element can carry colour, it is that ray — never the triangle.
- **In one ink, the ray moves outside.** `mark-mono.svg` starts the chosen ray at the exit point rather than cutting through the mass, because a ray crossing a filled triangle in the same colour is invisible.
- **No container.** No circle, no rounded square, no badge. The beam runs off both edges on purpose — it is a signal passing through, not an object sitting still.
- **Clear space is the height of the triangle**, on every side.
- **The wordmark's emphasis is ink, not weight.** `Super` is muted and `Router` is full strength, because a weight contrast vanishes the moment the typeface falls back — and inside an `<img>` it always does.

## Colour

| Token | Light ground | Dark ground |
|---|---|---|
| structure | `#1E3A46` | `#CFE0E2` |
| accent | `#00A88F` | `#2FE3C4` |
| muted (wordmark's *Super*) | `#6C8288` | `#7C9095` |

The structural colour is a deep slate-teal rather than black, so it belongs to
the same family as the accent and the mark reads as one object instead of a
black drawing with a coloured line laid over it.

Both values shift between grounds rather than staying fixed — one value cannot
hold its contrast on both, and a mark legible in only one theme is broken half
the time.
