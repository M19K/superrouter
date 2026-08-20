#!/usr/bin/env python3
"""
generic.py — the defect classes, targeted by ROLE instead of by this site's IDs.

**Why this file replaces the selectors in spec.py.** Those selectors were
`#leaders button`, `#askSend`, `.social a`. A defect class built on one product's
IDs cannot be applied to a second product, which makes it a defect *instance*
wearing a class's clothes — exactly what the no-hardcoding rule forbids, and the
thing that would stop this being a measuring kit for anything but one website.

Here a mutation names a **role** — the headline, the navigation links, the
primary control, the text input, the largest image, the small print at the
bottom. `discover.js` finds whatever the page put in that role. A page with no
such role skips that class, the same way a page without a hub skipped the label
mutations before.

**Assertions stay concrete while discovery stays generic.** The role is found by
rule; the sentence put to the model names what was actually found — "the
heading reading 'Product, systems…'" — because a model asked about "the primary
control" is being tested on jargon rather than on sight.
"""

# Each class: the role it needs, the JS that breaks it, and how to phrase the
# statement it falsifies. {t} is filled with the text actually discovered.
CLASSES = [
    {"id": "faint-text", "role": "headline", "layer": "3 · contrast",
     "js": "E.forEach(e=>{e.style.setProperty('color','rgba(128,128,128,0.06)','important');"
           "e.style.setProperty('-webkit-text-fill-color','rgba(128,128,128,0.06)','important')})",
     "breaks": ("The heading reading “{t}” is clearly legible against the background.", False),
     "control": ("There is text visible on this screen.", True)},

    {"id": "faint-nav", "role": "nav-links", "layer": "3 · contrast",
     "js": "E.forEach(e=>{e.style.setProperty('color','rgba(20,20,20,0.05)','important');"
           "e.style.setProperty('-webkit-text-fill-color','rgba(20,20,20,0.05)','important')})",
     "breaks": ("Every navigation or menu item on this screen is legible against the background.", False),
     "control": ("There is text visible on this screen.", True)},

    {"id": "covered-control", "role": "primary-control", "layer": "3 · nothing covered",
     "js": "E.forEach(e=>{const b=e.getBoundingClientRect();const d=document.createElement('div');"
           "d.style.cssText='position:fixed;z-index:99999;background:#1b1b1b;left:'+(b.left-7)+'px;top:'"
           "+(b.top-7)+'px;width:'+(b.width+14)+'px;height:'+(b.height+14)+'px;border-radius:9px';"
           "document.body.appendChild(d)})",
     "breaks": ("The button labelled or marked “{t}” is visible and not covered by anything.", False),
     "control": ("There is text visible on this screen.", True)},

    {"id": "type-drift", "role": "nav-links", "layer": "1 · opting out of the scale",
     "js": "if(E[1]){E[1].style.setProperty('font-size','46px','important');"
           "E[1].style.setProperty('font-weight','800','important')}",
     "breaks": ("All of the navigation or menu items on this screen are set at roughly the same text size.", False),
     "control": ("There is more than one navigation or menu item on this screen.", True)},

    {"id": "missing-items", "role": "nav-links", "layer": "1 · missing from its registry",
     "js": "E.slice(Math.max(1,Math.ceil(E.length/2))).forEach(e=>e.remove())",
     "breaks": ("Items labelled {names} are all visible on this screen.", False),
     "control": ("There is at least one navigation or menu item on this screen.", True)},

    {"id": "overflow-string", "role": "headline", "layer": "2 · the long string",
     "js": "E.forEach(e=>{e.textContent='Supercalifragilistic'+'a'.repeat(60);"
           "e.style.setProperty('white-space','nowrap','important')})",
     "breaks": ("The heading fits inside the screen and is not cut off at the right-hand edge.", False),
     "control": ("There is text visible on this screen.", True)},

    {"id": "clipped-text", "role": "headline", "layer": "3 · spacing and layout",
     "js": "E.forEach(e=>{e.style.setProperty('max-height','24px','important');"
           "e.style.setProperty('overflow','hidden','important')})",
     "breaks": ("The heading reading “{t}” is fully visible and not cut off part-way through.", False),
     "control": ("There is text visible on this screen.", True)},

    {"id": "hidden-image", "role": "image", "layer": "1 · silently resolves to nothing",
     "js": "E.forEach(e=>e.style.setProperty('opacity','0','important'))",
     "breaks": ("A large picture or graphic is visible on this screen.", False),
     "control": ("There is text visible on this screen.", True)},

    {"id": "squashed-image", "role": "image", "layer": "3 · visual consistency",
     "js": "E.forEach(e=>{e.style.setProperty('transform','scaleX(0.22)','important');"
           "e.style.setProperty('transform-origin','left center','important')})",
     "breaks": ("The large picture on this screen has natural proportions and is not squashed sideways.", False),
     "control": ("There is text visible on this screen.", True)},

    {"id": "invisible-control", "role": "primary-control", "layer": "3 · controls",
     "js": "E.forEach(e=>e.style.setProperty('opacity','0','important'))",
     "breaks": ("The main button on this screen is visible.", False),
     "control": ("There is text visible on this screen.", True)},

    {"id": "tiny-target", "role": "primary-control", "layer": "3 · touch minimum",
     "js": "E.forEach(e=>{['width','height','min-width','min-height'].forEach(p=>"
           "e.style.setProperty(p,'7px','important'));e.style.setProperty('padding','0','important')})",
     "breaks": ("The main button on this screen is a normal, comfortably clickable size.", False),
     "control": ("There is text visible on this screen.", True)},

    {"id": "overlapping-text", "role": "subhead", "layer": "3 · spacing and layout",
     "js": "E.forEach(e=>{e.style.setProperty('margin-top','-84px','important');"
           "e.style.setProperty('position','relative','important');e.style.setProperty('z-index','5','important')})",
     "breaks": ("The heading and the text beneath it are clearly separated and do not overlap.", False),
     "control": ("There is text visible on this screen.", True)},

    {"id": "duplicated-nav", "role": "nav-links", "layer": "1 · duplicate that silently wins",
     "js": "const p=E[0]&&E[0].parentNode; if(p){const c=p.cloneNode(true);"
           "c.style.marginLeft='24px';p.parentNode.insertBefore(c,p.nextSibling)}",
     "breaks": ("Each navigation or menu item on this screen appears exactly once.", False),
     "control": ("There is at least one navigation or menu item on this screen.", True)},

    {"id": "offscreen-input", "role": "text-input", "layer": "2 · state coverage",
     "js": "E.forEach(e=>{const t=e.closest('form')||e;"
           "t.style.setProperty('transform','translateX(62%)','important')})",
     "breaks": ("The text box on this screen sits fully inside the page and is not cut off at the right edge.", False),
     "control": ("There is a text box on this screen.", True)},

    {"id": "dark-band", "role": "footer-text", "layer": "1 · gradient to transparent",
     "js": "const s=document.createElement('style');s.textContent='body::after{content:\"\";"
           "position:fixed;inset:auto 0 0 0;height:38vh;background:linear-gradient(to top,"
           "rgba(0,0,0,.9),transparent);pointer-events:none;z-index:9998}';document.head.appendChild(s)",
     "breaks": ("The small text at the very bottom of the page is clearly legible against what is behind it.", False),
     "control": ("There is text visible on this screen.", True)},
]

# True of any page that renders at all — the control that costs nothing to check
# and catches a model answering "no" to everything.
UNIVERSAL = [("There is text visible on this screen.", True),
             ("This screen is completely blank with nothing rendered on it.", False)]
