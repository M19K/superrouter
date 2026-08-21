#!/usr/bin/env python3
"""
build_generic.py — the pointing set, for ANY site.

    python3 build_generic.py --origin https://example.com --name mysite

The first pointing builder named this one site's elements: `#askSend`,
`#leaders button`, the aria-label "GitHub". That is a set of instances, not a
method, and it could not be pointed at a second product — the same flaw the
judging set had and the same fix: find things by the ROLE they play, then
describe whatever was found.

**Ground truth stays exact.** The browser knows where every element is. Read the
rectangle in the same load as the screenshot and the right answer is a geometric
fact, not a judgement.

**The description must not leak the answer.** A target is described by what it
is and what it says — "the link reading Documentation" — never by where it sits.
A description containing "top right" tests reading comprehension, not vision.

Everything clickable is captured too, so a miss that lands on another control
can be told apart from a miss that lands on nothing. Only one of those is
dangerous.
"""
import argparse
import base64
import json
import os
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DISCOVER = os.path.join(os.path.dirname(HERE), "qa-vision", "discover.js")

# Role → how to describe whatever the page put in that role. The description is
# built from the element's own text or label, so it names a real thing on a real
# page without naming a position.
ROLES = {
    "nav-links":       'the navigation link reading "{t}"',
    "labels":          'the item reading "{t}"',
    "primary-control": 'the main button{q}',
    "text-input":      "the box where you type",
    "headline":        "the large headline text",
    "subhead":         "the line of text directly beneath the headline",
}

CLICKABLE = "a, button, input, textarea, select, [role=button], [role=link], [onclick]"


def ab(*a, timeout=120):
    return subprocess.run(["agent-browser", *a], capture_output=True, text=True, timeout=timeout)


def js(expr):
    out = (ab("eval", expr).stdout or "").strip()
    if out.startswith('"') and out.endswith('"'):
        out = out[1:-1].replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--origin", required=True)
    ap.add_argument("--name", required=True, help="short id for this product")
    ap.add_argument("--paths", default="/", help="comma-separated paths")
    a = ap.parse_args()

    frames = os.path.join(HERE, "sets", a.name, "frames")
    os.makedirs(frames, exist_ok=True)
    # Base64 then eval: the library is 90 lines and passing it inline through a
    # shell argument silently truncates it, which reads as "the page has nothing
    # on it" rather than as an error. Same trick the judging builder uses.
    lib_b64 = base64.b64encode(open(DISCOVER, "rb").read()).decode()
    cases, skipped = [], []

    states = []
    for path in a.paths.split(","):
        for vw, vh, tag in ((1280, 800, "desktop"), (390, 844, "mobile")):
            for scroll, where in ((0, "top"), (900, "lower")):
                states.append({"path": path.strip(), "vw": vw, "vh": vh,
                               "scroll": scroll,
                               "id": f"{tag}-{where}-{path.strip('/').replace('/','-') or 'home'}"})

    for st in states:
        ab("set", "viewport", str(st["vw"]), str(st["vh"]))
        ab("open", a.origin.rstrip("/") + st["path"])
        time.sleep(3)
        js(f"window.scrollTo(0,{st['scroll']});1")
        time.sleep(2)
        ab("screenshot", os.path.join(frames, st["id"] + ".png"))

        js(f"(0,eval)(atob('{lib_b64}'));1")
        raw = js("JSON.stringify([...document.querySelectorAll('" + CLICKABLE +
                 "')].map(e=>{const b=e.getBoundingClientRect();return "
                 "{t:(e.textContent||e.getAttribute('aria-label')||'').trim().slice(0,30),"
                 "x:Math.round(b.left),y:Math.round(b.top),"
                 "w:Math.round(b.width),h:Math.round(b.height)}})"
                 ".filter(r=>r.w>3&&r.h>3))")
        try:
            clickables = json.loads(raw)
        except Exception:
            clickables = []

        for role, template in ROLES.items():
            raw = js(f"JSON.stringify(window.SR_FIND('{role}').map(e=>{{"
                     "const b=e.getBoundingClientRect();return {"
                     "t:(e.textContent||'').trim().slice(0,32),"
                     "q:(e.getAttribute('aria-label')||e.getAttribute('placeholder')||''),"
                     "x:Math.round(b.left),y:Math.round(b.top),"
                     "w:Math.round(b.width),h:Math.round(b.height)}}))")
            try:
                found = json.loads(raw)
            except Exception:
                continue
            for el in found[:6]:
                text = (el.get("t") or "").strip()
                if "{t}" in template and (len(text) < 2 or len(text) > 30):
                    skipped.append((st["id"], role, "no usable label"))
                    continue
                desc = template.format(t=text,
                                       q=f' labelled "{el["q"]}"' if el.get("q") else "")
                b = {k: el[k] for k in ("x", "y", "w", "h")}
                # unpointable: off-screen, or too small to aim at
                if (b["w"] < 8 or b["h"] < 8 or b["x"] < 0 or b["y"] < 0
                        or b["x"] + b["w"] > st["vw"] or b["y"] + b["h"] > st["vh"]):
                    skipped.append((st["id"], role, f"outside the frame {b}"))
                    continue
                # ambiguous: the same description matches more than one thing
                if sum(1 for c in clickables if c["t"] == text) > 1 and "{t}" in template:
                    skipped.append((st["id"], role, f"'{text}' appears more than once"))
                    continue
                cases.append({"frame": st["id"], "target": desc, "box": b, "group": role,
                              "vw": st["vw"], "vh": st["vh"], "clickables": clickables})
        print(f"  {st['id']}: {sum(1 for c in cases if c['frame']==st['id'])} targets, "
              f"{len(clickables)} clickable regions")

    for i, c in enumerate(cases, 1):
        c["id"] = f"P{i:03d}"
    out = os.path.join(HERE, "sets", a.name)
    with open(os.path.join(out, "manifest.json"), "w") as f:
        json.dump({"task_type": "qa-vision-point", "product": a.name,
                   "origin": a.origin, "built": time.strftime("%Y-%m-%d"),
                   "cases": len(cases), "skipped": skipped,
                   "case_list": cases}, f, indent=1)
    print(f"\n{len(cases)} pointing targets · {len(skipped)} skipped as unpointable "
          f"or ambiguous\n  → {out}")


if __name__ == "__main__":
    main()
