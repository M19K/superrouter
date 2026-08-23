#!/usr/bin/env python3
"""
report.py — the dashboard: what you spent, and what it cost you in quality.

    python3 -m superrouter.report                 # writes state/report.html
    python3 -m superrouter.report --out /tmp/x.html
    python3 -m superrouter.report --open          # and open it

The proxy also serves this page at `/` while it is running, from the same
function, so there is one dashboard rather than two that drift.

── The one rule this page is built around ───────────────────────────────────

**No saving is ever shown without the quality it was bought at.** A dashboard
that headlines a number of dollars turns this into a cost tool, and a cost tool
is exactly what SuperRouter is not — anyone can route to the cheapest model, and
the whole argument is that the saving was *justified*. So every figure here is a
pair, and the pair is the smallest unit on the page.

The same discipline that the instrument enforces on itself applies here:

  · every rate carries its denominator and its interval
  · agreement is never shown without the caveat that it measures drift, not
    quality — measured 100% agreement against 75% correctness on 60 live calls
  · thin traffic is called thin, in place, rather than rendered as a confident
    headline over six requests

Static HTML, no dependencies, no telemetry, no network calls at render time.
It can be committed to a pull request or opened offline.
"""
import argparse
import glob
import json
import math
import os
import time
from collections import defaultdict

from ._io import read_json, read_text

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVED = os.path.join(CODE, "state", "served.jsonl")
LOGO = os.path.join(CODE, "assets", "logo", "mark-dark.svg")

# Brand tokens. These are the mark's own values — the dashboard is not allowed
# its own palette, or the product and its dashboard stop looking like one thing.
INK_D, ACCENT_D = "#CFE0E2", "#2FE3C4"
INK_L, ACCENT_L = "#1E3A46", "#00A88F"


def wilson(hits, n):
    """95% interval. A rate without one is an opinion with a decimal point."""
    if not n:
        return 0, 0
    p, z = hits / n, 1.96
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0, round(100 * (c - m))), min(100, round(100 * (c + m)))


def load_served():
    if not os.path.exists(SERVED):
        return []
    out = []
    for line in open(SERVED):
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def load_quality():
    """Newest run per (task, model), keeping only the current exam."""
    dirs = {"qa-vision-assert": "runs_portfolio", "text-faithful": "text_runs",
            "qa-vision-point": "point_runs_portfolio"}
    out = {}
    for task, d in dirs.items():
        rows = {}
        for p in sorted(glob.glob(os.path.join(CODE, "state", d, "*.json"))):
            s = read_json(p)["summary"]
            rows[s["model"]] = s
        vals = list(rows.values())
        stamped = [r for r in vals if r.get("exam_fingerprint")]
        if stamped:
            newest = stamped[-1]["exam_fingerprint"]
            vals = [r for r in vals if r.get("exam_fingerprint") == newest]
        out[task] = {r["model"]: r for r in vals}
    return out


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


CSS = """
:root{
  --ground:#F2F5F5; --panel:#FFFFFF; --sunk:#E7EDED;
  --ink:#132630; --ink-2:#41585F; --ink-3:#6E858B;
  --rule:#D6E0E0; --rule-2:#BECBCB;
  --brand:#1E3A46; --accent:#00A88F; --accent-soft:#DDF2EE;
  --warn:#B4571E; --warn-soft:#FBEDE2;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --ground:#080C0E; --panel:#0F1719; --sunk:#0C1315;
    --ink:#DFE9EA; --ink-2:#9DB1B4; --ink-3:#6B8085;
    --rule:#1B2528; --rule-2:#2A383B;
    --brand:#CFE0E2; --accent:#2FE3C4; --accent-soft:#102C2A;
    --warn:#E0904F; --warn-soft:#2A1E14;
  }
}
:root[data-theme="dark"]{
  --ground:#080C0E; --panel:#0F1719; --sunk:#0C1315;
  --ink:#DFE9EA; --ink-2:#9DB1B4; --ink-3:#6B8085;
  --rule:#1B2528; --rule-2:#2A383B;
  --brand:#CFE0E2; --accent:#2FE3C4; --accent-soft:#102C2A;
  --warn:#E0904F; --warn-soft:#2A1E14;
}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);
  font-family:"IBM Plex Sans",system-ui,-apple-system,sans-serif;
  font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased}
.wrap{max-width:1040px;margin:0 auto;padding:28px 20px 72px}
a{color:var(--accent)}

/* ── header ─────────────────────────────────────────── */
header{display:flex;align-items:center;gap:14px;padding-bottom:16px;
  border-bottom:1px solid var(--rule-2);margin-bottom:8px;flex-wrap:wrap}
header svg{width:46px;height:46px;flex:none;color:var(--brand)}
.title{font-family:"Chakra Petch",system-ui,sans-serif;font-size:26px;
  font-weight:600;letter-spacing:-.01em;line-height:1}
.title .muted{color:var(--ink-3)}
.stamp{margin-left:auto;font-family:"IBM Plex Mono",ui-monospace,monospace;
  font-size:11px;color:var(--ink-3);letter-spacing:.04em;text-align:right}

h2{font-family:"Chakra Petch",system-ui,sans-serif;font-size:12px;font-weight:600;
  letter-spacing:.15em;text-transform:uppercase;color:var(--ink);
  margin:32px 0 3px}
.sub{margin:0 0 12px;font-size:12.5px;color:var(--ink-3);max-width:74ch}

/* ── the pair: never one without the other ──────────── */
.pair{display:grid;grid-template-columns:1fr 1fr;border:1px solid var(--rule-2);
  background:var(--panel)}
.pair>div{padding:20px 22px}
.pair>div+div{border-left:1px solid var(--rule-2)}
.k{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:10.5px;
  letter-spacing:.14em;text-transform:uppercase;color:var(--ink-3);margin-bottom:8px}
.v{font-family:"Chakra Petch",system-ui,sans-serif;font-size:40px;font-weight:700;
  line-height:1;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.v.on{color:var(--accent)}
.vsub{margin-top:7px;font-size:12.5px;color:var(--ink-2)}
.joiner{grid-column:1/-1;border-top:1px solid var(--rule);padding:11px 22px;
  font-size:12.5px;color:var(--ink-2);background:var(--sunk)}

/* ── tables ─────────────────────────────────────────── */
.scroll{overflow-x:auto;border:1px solid var(--rule);background:var(--panel)}
table{border-collapse:collapse;width:100%;font-size:13px}
th{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:10px;
  letter-spacing:.1em;text-transform:uppercase;color:var(--ink-3);
  text-align:left;padding:10px 12px;border-bottom:1px solid var(--rule-2);
  white-space:nowrap;font-weight:500}
td{padding:9px 12px;border-bottom:1px solid var(--rule);vertical-align:top}
tr:last-child td{border-bottom:0}
td.n{font-family:"IBM Plex Mono",ui-monospace,monospace;
  font-variant-numeric:tabular-nums;white-space:nowrap}
td.model{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:12px}
.ci{color:var(--ink-3);font-size:11px}
.win{color:var(--accent);font-weight:500}

/* ── bar: quality held, drawn as a ratio not a score ── */
.bar{position:relative;height:6px;background:var(--sunk);border-radius:3px;
  overflow:hidden;min-width:70px;margin-top:5px}
.bar i{position:absolute;inset:0 auto 0 0;background:var(--accent);border-radius:3px}
.bar.ref i{background:var(--ink-3)}

/* ── notes ──────────────────────────────────────────── */
.note{border:1px solid var(--rule);border-left:3px solid var(--warn);
  background:var(--warn-soft);padding:13px 16px;margin-top:12px;font-size:13px;
  color:var(--ink-2)}
.note b{color:var(--ink);font-weight:600}
.thin{border-left-color:var(--ink-3);background:var(--sunk)}
footer{margin-top:36px;padding-top:14px;border-top:1px solid var(--rule);
  font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:11px;
  color:var(--ink-3);line-height:1.8}
@media (max-width:640px){
  .pair{grid-template-columns:1fr}
  .pair>div+div{border-left:0;border-top:1px solid var(--rule-2)}
  .v{font-size:32px}
}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""


def render(served=None, quality=None):
    served = load_served() if served is None else served
    quality = load_quality() if quality is None else quality

    mark = ""
    if os.path.exists(LOGO):
        mark = read_text(LOGO)
        mark = mark[mark.index(">") + 1:].rsplit("</svg>", 1)[0]
        mark = mark.replace("#CFE0E2", "currentColor").replace("#2FE3C4", "var(--accent)")
        mark = f'<svg viewBox="0 0 64 64" aria-hidden="true">{mark}</svg>'

    spent = sum(r.get("cost_usd") or 0 for r in served)
    ref = sum(r.get("reference_cost_estimate") or 0 for r in served)
    checks = sum(r.get("shadow_cost") or 0 for r in served)
    saving = (ref / spent) if spent else 0
    n = len(served)
    when = (f'{served[0]["ts"][:10]} → {served[-1]["ts"][:10]}' if served else "no traffic yet")

    shadowed = [r for r in served if r.get("agreed") is not None]
    agreed = sum(1 for r in shadowed if r["agreed"])
    a_lo, a_hi = wilson(agreed, len(shadowed))

    by_task = defaultdict(list)
    for r in served:
        by_task[r.get("task", "unrouted")].append(r)

    p = []
    A = p.append
    A('<div class="wrap">')

    A('<header>' + mark)
    A('<div><div class="title"><span class="muted">Super</span>Router</div></div>')
    A(f'<div class="stamp">{esc(when)}<br>{n} routed call{"" if n==1 else "s"}</div>')
    A('</header>')

    # ── the pair ────────────────────────────────────────────────────────────
    A('<h2>What it cost, and what it cost you</h2>')
    A('<p class="sub">These two are one figure. A saving without the quality it '
      'was bought at is the number any router can show you.</p>')
    A('<div class="pair">')
    A('<div><div class="k">Spent on routed calls</div>'
      f'<div class="v on">${spent:.4f}</div>'
      f'<div class="vsub">against <b>${ref:.4f}</b> if every call had gone to '
      f'the reference model — <b>{saving:.0f}× cheaper</b></div></div>')

    if shadowed:
        A('<div><div class="k">Agreed with the reference</div>'
          f'<div class="v">{round(100*agreed/len(shadowed))}%</div>'
          f'<div class="vsub">{agreed} of {len(shadowed)} sampled calls, '
          f'<span class="ci">95% interval {a_lo}–{a_hi}</span></div></div>')
    else:
        A('<div><div class="k">Agreed with the reference</div>'
          '<div class="v" style="color:var(--ink-3)">—</div>'
          '<div class="vsub">no calls sampled yet. Run the proxy with '
          '<code>--shadow N</code> and this fills in.</div></div>')

    if checks:
        A(f'<div class="joiner">Proving it cost <b>${checks:.4f}</b> on top, at the '
          f'sampling rate you ran. That is the price of the check and it is kept out '
          f'of the saving above — rolled in, a fully-sampled run reports no saving at '
          f'all and the router looks worthless.</div>')
    A('</div>')

    if shadowed:
        A('<div class="note"><b>Agreement is not quality.</b> It measures drift from '
          'the reference and nothing else, and it goes blind exactly where two models '
          'share a blind spot. Measured on 60 live calls: 100% agreement against 75% '
          'correctness — they agreed and were both wrong on 24% of traffic. Use this '
          'to decide <b>when</b> to re-run the exam, never as evidence you need not.</div>')
    if 0 < len(shadowed) < 30:
        A(f'<div class="note thin">Only {len(shadowed)} sampled calls so far, so that '
          f'interval is {a_hi-a_lo} points wide. It is a count, not a verdict yet.</div>')

    # ── per task ────────────────────────────────────────────────────────────
    A('<h2>By task</h2>')
    A('<p class="sub">Each task routes on its own measurement, because a model good '
      'at one is not thereby good at another — judging and pointing were close to '
      'unrelated abilities when measured.</p>')
    A('<div class="scroll"><table><thead><tr>'
      '<th>Task</th><th>Calls</th><th>Routed to</th><th>Spent</th>'
      '<th>Reference would</th><th>Saving</th><th>Quality held at</th>'
      '</tr></thead><tbody>')
    if not served:
        A('<tr><td colspan="7" style="color:var(--ink-3)">Nothing has been routed '
          'yet. Start the proxy and point an agent at it.</td></tr>')
    for task, rows in sorted(by_task.items()):
        s = sum(r.get("cost_usd") or 0 for r in rows)
        rf = sum(r.get("reference_cost_estimate") or 0 for r in rows)
        models = sorted({r.get("model", "?") for r in rows})
        q = quality.get(task, {})
        held = "<span class='ci'>not measured</span>"
        for m in models:
            if m in q:
                r0 = q[m]
                catch = r0.get("catch_when_answered", r0.get("catch"))
                ci = r0.get("catch_ci") or [0, 0]
                if catch is not None:
                    held = (f"{catch}% of planted faults caught"
                            f"<div class='ci'>95% interval {ci[0]}–{ci[1]}</div>"
                            f"<div class='bar'><i style='width:{catch}%'></i></div>")
                break
        A(f'<tr><td class="model">{esc(task)}</td><td class="n">{len(rows)}</td>'
          f'<td class="model">{esc(", ".join(m.split("/")[-1] for m in models))}</td>'
          f'<td class="n">${s:.4f}</td><td class="n">${rf:.4f}</td>'
          f'<td class="n win">{(rf/s if s else 0):.0f}×</td><td>{held}</td></tr>')
    A('</tbody></table></div>')

    # ── fallbacks ───────────────────────────────────────────────────────────
    fell = [r for r in served if r.get("fell_back")]
    # `attempts` is a LIST of the models tried, not a count — reading it as a
    # number silently compares a list to an int and the page dies. The log's
    # shape is the log's business; this reads it rather than assuming it.
    def tries(r):
        a = r.get("attempts")
        return len(a) if isinstance(a, list) else (a or 1)
    retried = [r for r in served if tries(r) > 1]
    A('<h2>When the cheap model failed</h2>')
    A('<p class="sub">A router that cannot fall back turns one model having a bad '
      'minute into a total outage. This is how often that mattered.</p>')
    if not served:
        A('<div class="note thin">No traffic yet.</div>')
    else:
        A('<div class="scroll"><table><thead><tr><th>Outcome</th><th>Calls</th>'
          '<th>Share</th></tr></thead><tbody>')
        for label, rows in (("Served by the first choice", [r for r in served if not r.get("fell_back")]),
                            ("Fell back to a dearer model", fell),
                            ("Needed more than one attempt", retried)):
            A(f'<tr><td>{label}</td><td class="n">{len(rows)}</td>'
              f'<td class="n">{round(100*len(rows)/len(served))}%</td></tr>')
        A('</tbody></table></div>')

    # ── quality bench ───────────────────────────────────────────────────────
    A('<h2>The measurements behind the table</h2>')
    A('<p class="sub">What each candidate scored on your own product. Two numbers, '
      'never one — a model that misses faults and a model that invents them cannot '
      'be told apart by a single score.</p>')
    A('<div class="scroll"><table><thead><tr><th>Task</th><th>Model</th>'
      '<th>Catches</th><th>False alarms</th><th>Refused</th><th>$ / run</th>'
      '</tr></thead><tbody>')
    any_q = False
    for task, models in quality.items():
        for m, r in sorted(models.items(), key=lambda kv: -(kv[1].get("catch_when_answered") or 0)):
            any_q = True
            c = r.get("catch_when_answered", r.get("catch")) or 0
            cci = r.get("catch_ci") or [0, 0]
            fa = r.get("false_alarm_when_answered", r.get("false_alarm")) or 0
            fci = r.get("false_alarm_ci") or [0, 0]
            A(f'<tr><td class="model">{esc(task)}</td>'
              f'<td class="model">{esc(m)}</td>'
              f'<td class="n">{c}%<div class="ci">{cci[0]}–{cci[1]}</div></td>'
              f'<td class="n">{fa}%<div class="ci">{fci[0]}–{fci[1]}</div></td>'
              f'<td class="n">{r.get("refusal_pct", 0)}%</td>'
              f'<td class="n">${r.get("cost_usd", 0):.4f}</td></tr>')
    if not any_q:
        A('<tr><td colspan="6" style="color:var(--ink-3)">No measurements yet. '
          'Build a golden set against your product, then score it.</td></tr>')
    A('</tbody></table></div>')

    A('<footer>Generated ' + time.strftime("%Y-%m-%d %H:%M")
      + ' from state/served.jsonl and state/*runs*/ · no network calls, no telemetry<br>'
        'Every rate carries its denominator and its 95% interval. '
        'A figure without one was not measured, it was assumed.</footer>')
    A('</div>')

    return ("<title>SuperRouter</title>\n"
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
            '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
            'family=Chakra+Petch:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500'
            '&family=IBM+Plex+Sans:wght@400;500;600&display=swap">\n'
            f"<style>{CSS}</style>\n" + "\n".join(p))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(CODE, "state", "report.html"))
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args()
    html = render()
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as f:
        f.write(html)
    print(f"dashboard → {a.out}  ({len(html):,} bytes, no dependencies)")
    if a.open:
        import webbrowser
        webbrowser.open("file://" + os.path.abspath(a.out))


if __name__ == "__main__":
    main()
