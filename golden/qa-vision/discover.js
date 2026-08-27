/* discover.js — find the parts of ANY page by role, not by name.
 *
 * The first version of this golden set targeted `#leaders button` and `#askSend`.
 * Those are one site's IDs, so a "defect class" built on them is a defect
 * instance wearing a class's clothes — it could never be applied to a second
 * product, which is the whole no-hardcoding rule.
 *
 * This finds the same roles anywhere: the biggest heading, the navigation links,
 * the primary control, the text input, the repeated small labels, the largest
 * image, the smallest text near the bottom. A mutation names a role; whatever
 * the page puts in that role is what gets broken. A page with no such role
 * simply skips that class, exactly as before.
 */
window.SR_FIND = function (role) {
  const vis = (e) => {
    const b = e.getBoundingClientRect(), s = getComputedStyle(e);
    return b.width > 4 && b.height > 4 && b.top < innerHeight && b.bottom > 0 &&
           b.left < innerWidth && b.right > 0 &&
           s.visibility !== 'hidden' && s.display !== 'none' && +s.opacity > 0.05;
  };
  const all = (sel) => [...document.querySelectorAll(sel)].filter(vis);
  const area = (e) => { const b = e.getBoundingClientRect(); return b.width * b.height; };
  const size = (e) => parseFloat(getComputedStyle(e).fontSize) || 0;
  const txt = (e) => (e.textContent || '').trim();
  // A button and the span inside it are the same thing to a person looking at
  // the screen, and counting both makes "how many labels" wrong.
  const outermost = (list) => list.filter(e => !list.some(o => o !== e && o.contains(e)));
  // What ordinary body text measures on this page, so "large" means large
  // relative to the page rather than to a number picked in advance.
  const body = (() => {
    const t = all('p,li,span,div').filter(e => e.children.length === 0 && txt(e).length > 30)
      .map(size).sort((a, b) => a - b);
    return t.length ? t[Math.floor(t.length / 2)] : 16;
  })();

  switch (role) {
    case 'headline': {
      // **Semantics first, size only as a fallback.** The size rule alone —
      // "1.6x body or it is not a heading" — is a rule about how loud a
      // product's type scale is, not about whether the page has a heading.
      // Measured 2026-08-27 on Handrail's onboarding screen: a real <h1> at
      // 19px against 12.5px body is 1.52x, so the page was reported as having
      // no headline at all and four defect classes had nothing to break. A
      // restrained type scale is a design choice, not an absent heading.
      //
      // So an actual heading element wins outright when the page has one, and
      // the size heuristic is kept for the div-soup pages it was written for.
      const heads = all('h1,h2,h3,h4').filter(e => txt(e).length > 2);
      if (heads.length) {
        const rank = (e) => ({ H1: 0, H2: 1, H3: 2, H4: 3 })[e.tagName];
        return [heads.sort((a, b) => rank(a) - rank(b) || size(b) - size(a))[0]];
      }
      const c = all('p,div,span,a,button')
        .filter(e => txt(e).length > 12 && txt(e).length < 200 && e.children.length === 0);
      const top = c.sort((a, b) => size(b) - size(a))[0];
      // no headline is a legitimate answer — a page can simply not have one,
      // and inventing one turns a label into a heading
      return top && size(top) >= body * 1.6 ? [top] : [];
    }
    case 'subhead': {
      const h = window.SR_FIND('headline')[0];
      if (!h) return [];
      const c = all('p,div,span').filter(e =>
        e !== h && e.children.length === 0 && txt(e).length > 20 &&
        e.getBoundingClientRect().top > h.getBoundingClientRect().top);
      return c.sort((a, b) =>
        a.getBoundingClientRect().top - b.getBoundingClientRect().top).slice(0, 1);
    }
    case 'nav-links': {
      // links or buttons living in a header/nav, or pinned to the top strip
      let c = all('header a, nav a, header button, nav button');
      if (c.length < 2) c = all('a,button').filter(e =>
        e.getBoundingClientRect().top < Math.max(90, innerHeight * 0.12));
      return outermost(c).slice(0, 12);
    }
    case 'labels': {
      // repeated short text that acts as a set of section labels
      const c = all('a,button,li,span').filter(e =>
        e.children.length <= 1 && txt(e).length > 1 && txt(e).length < 28);
      const byTop = {};
      c.forEach(e => { const k = Math.round(size(e)); (byTop[k] = byTop[k] || []).push(e); });
      const biggest = Object.values(byTop).sort((a, b) => b.length - a.length)[0] || [];
      const dedup = outermost(biggest);
      return dedup.length >= 3 ? dedup.slice(0, 12) : [];
    }
    case 'primary-control': {
      // the most prominent button that is not a nav link
      const nav = new Set(window.SR_FIND('nav-links'));
      const c = all('button,[role=button],input[type=submit]').filter(e => !nav.has(e));
      return c.sort((a, b) => area(b) - area(a)).slice(0, 1);
    }
    case 'text-input':
      // **A text box is a text box.** Listing only `type=text` meant every
      // password, email, search, url, telephone and number field on the web
      // was invisible to this — which is to say every sign-in screen. Measured
      // 2026-08-27: Locus's key screen is built around one `type=password`
      // input and reported `text-input: 0`. Types that are NOT a box a person
      // types into — checkbox, radio, submit, file, range, colour — stay out.
      return all('input[type=text],input[type=password],input[type=email],'
                 + 'input[type=search],input[type=url],input[type=tel],'
                 + 'input[type=number],input:not([type]),textarea,'
                 + '[contenteditable=true]')
        .sort((a, b) => area(b) - area(a)).slice(0, 1);
    case 'image':
      return all('img,canvas,svg,video').sort((a, b) => area(b) - area(a))
        .filter(e => area(e) > 8000).slice(0, 1);
    case 'footer-text': {
      const c = all('*').filter(e => e.children.length === 0 && txt(e).length > 2 &&
        e.getBoundingClientRect().bottom > innerHeight * 0.82);
      return outermost(c).sort((a, b) => size(a) - size(b)).slice(0, 2);
    }
    default:
      return [];
  }
};

/* What the page actually put in each role, so an assertion can name it
   concretely while the discovery that found it stays generic. */
window.SR_ROLES = function () {
  const out = {};
  for (const r of ['headline', 'subhead', 'nav-links', 'labels', 'primary-control',
                   'text-input', 'image', 'footer-text']) {
    const es = window.SR_FIND(r);
    out[r] = es.map(e => ({
      text: (e.textContent || '').trim().slice(0, 40),
      tag: e.tagName.toLowerCase(),
      label: e.getAttribute('aria-label') || e.getAttribute('placeholder') || '',
    }));
  }
  return out;
};
