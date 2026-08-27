#!/usr/bin/env python3
"""
build.py — turn spec.py into frames on disk and a manifest with ground truth.

    python3 build.py [--origin http://localhost:8934] [--only <frame-id>]

Nothing here decides what is true. `spec.py` declares it, this only executes.
Frames are frozen once written: the source page renders differently on every
load, so scoring against it live would confuse a worse model with a changed page.
"""
import argparse
import json
import os
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FRAMES = os.path.join(HERE, "frames")
import spec

# Injected before every mutation so a mutation stays one readable line. These
# are helpers, not defects — they express "break this kind of thing", which is
# what keeps a mutation a class rather than an instance.
PRELUDE = """
window.S=(css)=>{const s=document.createElement('style');s.id='mut';s.textContent=css;document.head.appendChild(s);return 1};
window.COVER=(sel)=>{const e=document.querySelector(sel);if(!e)return 0;const b=e.getBoundingClientRect();
 const d=document.createElement('div');d.style.cssText='position:fixed;z-index:99999;background:#1b1b1b;left:'+(b.left-7)+'px;top:'+(b.top-7)+'px;width:'+(b.width+14)+'px;height:'+(b.height+14)+'px;border-radius:9px';
 document.body.appendChild(d);return 1};
window.REMOVE_TEXT=(sel,names)=>{let n=0;[...document.querySelectorAll(sel)].forEach(e=>{if(names.includes(e.textContent.trim())){e.remove();n++}});return n};
window.SET_TEXT=(sel,match,val)=>{const e=[...document.querySelectorAll(sel)].find(x=>x.textContent.trim()===match);if(!e)return 0;e.textContent=val;return 1};
"""


def ab(*args, timeout=90):
    return subprocess.run(["agent-browser", *args], capture_output=True, text=True, timeout=timeout)


def js(expr):
    r = ab("eval", expr)
    return (r.stdout or "").strip()


def build_frame(item, origin, settle=2.6):
    st, mut = item["state"], item["mutation"]
    ab("set", "viewport", str(st["vw"]), str(st["vh"]))
    ab("open", origin + "/")
    time.sleep(settle)
    js(f"document.documentElement.setAttribute('data-theme','{st['theme']}');1")
    js(f"window.scrollTo(0,{st['scroll']});1")
    time.sleep(settle)
    applied = None
    if mut:
        js(PRELUDE.replace("\n", "") + "1")
        applied = js(mut["js"])
        time.sleep(0.9)
        # A mutation that silently fails produces a healthy page filed as broken.
        # It has happened. Refuse the frame rather than write a lie.
        if applied in ("", "0", '"0"', "null", "undefined") or "rror" in applied:
            return None, f"js returned {applied!r}"
    path = os.path.join(FRAMES, item["frame"] + ".png")
    ab("screenshot", path)

    # A mutation can return success and change nothing on screen — a CSS rule
    # whose selector matches no element does exactly that, and one did. The JS
    # return value cannot see it; the pixels can. Compare against the healthy
    # frame of the same state and refuse anything that did not move.
    if mut:
        base = os.path.join(FRAMES, st["id"] + ".png")
        if os.path.exists(base):
            moved = pixels_changed(base, path)
            if moved < 0.0015:
                os.remove(path)
                return None, f"no visible change ({moved:.4%} of pixels moved)"
    return True, applied


def pixels_changed(a, b):
    """Fraction of pixels that differ, via ffmpeg. Cheap, no image library.

    `-v info`, not `-v error`: `metadata=print` writes at info level, so the
    quieter setting suppressed the only line this reads and every call fell
    through to the `else 1.0` — "everything moved" — which passes every gate it
    is asked. Found 2026-08-27; see the note in build_generic.py.
    """
    r = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", a, "-i", b, "-filter_complex",
         "[0:v][1:v]blend=all_mode=difference,format=gray,"
         "geq=lum='if(gt(lum(X\\,Y),12),255,0)',signalstats,"
         "metadata=print:key=lavfi.signalstats.YAVG", "-f", "null", "-"],
        capture_output=True, text=True, timeout=90)
    vals = [float(l.split("=")[-1]) for l in (r.stderr + r.stdout).splitlines()
            if "YAVG" in l]
    return (max(vals) / 255.0) if vals else 1.0


def cases_for(item):
    st, mut = item["state"], item["mutation"]
    out = []
    if mut is None:
        for g in sorted(st["groups"]):
            for text, ans in spec.TRUE_OF_HEALTHY[g]:
                out.append({"frame": item["frame"], "assert": text, "answer": ans,
                            "needs_defect_sight": False, "group": g, "defect": None})
    else:
        t, a = mut["breaks"]
        out.append({"frame": item["frame"], "assert": t, "answer": a,
                    "needs_defect_sight": True, "group": mut["needs"],
                    "defect": mut["id"], "qa_layer": mut["layer"]})
        t, a = mut["control"]
        out.append({"frame": item["frame"], "assert": t, "answer": a,
                    "needs_defect_sight": False, "group": mut["needs"],
                    "defect": mut["id"], "control_for": mut["id"]})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--origin", default="http://localhost:8934")
    ap.add_argument("--only")
    a = ap.parse_args()
    os.makedirs(FRAMES, exist_ok=True)
    healthy, broken = spec.plan()
    items = healthy + broken
    if a.only:
        items = [i for i in items if i["frame"] == a.only]

    built, failed, cases = [], [], []
    t0 = time.time()
    for n, item in enumerate(items, 1):
        ok, applied = build_frame(item, a.origin)
        if ok:
            built.append(item["frame"])
            cases.extend(cases_for(item))
            print(f"  [{n}/{len(items)}] {item['frame']}")
        else:
            failed.append((item["frame"], applied))
            print(f"  [{n}/{len(items)}] FAILED TO APPLY  {item['frame']}  -> {applied!r}")

    for i, c in enumerate(cases, 1):
        c["id"] = f"C{i:03d}"
    t = sum(1 for c in cases if c["answer"])
    manifest = {
        "task_type": "qa-vision-assert",
        "version": 2,
        "built": time.strftime("%Y-%m-%d"),
        "source": "02-Projects/portfolio-website/code/site",
        "generated_by": "spec.py + build.py — states x mutations, not hand-written",
        "frames": len(built),
        "cases": len(cases),
        "true": t, "false": len(cases) - t,
        "defect_sight_cases": sum(1 for c in cases if c["needs_defect_sight"]),
        "failed_mutations": failed,
        "case_list": cases,
    }
    with open(os.path.join(HERE, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    print(f"\n{len(built)} frames, {len(cases)} cases ({t} true / {len(cases)-t} false), "
          f"{manifest['defect_sight_cases']} needing defect sight, "
          f"{len(failed)} mutations refused, {round(time.time()-t0)}s")


if __name__ == "__main__":
    main()
