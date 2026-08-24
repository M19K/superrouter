#!/usr/bin/env python3
"""
doctor.py — can this machine actually do the thing, and if not, exactly why.

    python3 -m superrouter.doctor            # human-readable
    python3 -m superrouter.doctor --json     # for an agent driving the install

**Why this is the first command.** Building an exam takes about ten minutes of
screenshots, and it needs two programs that are not Python packages —
`agent-browser` to drive a browser and `ffmpeg` to compare frames. Nothing
checked for either, so a user found out partway through a capture run, from a
`FileNotFoundError` naming a binary rather than a problem.

A dependency that is not a package is the one nobody declares. It is the same
class as this project's own worst bug: `ffmpeg` was being invoked with a flag
that suppressed the only output the caller read, so the pixel gate silently
never fired. **A tool that is present but not doing its job is worse than a
tool that is absent**, so this checks behaviour, not presence — it runs each
one and reads what comes back.

Every check reports one of four states, and the distinction matters to an agent
deciding what to do next:

    ok        this works, verified by running it
    missing   not installed. There is an install line.
    broken    installed but does not behave. There is a diagnosis.
    optional  absent, and only one capability is unavailable without it

Exit code is 0 when everything **required** works, 1 otherwise. Optional
failures never fail the exit code, because a machine with no local model can
still measure hosted ones perfectly well.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except FileNotFoundError:
        return None, "not found"
    except subprocess.TimeoutExpired:
        return None, "timed out"


def check_python():
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 9)
    return {"name": "python", "state": "ok" if ok else "broken",
            "detail": f"{v.major}.{v.minor}.{v.micro}",
            "fix": "" if ok else "SuperRouter needs Python 3.9 or newer."}


def check_stdlib_only():
    """The install story is that there isn't one. Assert it rather than say it."""
    import importlib
    bad = []
    for m in ("evals", "serve", "audit", "policy", "perquery", "route_table"):
        try:
            importlib.import_module(f"superrouter.{m}")
        except Exception as e:                     # noqa: BLE001
            bad.append(f"{m}: {type(e).__name__}")
    return {"name": "core modules", "state": "ok" if not bad else "broken",
            "detail": "import with nothing installed" if not bad else ", ".join(bad),
            "fix": "" if not bad else "Report this — the core is meant to be "
                                      "standard library only."}


def check_ffmpeg():
    """Present AND doing its job.

    Presence is not the check. This runs the exact filter the exam builder uses
    to decide whether a planted defect changed any pixels, against an image
    compared with itself, and requires the answer to be zero. Under `-v error`
    that filter prints nothing and the caller reads an empty list — which is how
    the gate came to pass everything it was ever asked.
    """
    if not shutil.which("ffmpeg"):
        return {"name": "ffmpeg", "state": "missing", "detail": "not on PATH",
                "fix": "macOS: brew install ffmpeg · Debian/Ubuntu: apt install ffmpeg"}
    code, out = _run(["ffmpeg", "-v", "info", "-f", "lavfi", "-i",
                      "color=c=black:s=32x32:d=1", "-vf",
                      "signalstats,metadata=print:key=lavfi.signalstats.YAVG",
                      "-f", "null", "-"], timeout=30)
    if "YAVG" not in out:
        return {"name": "ffmpeg", "state": "broken",
                "detail": "runs, but prints no signalstats metadata",
                "fix": "The frame comparison reads that line to decide whether a "
                       "planted defect is visible. Without it every mutation "
                       "passes the gate. Try a build with the signalstats and "
                       "metadata filters enabled."}
    return {"name": "ffmpeg", "state": "ok", "detail": "frame comparison verified", "fix": ""}


def check_agent_browser():
    if not shutil.which("agent-browser"):
        return {"name": "agent-browser", "state": "missing", "detail": "not on PATH",
                "fix": "npm i -g agent-browser  (then: agent-browser install)"}
    code, out = _run(["agent-browser", "--version"], timeout=30)
    if code is None:
        return {"name": "agent-browser", "state": "broken", "detail": out.strip()[:80],
                "fix": "agent-browser install"}
    return {"name": "agent-browser", "state": "ok",
            "detail": out.strip().splitlines()[0][:40] if out.strip() else "present",
            "fix": ""}


def check_key():
    """A key is needed to SCORE, never to build. Say which."""
    from . import evals
    try:
        evals.key()
        return {"name": "provider key", "state": "ok", "detail": "resolved", "fix": ""}
    except SystemExit:
        return {"name": "provider key", "state": "missing",
                "detail": "no key found",
                "fix": "export OPENROUTER_API_KEY=…  (only needed to score; "
                       "building an exam costs nothing)"}
    except Exception as e:                          # noqa: BLE001
        return {"name": "provider key", "state": "broken",
                "detail": f"{type(e).__name__}", "fix": "check the key source"}


def check_local_model(base=None):
    base = base or os.environ.get("LOCAL_MODEL_BASE_URL", "http://localhost:11434/v1")
    url = base.rstrip("/").rsplit("/v1", 1)[0] + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=4) as r:
            names = [m["name"] for m in json.load(r).get("models", [])]
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return {"name": "local models", "state": "optional",
                "detail": "no local endpoint",
                "fix": "Optional. A local model can be scored alongside hosted "
                       "ones and costs nothing; without it you measure hosted "
                       "models only."}
    vision = [n for n in names if any(t in n.lower() for t in ("vl", "vision", "llava"))]
    return {"name": "local models", "state": "ok",
            "detail": f"{len(names)} model(s)" + (f", {len(vision)} with vision" if vision else ""),
            "fix": "" if vision else "None of them read images, so they can only "
                                     "sit the text exam."}


def check_exam():
    """Is there anything to score, and are its frames actually here?"""
    import glob
    sets = sorted(glob.glob(os.path.join(CODE, "golden", "qa-vision", "sets", "*")))
    usable = []
    for d in sets:
        fr = os.path.join(d, "frames")
        if os.path.isdir(fr) and glob.glob(os.path.join(fr, "*.png")):
            usable.append(os.path.basename(d))
    if usable:
        return {"name": "an exam to score", "state": "ok",
                "detail": ", ".join(usable), "fix": ""}
    return {"name": "an exam to score", "state": "missing",
            "detail": "manifests ship, frames do not",
            "fix": "python3 golden/qa-vision/build_generic.py "
                   "--origin https://your.site --name yours"}


REQUIRED = ("python", "core modules", "ffmpeg", "agent-browser")


def run_all():
    return [check_python(), check_stdlib_only(), check_ffmpeg(),
            check_agent_browser(), check_key(), check_local_model(), check_exam()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true",
                    help="machine-readable, for an agent driving the install")
    a = ap.parse_args()

    checks = run_all()
    blocking = [c for c in checks
                if c["name"] in REQUIRED and c["state"] not in ("ok", "optional")]

    if a.json:
        print(json.dumps({"ok": not blocking, "checks": checks,
                          "blocking": [c["name"] for c in blocking]}, indent=1))
        return 0 if not blocking else 1

    mark = {"ok": "  ok  ", "missing": " miss ", "broken": "BROKEN", "optional": "  --  "}
    print("superrouter doctor\n")
    for c in checks:
        req = "" if c["name"] in REQUIRED else "  (optional)"
        print(f"  [{mark[c['state']]}]  {c['name']:<18} {c['detail']}{req}")
        if c["fix"]:
            print(f"              {c['fix']}")
    print()
    if blocking:
        print(f"  {len(blocking)} thing(s) must be fixed before an exam can be built.")
    else:
        print("  Everything required works. Build an exam against your own product:")
        print("    python3 golden/qa-vision/build_generic.py --origin "
              "https://your.site --name yours")
    return 0 if not blocking else 1


if __name__ == "__main__":
    sys.exit(main())
