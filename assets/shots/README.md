# Product shots

**Real output, never retyped.** Every image here is a capture of the tool
actually running against this project's own data — the terminal shots render
the exact bytes the command printed, and the dashboard is the page the proxy
serves, not a mockup of it.

| File | What it shows |
|---|---|
| `dashboard.png` | the dashboard the tool serves at `/` — spend against reference spend, and the quality it was held at |
| `dashboard-mobile.png` | the same page at 375px |
| `routing-table.png` | the table it writes for your product, and the same task measured on two products |
| `shadow.png` | the real bill against the predicted one, with the agreement caveat in place |
| `staleness.png` | what has moved since you measured — prices, new models, a rebuilt exam |

**Regenerate rather than retouch.** These go stale the moment a number changes:

```bash
python3 -m superrouter.report            # then screenshot state/report.html
python3 -m superrouter.route_table       # and the other two commands
```

**If one is cropped for a card, keep the pair.** The dashboard sets a saving and
the quality it was bought at side by side on purpose; cropping to the dollar
figure turns a measurement tool into a cost tool, which is the exact misreading
the product is built against.
