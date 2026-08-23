#!/usr/bin/env bash
# capture.sh — rebuild every frame in the QA-vision golden set, from the real site.
#
# The frames are FROZEN on purpose. If the eval drove the live site instead, a
# score that moved would be ambiguous: the model got worse, or the site changed.
# Freezing separates those. Re-run this only when you mean to re-baseline, and
# say so in the log when you do.
#
#   ./capture.sh [origin]      default origin: http://localhost:8934
#
# Every "broken" frame is one mutation injected into the real page at capture
# time. Each mutation is a defect class named in 05-Orchestrator/qa/README.md,
# not an invented one.
set -euo pipefail
ORIGIN="${1:-http://localhost:8934}"
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/frames"
mkdir -p "$OUT"

ab() { agent-browser "$@" >/dev/null 2>&1; }
settle() { sleep "${1:-2}"; }

# Reach a named state, with no mutation applied.
goto() {                      # goto <scrollY> <viewportW> <viewportH> <theme>
  ab set viewport "$2" "$3"
  ab open "$ORIGIN/"
  settle 3
  ab eval "document.documentElement.setAttribute('data-theme','$4'); 'ok'"
  ab eval "window.scrollTo(0,$1); 'ok'"
  settle 3
}

shot() { ab screenshot "$OUT/$1.png"; echo "  $1.png"; }

# Inject CSS. Kept as a <style> so it is one obvious, removable mutation.
mutate() { ab eval "(()=>{const s=document.createElement('style');s.id='mutation';s.textContent=$1;document.head.appendChild(s);return 'ok'})()"; settle 1; }

echo "capturing golden frames from $ORIGIN"

# ── healthy ──────────────────────────────────────────────────────────────
goto 0    1280 800 dark  ; shot hero-dark
goto 0    1280 800 light ; shot hero-light
goto 2312 1280 800 dark  ; shot hub-dark
goto 0     390 844 dark  ; shot hero-mobile

# ── broken · one mutation each ───────────────────────────────────────────

# contrast — text set to all-but-background. QA README Layer 3, "contrast and
# legibility against WCAG AA".
goto 2312 1280 800 dark
mutate "'.leaders *,.leaders{color:#171717 !important;-webkit-text-fill-color:#171717 !important}'"
shot broken-contrast

# covered control — QA README Layer 3, "nothing covered by something else".
goto 2312 1280 800 dark
ab eval "(()=>{const b=document.querySelector('#askSend').getBoundingClientRect();const d=document.createElement('div');d.id='mutation';d.style.cssText='position:fixed;z-index:99999;background:#1a1a1a;left:'+(b.left-6)+'px;top:'+(b.top-6)+'px;width:'+(b.width+12)+'px;height:'+(b.height+12)+'px;border-radius:8px';document.body.appendChild(d);return 'ok'})()"
settle 1; shot broken-covered-control

# type-scale drift — QA README Layer 1, "a component setting its own type sizes
# instead of the scale", seen in Layer 3 as drift between screens.
goto 2312 1280 800 dark
mutate "'#leaders button:nth-of-type(4),#leaders button:nth-of-type(4) span{font-size:46px !important;font-weight:800 !important;letter-spacing:-1px !important}'"
shot broken-type-drift

# a section that silently never loads — QA README Layer 1, "a module missing
# from its own registry". Three of the eight leaders removed.
goto 2312 1280 800 dark
ab eval "(()=>{const ls=[...document.querySelectorAll('.leaders a,.leaders button')];['Media','Contact','Certifications'].forEach(n=>{const e=ls.find(x=>x.textContent.trim()===n); if(e) e.remove()});return 'ok'})()"
settle 1; shot broken-missing-sections

# the long string that breaks the layout — QA README Layer 2, "state coverage".
goto 2312 1280 800 dark
ab eval "(()=>{const e=[...document.querySelectorAll('.leaders a,.leaders button')].find(x=>x.textContent.trim()==='About'); if(e) e.textContent='Aboutttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttt'; return 'ok'})()"
settle 1; shot broken-overflow

# a gradient fading to the keyword `transparent`, which interpolates through
# black and paints a dark band. QA README Layer 1, verbatim. Captured in the
# light theme, which is the only place the band is visible.
goto 0 1280 800 light
mutate "'body::after{content:\"\";position:fixed;inset:auto 0 0 0;height:38vh;background:linear-gradient(to top, rgba(0,0,0,.85), transparent);pointer-events:none;z-index:9998}'"
shot broken-dark-band

echo "done — $(find "$OUT" -type f | wc -l | tr -d ' ') frames in $OUT"
