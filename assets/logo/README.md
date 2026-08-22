# The mark

**A prism.** One beam goes in, the spectrum comes out, and one ray is brighter
than the rest — separate, then choose. That is the product in one image, and it
is the half most routers skip: everything else in the category draws the
switching and nothing draws the measuring.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="lockup-dark.svg">
  <img src="lockup-light.svg" alt="SuperRouter" width="250">
</picture>

## The files

| File | Use it for |
|---|---|
| `mark.svg` | embedding in an app that already has a theme — structure is `currentColor`, the accent is `--sr-accent` |
| `mark-light.svg` · `mark-dark.svg` | anywhere the ground is fixed and known |
| `mark-mono.svg` | one colour only — a stamp, an etch, a terminal, or any place two inks are not available |
| `lockup-light.svg` · `lockup-dark.svg` | mark plus wordmark, for a header or a README |
| `favicon.svg` | browser tab |
| `contact-sheet.html` | open it to see every file at every size on both grounds |

Every file is generated from one set of coordinates, so the favicon and the
header cannot drift apart.

## Rules

- **One accent, never two.** The unchosen rays are the same ink at 30% opacity. A second hue makes the mark argue with itself about which ray was chosen.
- **The accent ray is the message.** If only one element can carry colour, it is that ray — never the triangle.
- **Below 16px, use `mark-mono.svg`.** The accent is under one pixel wide by then and reads as noise rather than as a decision.
- **Do not add a container.** No circle, no rounded square, no badge. The beam runs off both edges on purpose — it is a signal passing through, not an object sitting still.
- **Clear space is the height of the triangle**, on every side.
- **The wordmark's emphasis is ink, not weight.** `Super` is muted and `Router` is full-strength, because a weight contrast vanishes the moment the typeface falls back — and in an `<img>` it always does.

## Colour

| Token | Light ground | Dark ground |
|---|---|---|
| ink | `#0C1416` | `#DFE9EA` |
| accent | `#008E7E` | `#2FE3C4` |
| muted (wordmark's *Super*) | `#6C8288` | `#7C9095` |

The accent shifts between grounds rather than staying fixed — one value cannot
hold its contrast on both, and a mark that is legible in only one theme is a
mark that is broken half the time.
