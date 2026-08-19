#!/usr/bin/env python3
"""
spec.py — the golden set as a declaration, not a pile of files.

**This is the part that makes measuring quality cheap enough to actually do.**

Labelling is why nobody measures quality per task: someone has to look at a
thousand outputs and say which are right. Mutation testing sidesteps that. Take
a product that works, break it in a way you chose, and you know the right answer
without anyone labelling anything — you *planted* it.

So the set is declared as `states × mutations`:

  a STATE is a real screen of a real product, healthy.
  a MUTATION is one named way a screen can be wrong, with the statement it
  falsifies and a statement it leaves alone.

Every combination that applies produces a frame and two scored cases: one that
can only be answered by seeing the defect, and one control that stays true so a
model cannot pass by calling everything broken.

**Adding a defect class is one dict entry and it multiplies across every state
it applies to.** That is the no-hardcoding rule doing real work: a mutation
targets a *kind* of element, never a particular one.
"""

# ── the screens ──────────────────────────────────────────────────────────────
# `groups` is what a state actually contains, so a mutation can say what it
# needs rather than naming the states it happens to work on.
STATES = [
    {"id": "hero-dark",   "scroll": 0,    "vw": 1280, "vh": 800, "theme": "dark",
     "groups": {"topbar", "headline", "portrait", "footer"}},
    {"id": "hero-light",  "scroll": 0,    "vw": 1280, "vh": 800, "theme": "light",
     "groups": {"topbar", "headline", "portrait", "footer"}},
    {"id": "hub-dark",    "scroll": 2312, "vw": 1280, "vh": 800, "theme": "dark",
     "groups": {"topbar", "leaders", "askbar"}},
    {"id": "hub-light",   "scroll": 2312, "vw": 1280, "vh": 800, "theme": "light",
     "groups": {"topbar", "leaders", "askbar"}},
    {"id": "hero-mobile", "scroll": 0,    "vw": 390,  "vh": 844, "theme": "dark",
     "groups": {"topbar", "headline", "portrait"}},
]

# Statements that are true of every healthy screen carrying that group. These
# are the controls, and they double as the healthy-frame cases.
TRUE_OF_HEALTHY = {
    "topbar":   [("The site name 'Maaz Kazi' is readable in the top-left corner.", True),
                 ("The site name in the top-left corner reads 'Maaz Khan'.", False)],
    "headline": [("The large headline text is fully readable and not cut off.", True),
                 ("The headline is a single short word.", False)],
    "portrait": [("A portrait photograph of a person is visible.", True),
                 ("The portrait photograph is in full colour.", False)],
    "leaders":  [("Text labels naming sections are visible around the sphere.", True),
                 ("A portrait photograph of a person is visible.", False)],
    "askbar":   [("There is a wide text input with the placeholder 'Ask me about anything.'", True),
                 ("The heading 'Product, systems, and the thing between them' is visible.", False)],
    "footer":   [("Small text appears at the very bottom-left of the page.", True),
                 ("A wireframe sphere or globe is visible.", False)],
}

# ── the ways a screen can be wrong ───────────────────────────────────────────
# Each is a defect CLASS. `needs` is the group it acts on, so it applies to
# every state carrying that group and to none that do not.
#
#   breaks  — a statement that is TRUE of the healthy screen and FALSE here.
#             Answering it correctly requires seeing the defect.
#   control — a statement that stays TRUE despite the defect.
#   layer   — where this class comes from in 05-Orchestrator/qa/README.md.
MUTATIONS = [
    {"id": "faint-labels", "needs": "leaders", "layer": "3 · contrast",
     "js": "S('#leaders button,#leaders span{color:#191919!important;-webkit-text-fill-color:#191919!important}')",
     "breaks": ("Every section label around the sphere is legible against the background.", False),
     "control": ("There is a wide text input with the placeholder 'Ask me about anything.'", True)},

    {"id": "faint-headline", "needs": "headline", "layer": "3 · contrast",
     "js": "S('#heroCopy h1{color:rgba(128,128,128,0.06)!important;-webkit-text-fill-color:rgba(128,128,128,0.06)!important}')",
     "breaks": ("The large headline text is fully readable and not cut off.", False),
     "control": ("The site name 'Maaz Kazi' is readable in the top-left corner.", True)},

    {"id": "covered-send", "needs": "askbar", "layer": "3 · nothing covered",
     "js": "COVER('#askSend')",
     "breaks": ("The round arrow send button at the right end of the text input is visible and unobstructed.", False),
     "control": ("There is a wide text input with the placeholder 'Ask me about anything.'", True)},

    {"id": "covered-social", "needs": "topbar", "layer": "3 · nothing covered",
     "js": "COVER('.social')",
     "breaks": ("Social media icons are visible in the top bar.", False),
     "control": ("The site name 'Maaz Kazi' is readable in the top-left corner.", True)},

    {"id": "type-drift-label", "needs": "leaders", "layer": "1 · opting out of the scale",
     "js": "S('#leaders button:nth-of-type(4),#leaders button:nth-of-type(4) span{font-size:46px!important;font-weight:800!important}')",
     "breaks": ("All of the section labels around the sphere are set at roughly the same text size.", False),
     "control": ("Text labels naming sections are visible around the sphere.", True)},

    {"id": "type-drift-mark", "needs": "topbar", "layer": "1 · opting out of the scale",
     "js": "S('#mark{font-size:7px!important} #mark span{font-size:6px!important}')",
     "breaks": ("The site name in the top-left is set at a normal, readable size rather than a tiny one.", False),
     "control": ("Social media icons are visible in the top bar.", True)},

    {"id": "missing-labels", "needs": "leaders", "layer": "1 · missing from its registry",
     "js": "REMOVE_TEXT('#leaders button', ['Media','Contact','Certifications'])",
     "breaks": ("Labels reading Media, Contact and Certifications are all visible on this screen.", False),
     "control": ("There is a wide text input with the placeholder 'Ask me about anything.'", True)},

    {"id": "missing-social", "needs": "topbar", "layer": "1 · missing from its registry",
     "js": "(()=>{const a=document.querySelectorAll('.social a');for(let i=1;i<a.length;i++)a[i].remove();return 1})()",
     "breaks": ("There are three separate social media icons in the top bar.", False),
     "control": ("The site name 'Maaz Kazi' is readable in the top-left corner.", True)},

    {"id": "overflow-label", "needs": "leaders", "layer": "2 · the long string",
     "js": "SET_TEXT('#leaders button', 'About', 'About'+'t'.repeat(70))",
     "breaks": ("Every section label is a short word or phrase, with none running as a long unbroken string of repeated letters.", False),
     "control": ("There is a wide text input with the placeholder 'Ask me about anything.'", True)},

    {"id": "overflow-headline", "needs": "headline", "layer": "2 · the long string",
     "js": "(()=>{const h=document.querySelector('#heroCopy h1');h.textContent='Supercalifragilisticexpialidociousnessalonglongunbrokenword';h.style.whiteSpace='nowrap';return 1})()",
     "breaks": ("The headline text fits inside the screen and is not cut off at the right-hand edge.", False),
     "control": ("A portrait photograph of a person is visible.", True)},

    {"id": "dark-band", "needs": "footer", "layer": "1 · gradient to transparent",
     "js": "S('body::after{content:\"\";position:fixed;inset:auto 0 0 0;height:38vh;background:linear-gradient(to top,rgba(0,0,0,.9),transparent);pointer-events:none;z-index:9998}')",
     "breaks": ("The small text at the very bottom of the page is clearly legible against what is behind it.", False),
     "control": ("A portrait photograph of a person is visible.", True)},

    {"id": "clipped-headline", "needs": "headline", "layer": "3 · spacing and layout",
     "js": "S('#heroCopy h1{max-height:26px!important;overflow:hidden!important}')",
     "breaks": ("The large headline text is fully readable and not cut off.", False),
     "control": ("A portrait photograph of a person is visible.", True)},

    {"id": "hidden-portrait", "needs": "portrait", "layer": "1 · silently resolves to nothing",
     "js": "S('#scene{opacity:0!important}')",
     "breaks": ("A portrait photograph of a person is visible.", False),
     "control": ("The site name 'Maaz Kazi' is readable in the top-left corner.", True)},

    {"id": "invisible-send", "needs": "askbar", "layer": "3 · controls",
     "js": "S('#askSend{opacity:0!important}')",
     "breaks": ("The round arrow send button at the right end of the text input is visible and unobstructed.", False),
     "control": ("There is a wide text input with the placeholder 'Ask me about anything.'", True)},

    {"id": "tiny-target", "needs": "askbar", "layer": "3 · touch minimum",
     "js": "S('#askSend{width:7px!important;height:7px!important;min-width:7px!important;padding:0!important}')",
     "breaks": ("The send button at the right of the input is a normal, comfortably clickable size.", False),
     "control": ("There is a wide text input with the placeholder 'Ask me about anything.'", True)},

    {"id": "overlapping-text", "needs": "headline", "layer": "3 · spacing and layout",
     "js": "S('#heroCopy p{margin-top:-84px!important;position:relative!important;z-index:5!important}')",
     "breaks": ("The headline and the sentence beneath it are clearly separated and do not overlap each other.", False),
     "control": ("A portrait photograph of a person is visible.", True)},

    {"id": "duplicated-nav", "needs": "topbar", "layer": "1 · duplicate that silently wins",
     "js": "(()=>{const n=document.querySelector('.social');const c=n.cloneNode(true);c.style.marginLeft='26px';n.parentNode.insertBefore(c,n.nextSibling);return 1})()",
     "breaks": ("The set of social media icons appears exactly once in the top bar.", False),
     "control": ("The site name 'Maaz Kazi' is readable in the top-left corner.", True)},

    {"id": "offscreen-input", "needs": "askbar", "layer": "2 · state coverage",
     "js": "S('#askbar{transform:translateX(62%)!important}')",
     "breaks": ("The text input sits fully inside the screen and is not cut off at the right-hand edge.", False),
     "control": ("Text labels naming sections are visible around the sphere.", True)},
]


def plan():
    """Every (state, mutation) pair that applies, plus the healthy states."""
    healthy = [{"frame": s["id"], "state": s, "mutation": None} for s in STATES]
    broken = [{"frame": f"{s['id']}__{m['id']}", "state": s, "mutation": m}
              for s in STATES for m in MUTATIONS if m["needs"] in s["groups"]]
    return healthy, broken


if __name__ == "__main__":
    h, b = plan()
    print(f"{len(h)} healthy frames, {len(b)} broken frames")
    print(f"cases: {sum(len(TRUE_OF_HEALTHY[g]) for s in STATES for g in s['groups'])} healthy "
          f"+ {len(b) * 2} broken = "
          f"{sum(len(TRUE_OF_HEALTHY[g]) for s in STATES for g in s['groups']) + len(b) * 2}")
    print(f"defect-sight cases (the ones that decide catch rate): {len(b)}")
