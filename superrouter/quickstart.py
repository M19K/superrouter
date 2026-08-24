#!/usr/bin/env python3
"""
quickstart.py — clone to running proxy, in one command, stopping before money.

    python3 -m superrouter.quickstart --origin https://your.site --name yours
    python3 -m superrouter.quickstart --origin … --name yours --spend   # actually score
    python3 -m superrouter.quickstart --origin … --name yours --json    # for an agent

**Why this exists.** Every step of this already worked and none of them were
joined up. A person cloned the repo, read a README, ran four commands in order,
read a table, and then started a proxy themselves — and if any step needed a
program that was not installed, they found out ten minutes into a screenshot
run. Two artefacts and a human in between is where an agent stops.

**It refuses to spend money by default, and that is the whole design.** Scoring
a ladder costs real dollars on somebody else's account. So the default run does
everything free — check the machine, photograph the product, plant the defects,
price the ladder — and then stops and prints the bill it would incur. `--spend`
is the second, deliberate command. A tool that quietly charges you on first run
is a tool nobody runs twice.

**Every step reports what it did, what it cost, and what to do if it failed**,
so an agent driving this has somewhere to go other than "it didn't work".
"""
import argparse
import json
import os
import subprocess
import sys

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def step(name, cmd, cwd=None, capture=True):
    """Run one step. Returns (ok, output). Never raises — a failed step is a
    result the caller reports, not a traceback the caller has to interpret."""
    try:
        r = subprocess.run(cmd, cwd=cwd or CODE, text=True,
                           capture_output=capture, timeout=3600)
        out = ((r.stdout or "") + (r.stderr or "")) if capture else ""
        return r.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, "timed out after an hour"
    except FileNotFoundError as e:
        return False, f"command not found: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--origin", action="append", required=True,
                    help="a URL of your product; repeat for several screens")
    ap.add_argument("--name", required=True, help="short id for this product")
    ap.add_argument("--spend", action="store_true",
                    help="actually score the ladder. Costs money. Off by default.")
    ap.add_argument("--model", action="append", default=[],
                    help="candidate model; repeatable. Sensible defaults if omitted.")
    ap.add_argument("--json", action="store_true", help="machine-readable")
    a = ap.parse_args()

    report = {"product": a.name, "origins": a.origin, "steps": [], "ok": True}

    def record(name, ok, detail, fix=""):
        report["steps"].append({"step": name, "ok": ok,
                                "detail": detail.strip()[-1500:], "fix": fix})
        if not ok:
            report["ok"] = False
        if not a.json:
            print(f"\n── {name} {'·' * max(1, 58 - len(name))} {'ok' if ok else 'FAILED'}")
            if not ok:
                print(detail.strip()[-1200:])
                if fix:
                    print(f"\n  {fix}")
        return ok

    # 1 ─ can this machine do it at all
    ok, out = step("doctor", [PY, "-m", "superrouter.doctor", "--json"])
    if not record("checking this machine", ok, out,
                  "Run `python3 -m superrouter.doctor` for the fix for each item."):
        return _finish(report, a)

    # 2 ─ photograph the product and plant the defects. Free.
    cmd = [PY, os.path.join(CODE, "golden", "qa-vision", "build_generic.py"),
           "--name", a.name]
    for o in a.origin:
        cmd += ["--origin", o]
    ok, out = step("build", cmd)
    if not record(f"building an exam from {len(a.origin)} screen(s)", ok, out,
                  "A page with more on it, or more --origin screens, gives the "
                  "builder more defect classes to plant."):
        return _finish(report, a)
    report["exam"] = out.strip().splitlines()[-1] if out.strip() else ""

    # 3 ─ what would scoring cost. Free.
    ok, out = step("dry-run", [PY, "-m", "superrouter.evals", "--set", a.name, "--dry-run"])
    record("pricing the run", ok, out)
    report["estimate"] = out.strip()[-1200:]

    if not a.spend:
        report["stopped"] = "before spending"
        if not a.json:
            print(out.strip())
            print("\n" + "=" * 70)
            print("Stopped here on purpose. Everything above cost nothing.")
            print("The next step calls real models on your account. To do it:")
            print(f"\n  python3 -m superrouter.quickstart --name {a.name} "
                  + " ".join(f"--origin {o}" for o in a.origin) + " --spend")
            print("=" * 70)
        return _finish(report, a)

    # 4 ─ score the ladder. This is the part that costs money.
    models = a.model or ["google/gemma-3-12b-it", "anthropic/claude-haiku-4.5",
                         "anthropic/claude-sonnet-5"]
    cmd = [PY, "-m", "superrouter.evals", "--set", a.name]
    for m in models:
        cmd += ["--model", m]
    ok, out = step("score", cmd)
    if not record(f"scoring {len(models)} model(s)", ok, out,
                  "A provider key is needed here and only here — see "
                  "`python3 -m superrouter.doctor`."):
        return _finish(report, a)
    report["scores"] = out.strip()[-2000:]

    # 5 ─ turn the measurement into a decision
    ok, out = step("route-table", [PY, "-m", "superrouter.route_table"])
    record("deciding what to route where", ok, out)
    report["table"] = out.strip()[-2000:]

    # 6 ─ and say exactly how to run it
    report["serve"] = "python3 -m superrouter.serve --shadow 20"
    if not a.json:
        print(out.strip())
        print("\n" + "=" * 70)
        print("Measured. To put it in front of your agent:")
        print("\n  python3 -m superrouter.serve --shadow 20")
        print("\nThen point your client at http://localhost:8787/v1 and ask for")
        print("the model `superrouter/auto`. `--shadow 20` sends one call in 20")
        print("to the reference model as well, so drift shows up while it is")
        print("happening rather than in a report months later.")
        print("=" * 70)
    return _finish(report, a)


def _finish(report, a):
    if a.json:
        print(json.dumps(report, indent=1))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
