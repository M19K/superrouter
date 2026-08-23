#!/usr/bin/env python3
"""
build_generic.py — build a golden set for ANY web product, not just one.

    python3 build_generic.py --origin http://localhost:8934 --name portfolio
    python3 build_generic.py --origin https://example.org --name example

Roles are discovered by rule (`discover.js`), defects are planted by role
(`generic.py`), and the statement put to the model names whatever the page
actually put in that role. A role the page does not have simply skips its
classes. Nothing here knows anything about a particular site.

Every planted defect is still gated on pixels: a mutation that reports success
and changes nothing is refused rather than filed as a lie about itself.
"""
import argparse
import base64
import json
import os
import subprocess
import time

import generic

HERE = os.path.dirname(os.path.abspath(__file__))
DISCOVER = base64.b64encode(open(os.path.join(HERE, "discover.js"), "rb").read()).decode()

VIEWPORTS = [("desktop", 1280, 800), ("mobile", 390, 844), ("tablet", 820, 1100)]
SCROLLS = [0.0, 0.45, 0.98]
THEMES = ["dark", "light"]


def ab(*a, timeout=90):
    return subprocess.run(["agent-browser", *a], capture_output=True, text=True, timeout=timeout)


def js(expr):
    return (ab("eval", expr).stdout or "").strip()


def load_discover():
    js(f"(0,eval)(atob('{DISCOVER}'));1")


def roles_present():
    raw = js("JSON.stringify(window.SR_ROLES())")
    s = raw[raw.find("{"):raw.rfind("}") + 1].replace('\\"', '"')
    try:
        return json.loads(s)
    except Exception:
        return {}


def pixels_changed(a, b):
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", a, "-i", b, "-filter_complex",
         "[0:v][1:v]blend=all_mode=difference,format=gray,"
         "geq=lum='if(gt(lum(X\\,Y),12),255,0)',signalstats,"
         "metadata=print:key=lavfi.signalstats.YAVG", "-f", "null", "-"],
        capture_output=True, text=True, timeout=90)
    vals = [float(l.split("=")[-1]) for l in (r.stderr + r.stdout).splitlines() if "YAVG" in l]
    return (max(vals) / 255.0) if vals else 1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--origin", required=True)
    ap.add_argument("--name", required=True, help="short id for this product")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    out = a.out or os.path.join(HERE, f"sets/{a.name}")
    frames = os.path.join(out, "frames")
    os.makedirs(frames, exist_ok=True)

    states, cases, refused = [], [], []
    for vp, w, h in VIEWPORTS:
        for theme in THEMES:
            for si, frac in enumerate(SCROLLS):
                sid = f"{a.name}-{vp}-{theme}-{si}"
                ab("set", "viewport", str(w), str(h))
                ab("open", a.origin)
                time.sleep(3)
                js(f"document.documentElement.setAttribute('data-theme','{theme}');1")
                js(f"window.scrollTo(0,Math.round(document.body.scrollHeight*{frac}));1")
                time.sleep(2.6)
                load_discover()
                roles = roles_present()
                if not roles:
                    refused.append((sid, "-", "page exposed no roles"))
                    continue
                base = os.path.join(frames, sid + ".png")
                ab("screenshot", base)
                states.append({"id": sid, "viewport": vp, "theme": theme,
                               "roles": {k: len(v) for k, v in roles.items()}})
                for t, ans in generic.UNIVERSAL:
                    cases.append({"frame": sid, "assert": t, "answer": ans,
                                  "needs_defect_sight": False, "defect": None})

                for cls in generic.CLASSES:
                    found = roles.get(cls["role"]) or []
                    if not found:
                        refused.append((sid, cls["id"], f"no {cls['role']} on this screen"))
                        continue
                    fid = f"{sid}__{cls['id']}"
                    ab("open", a.origin)
                    time.sleep(3)
                    js(f"document.documentElement.setAttribute('data-theme','{theme}');1")
                    js(f"window.scrollTo(0,Math.round(document.body.scrollHeight*{frac}));1")
                    time.sleep(2.6)
                    load_discover()
                    ok = js("(()=>{const E=window.SR_FIND('%s');if(!E.length)return '0';%s;return '1'})()"
                            % (cls["role"], cls["js"]))
                    if "1" not in ok:
                        refused.append((sid, cls["id"], "mutation did not apply"))
                        continue
                    time.sleep(0.9)
                    path = os.path.join(frames, fid + ".png")
                    ab("screenshot", path)
                    moved = pixels_changed(base, path)
                    if moved < 0.0015:
                        os.remove(path)
                        refused.append((sid, cls["id"], f"no visible change ({moved:.4%})"))
                        continue
                    txt = (found[0].get("text") or found[0].get("label")
                           or found[0].get("tag") or "")
                    # For a class that removes items, the statement names the ones
                    # that were removed — a count would be a different, harder
                    # question about counting rather than about seeing.
                    gone = [ (f.get("text") or f.get("label") or f.get("tag") or "").strip()
                             for f in found[max(1, -(-len(found) // 2)):] ]
                    gone = [g for g in gone if g][:3]
                    names = ", ".join(f"“{g[:24]}”" for g in gone) or "the removed items"
                    bt, ba = cls["breaks"]
                    ct, ca = cls["control"]
                    if "{names}" in bt and not gone:
                        refused.append((sid, cls["id"], "nothing nameable was removed"))
                        continue
                    cases.append({"frame": fid,
                                  "assert": bt.format(t=txt[:38], n=len(found), names=names),
                                  "answer": ba, "needs_defect_sight": True,
                                  "defect": cls["id"], "qa_layer": cls["layer"],
                                  "role": cls["role"]})
                    cases.append({"frame": fid, "assert": ct, "answer": ca,
                                  "needs_defect_sight": False, "defect": cls["id"],
                                  "control_for": cls["id"]})
                print(f"  {sid}: {sum(1 for c in cases if c['frame'].startswith(sid))} cases")

    for i, c in enumerate(cases, 1):
        c["id"] = f"G{i:03d}"
    t = sum(1 for c in cases if c["answer"])
    planted = sum(1 for c in cases if c["needs_defect_sight"])

    # **An exam with no planted defects measures nothing, and it does not look
    # broken.** Run cold against a minimal page, every one of the 18 defect
    # classes found nothing to break, and the builder still emitted 36 balanced
    # cases whose entire negative half was one trivial control assertion. Every
    # model would score near 100% on it and the reader would believe the number.
    #
    # So it refuses, and says which classes found nothing — because the fix is
    # a richer page or a defect class this page can carry, and neither is
    # guessable from "0 defect-sight" printed at the end of a success message.
    if planted < 5:
        from collections import Counter
        why = Counter(r[1] for r in refused).most_common(6)
        print(f"\nREFUSING to write this exam: only {planted} planted defect(s).")
        print(f"  {len(cases)} cases would have been written and every model would")
        print("  score near 100% on them, because the negative half is a control")
        print("  assertion rather than a real fault. That is a set that measures")
        print("  nothing while looking balanced.\n")
        print("  Defect classes that found nothing to break on this page:")
        for cls, n in why:
            print(f"    {cls:<28} refused {n}×")
        print("\n  Point it at a page with more on it, or add a defect class this")
        print(f"  page can carry. `states` found: {len(states)} screen(s).")
        raise SystemExit(2)

    json.dump({"task_type": "qa-vision-assert", "generator": "generic (role-targeted)",
               "product": a.name, "origin": a.origin,
               "built": time.strftime("%Y-%m-%d"),
               "states": states, "cases": len(cases),
               "true": t, "false": len(cases) - t,
               "defect_sight_cases": sum(1 for c in cases if c["needs_defect_sight"]),
               "refused": refused, "case_list": cases},
              open(os.path.join(out, "manifest.json"), "w"), indent=1)
    print(f"\n{a.name}: {len(states)} screens · {len(cases)} cases "
          f"({t} true / {len(cases)-t} false) · "
          f"{sum(1 for c in cases if c['needs_defect_sight'])} defect-sight · "
          f"{len(refused)} refused")


if __name__ == "__main__":
    main()
