#!/usr/bin/env python3
"""
build.py — the second golden set: can the model POINT, not just judge.

    python3 build.py [--origin http://localhost:8934]

**Why this set exists.** The first set measures whether a model can say what is
on a screen. A QA agent also has to *act* on it — Midscene's `aiTap` and
`aiInput` need pixel coordinates, and judging and locating are different
abilities. A model can answer every assertion correctly and still click the
wrong button, and clicking the wrong button is the failure that does damage.

**Ground truth is free here too, and exactly.** The browser knows where every
element is. Ask it at the same instant the screenshot is taken and the answer is
a rectangle, not a judgement. No labelling, no argument.

**The frames must be captured with the boxes, in the same load.** The hub draws
its sphere at a different rotation every time, so a box read from a second load
describes a different picture.
"""
import argparse
import json
import os
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FRAMES = os.path.join(HERE, "frames")

# What the model is asked to point at, described the way a person would — never
# by position, or the question answers itself.
TARGETS = {
    "topbar": [
        ("the GitHub icon in the top bar", "document.querySelector('.social a[aria-label=\"GitHub\"]')"),
        ("the X (formerly Twitter) icon in the top bar", "document.querySelector('.social a[aria-label=\"X\"]')"),
        ("the LinkedIn icon in the top bar", "document.querySelector('.social a[aria-label=\"LinkedIn\"]')"),
        ("the switch that changes between light and dark mode", "document.querySelector('#theme')"),
        ("the site name 'Maaz Kazi'", "document.querySelector('#mark')"),
    ],
    "headline": [
        ("the large headline text", "document.querySelector('#heroCopy h1')"),
        ("the sentence directly beneath the headline", "document.querySelector('#heroCopy p')"),
    ],
    "askbar": [
        ("the box where you type a question", "document.querySelector('#askInput')"),
        ("the round arrow button that sends the question", "document.querySelector('#askSend')"),
    ],
    "footer": [
        ("the small line of text at the very bottom-left of the page",
         "document.querySelector('#hint')"),
        ("the percentage figure at the bottom-right of the page",
         "document.querySelector('#count')"),
    ],
    "leaders": [
        (f"the section label reading '{n}'",
         f"[...document.querySelectorAll('#leaders button')].find(e=>e.textContent.trim()==='{n}')")
        for n in ["About", "Startups", "Products", "Business cases", "Media", "Contact", "Certifications"]
    ],
}

STATES = [
    {"id": "hero-dark",    "scroll": 0,    "vw": 1280, "vh": 800, "theme": "dark",
     "groups": ["topbar", "headline", "footer"]},
    {"id": "hero-light",   "scroll": 0,    "vw": 1280, "vh": 800, "theme": "light",
     "groups": ["topbar", "headline", "footer"]},
    {"id": "hub-dark",     "scroll": 2312, "vw": 1280, "vh": 800, "theme": "dark",
     "groups": ["topbar", "askbar", "leaders"]},
    {"id": "hub-light",    "scroll": 2312, "vw": 1280, "vh": 800, "theme": "light",
     "groups": ["topbar", "askbar", "leaders"]},
    {"id": "mid-dark",     "scroll": 1150, "vw": 1280, "vh": 800, "theme": "dark",
     "groups": ["topbar", "footer"]},
    {"id": "mid-light",    "scroll": 1150, "vw": 1280, "vh": 800, "theme": "light",
     "groups": ["topbar", "footer"]},
    {"id": "hero-mobile",  "scroll": 0,    "vw": 390,  "vh": 844, "theme": "dark",
     "groups": ["topbar", "headline"]},
    {"id": "hero-mobile-l","scroll": 0,    "vw": 390,  "vh": 844, "theme": "light",
     "groups": ["topbar", "headline"]},
    {"id": "hub-mobile",   "scroll": 2312, "vw": 390,  "vh": 844, "theme": "dark",
     "groups": ["topbar", "askbar", "leaders"]},
    {"id": "wide-dark",    "scroll": 2312, "vw": 1600, "vh": 900, "theme": "dark",
     "groups": ["topbar", "askbar", "leaders"]},
    {"id": "tablet-dark",  "scroll": 0,    "vw": 820,  "vh": 1100, "theme": "dark",
     "groups": ["topbar", "headline", "footer"]},
]

# Everything clickable on the page, so a miss can be told apart from a miss that
# lands on something else. Those are different failures and only one is dangerous.
CLICKABLE = ("#leaders button, .social a, #theme, #mark, #askSend, #askInput, "
             "#askbar, .dock, .chev")


def ab(*a, timeout=90):
    return subprocess.run(["agent-browser", *a], capture_output=True, text=True, timeout=timeout)


def js(expr):
    return (ab("eval", expr).stdout or "").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--origin", default="http://localhost:8934")
    a = ap.parse_args()
    os.makedirs(FRAMES, exist_ok=True)
    cases, skipped = [], []

    for st in STATES:
        ab("set", "viewport", str(st["vw"]), str(st["vh"]))
        ab("open", a.origin + "/")
        time.sleep(3)
        js(f"document.documentElement.setAttribute('data-theme','{st['theme']}');1")
        js(f"window.scrollTo(0,{st['scroll']});1")
        time.sleep(3)
        ab("screenshot", os.path.join(FRAMES, st["id"] + ".png"))

        # every clickable rectangle, for the wrong-element check
        others = js("JSON.stringify([...document.querySelectorAll('" + CLICKABLE +
                    "')].map(e=>{const b=e.getBoundingClientRect();return "
                    "{t:(e.textContent||e.getAttribute('aria-label')||e.id||'').trim().slice(0,28),"
                    "x:Math.round(b.left),y:Math.round(b.top),w:Math.round(b.width),h:Math.round(b.height)}})"
                    ".filter(r=>r.w>3&&r.h>3&&r.x>-50&&r.y>-50))")
        try:
            clickables = json.loads(json.loads(others)) if others.startswith('"') else json.loads(others)
        except Exception:
            clickables = []

        for g in st["groups"]:
            for desc, sel in TARGETS[g]:
                raw = js(f"(()=>{{const e={sel};if(!e)return 'null';const b=e.getBoundingClientRect();"
                         "return JSON.stringify({x:Math.round(b.left),y:Math.round(b.top),"
                         "w:Math.round(b.width),h:Math.round(b.height)})})()")
                raw = raw.strip('"').replace('\\"', '"')
                if raw in ("null", "", "undefined"):
                    skipped.append((st["id"], desc, "not present"))
                    continue
                box = json.loads(raw)
                # An element off-screen or with no area cannot be pointed at, and
                # asking anyway measures the question, not the model.
                if box["w"] < 6 or box["h"] < 6 or box["x"] < 0 or box["y"] < 0 \
                        or box["x"] + box["w"] > st["vw"] or box["y"] + box["h"] > st["vh"]:
                    skipped.append((st["id"], desc, f"outside the frame {box}"))
                    continue
                cases.append({"frame": st["id"], "target": desc, "box": box,
                              "group": g, "vw": st["vw"], "vh": st["vh"],
                              "clickables": clickables})
        print(f"  {st['id']}: {sum(1 for c in cases if c['frame']==st['id'])} targets, "
              f"{len(clickables)} clickable regions")

    for i, c in enumerate(cases, 1):
        c["id"] = f"P{i:03d}"
    with open(os.path.join(HERE, "manifest.json"), "w") as f:
        json.dump({"task_type": "qa-vision-point", "version": 1,
                   "built": time.strftime("%Y-%m-%d"),
                   "what_the_model_is_asked":
                       "Given a screenshot and a plain description of one thing on it, "
                       "return the pixel coordinates to click. This is Midscene's aiTap.",
                   "cases": len(cases), "skipped": skipped,
                   "case_list": cases}, f, indent=1)
    print(f"\n{len(cases)} pointing cases, {len(skipped)} skipped as unpointable")


if __name__ == "__main__":
    main()
