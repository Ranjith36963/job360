"""The script the browser agent runs inside a page to read it.

It tags every visible control with ``data-j360-ref`` so a later click/type
can find the same element again, and returns plain data only. It never
returns a password box's value. Kept as one string so the Python side has no
Playwright import at module level (rule #16).
"""
from __future__ import annotations

# Returns {elements: [...], text: str, has_password_box: bool, omitted_above: int, omitted_below: int}.
# Each element: {ref, tag, type, role, name, value, checked, required, disabled, autocomplete,
# in_form, options, via_label}.
#
# Learned from the open-source browser agents (docs/product/BROWSER_AGENT.md, research 2026-10-19):
# * nearest-first budget: when a page has more controls than fit, keep the ones closest to what is
#   on screen and SAY how many were left above / below (nanobrowser window.ts:110-138,
#   browser-use prompts.py:268-280), instead of silently cutting at the first N in page order;
# * open shadow roots are read (Playwright injected.js:4410-4421; SmartRecruiters' spl-* widgets);
# * a 0x0 / transparent checkbox or radio behind a styled label is read through its label
#   (Skyvern domUtils.js:514-526, nanobrowser enrich.ts:3-5).
SNAPSHOT_JS = r"""
(args) => {
  const maxElements = args.maxElements, maxText = args.maxText, maxName = 120;
  const sel = 'input, textarea, select, button, a[href], [role=button], [role=checkbox], [role=radio], '
    + '[role=combobox], [role=option], [role=tab], [role=link], [role=switch], [contenteditable=true]';
  // Every match in the document AND in open shadow roots, in document order.
  const all = [];
  const walk = (root, depth) => {
    for (const el of root.querySelectorAll(sel)) all.push(el);
    if (depth > 6) return;
    for (const host of root.querySelectorAll('*')) if (host.shadowRoot) walk(host.shadowRoot, depth + 1);
  };
  walk(document, 0);
  const shown = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none';
  };
  const labelEl = (el) => {
    if (el.labels && el.labels.length) return el.labels[0];
    return el.closest('label');
  };
  const clip = (t, n) => { t = (t || '').replace(/\s+/g, ' ').trim(); return t.length > n ? t.slice(0, n) + '…' : t; };
  const labelOf = (el) => {
    const aria = el.getAttribute('aria-label');
    if (aria) return aria;
    const root = el.getRootNode();
    const by = el.getAttribute('aria-labelledby');
    if (by && root.getElementById) {
      const t = by.split(/\s+/).map(id => root.getElementById(id)).filter(Boolean).map(n => n.innerText).join(' ');
      if (t.trim()) return t;
    }
    const l = labelEl(el);
    if (l && l.innerText.trim()) return l.innerText;
    if (el.tagName === 'INPUT' && ['submit', 'button'].includes(el.type)) return el.value;
    return el.getAttribute('placeholder') || el.getAttribute('title') || el.innerText || el.getAttribute('name') || '';
  };
  const vh = window.innerHeight || 900;
  let n = 0, hasPassword = false;
  const candidates = [];
  all.forEach((el, order) => {
    const tag = el.tagName.toLowerCase();
    const type = (el.type || el.getAttribute('type') || '').toLowerCase();
    let box = el, viaLabel = false;
    if (!shown(el)) {
      if (type === 'file') box = el;  // file inputs often sit behind a styled button: always listed
      else if ((type === 'checkbox' || type === 'radio') && labelEl(el) && shown(labelEl(el))) {
        box = labelEl(el); viaLabel = true;
      } else return;
    }
    const isPassword = type === 'password' || (el.getAttribute('autocomplete') || '').includes('password');
    if (isPassword) hasPassword = true;
    const r = box.getBoundingClientRect();
    const dist = r.bottom < 0 ? -r.bottom : r.top > vh ? r.top - vh : 0;
    candidates.push({ el, tag, type, viaLabel, isPassword, order, dist, above: r.bottom < 0 });
  });
  // Nearest the screen first; ties keep page order. Then back to page order for reading.
  const kept = candidates.slice().sort((a, b) => a.dist - b.dist || a.order - b.order).slice(0, maxElements);
  const keptSet = new Set(kept);
  let omittedAbove = 0, omittedBelow = 0;
  for (const c of candidates) if (!keptSet.has(c)) { if (c.above) omittedAbove++; else omittedBelow++; }
  kept.sort((a, b) => a.order - b.order);
  const out = kept.map(({ el, tag, type, viaLabel, isPassword }) => {
    let ref = el.getAttribute('data-j360-ref');
    if (!ref) {
      ref = 'e' + (++n) + '_' + Math.random().toString(36).slice(2, 6);
      el.setAttribute('data-j360-ref', ref);
    }
    const item = {
      ref, tag, type, role: el.getAttribute('role') || '', name: clip(labelOf(el), maxName),
      required: !!(el.required || el.getAttribute('aria-required') === 'true'),
      disabled: !!(el.disabled || el.getAttribute('aria-disabled') === 'true'),
      autocomplete: el.getAttribute('autocomplete') || '', in_form: !!el.closest('form'),
    };
    if (viaLabel) item.via_label = true;
    // The site's own "this answer is wrong / missing" mark: Workday sets it only after Save
    // (ats-autofill skills/fill-app/dom-toolkit.js:431-437).
    if (el.getAttribute('aria-invalid') === 'true') item.invalid = true;
    if (isPassword) item.value = '';
    else if (tag === 'select') {
      item.value = el.selectedOptions.length ? clip(el.selectedOptions[0].text, maxName) : '';
      item.options = Array.from(el.options).slice(0, 40).map(o => clip(o.text, 80));
    } else if (tag === 'input' || tag === 'textarea') {
      if (type === 'checkbox' || type === 'radio') item.checked = el.checked;
      else if (type === 'file') item.value = Array.from(el.files || []).map(f => f.name).join(', ');
      else item.value = clip(el.value, 300);
    } else if (el.getAttribute('aria-checked') !== null) item.checked = el.getAttribute('aria-checked') === 'true';
    else if (el.getAttribute('aria-pressed') !== null) item.pressed = el.getAttribute('aria-pressed') === 'true';
    // Ashby's Yes/No buttons: the chosen one carries a hashed "_active_" class (ApplYourself
    // src/apply/fill.py:1059-1064); buttons only, an open listbox marks its highlighted div too.
    else if (tag === 'button' && /(^|\s)_active_/.test(el.className || '')) item.pressed = true;
    return item;
  });
  const text = clip(document.body ? document.body.innerText : '', maxText);
  return {
    elements: out, text, has_password_box: hasPassword, omitted_above: omittedAbove, omitted_below: omittedBelow,
  };
}
"""

# Reads one tagged element's facts again right before acting on it, so the
# safety check runs on what is on the page NOW, not on an old snapshot.
ELEMENT_FACTS_JS = r"""
(el) => {
  const aria = el.getAttribute('aria-label');
  let name = aria || '';
  if (!name && el.labels && el.labels.length) name = el.labels[0].innerText;
  if (!name && el.tagName === 'INPUT' && ['submit', 'button'].includes(el.type)) name = el.value;
  if (!name) name = el.innerText || el.getAttribute('placeholder') || el.getAttribute('name') || '';
  return {
    tag: el.tagName.toLowerCase(), type: (el.type || el.getAttribute('type') || '').toLowerCase(),
    name: name.replace(/\s+/g, ' ').trim().slice(0, 200), autocomplete: el.getAttribute('autocomplete') || '',
    in_form: !!el.closest('form'), role: el.getAttribute('role') || '',
    // Any typed answer or chosen file anywhere on the page: the form is being filled.
    page_has_answers: Array.from(el.ownerDocument.querySelectorAll('input, textarea')).some((f) => {
      const t = (f.type || '').toLowerCase();
      if (['hidden', 'submit', 'button', 'image', 'checkbox', 'radio', 'password', 'search'].includes(t)) return false;
      if (t === 'file') return !!(f.files && f.files.length);
      return !!(f.value || '').trim();
    }),
  };
}
"""

# Is the focused element a suggestion list (a combobox, an autocomplete box, an option)? Only
# there may the assistant press Enter: anywhere else Enter can submit the form.
FOCUS_IN_LIST_JS = r"""
() => {
  const el = document.activeElement;
  if (!el || el === document.body) return false;
  const role = (el.getAttribute('role') || '').toLowerCase();
  // Focus on an option of an open list: Enter picks it.
  if (role === 'option' || el.closest('[role=listbox]')) return true;
  // A suggestion box counts only while its list is OPEN: a closed combobox inside a form
  // submits on Enter like any field (implicit submission), which would skip check_submit.
  const box = el.closest('[role=combobox]') || el;
  return box.getAttribute('aria-expanded') === 'true' || !!el.getAttribute('aria-activedescendant');
}
"""

# A checkbox / radio hidden behind its styled label: click the label (what a person does), then
# report the real state. Used only after Playwright's own set_checked refused the hidden input.
TICK_VIA_LABEL_JS = r"""
(el, on) => {
  if (el.checked !== on) {
    const label = (el.labels && el.labels[0]) || el.closest('label');
    (label || el).click();
  }
  return el.checked;
}
"""

# React and other framework inputs ignore a plain `value =`: set it through the native setter and
# fire the events a person's typing fires (browser-use default_action_watchdog.py:1392-1405;
# freehire extension/lib/form.ts:798-809). Used only when Playwright's fill() read back wrong.
NATIVE_SET_JS = r"""
(el, v) => {
  const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
  setter.call(el, v);
  for (const t of ['input', 'change', 'blur']) el.dispatchEvent(new Event(t, { bubbles: true }));
  return el.value;
}
"""

# The value a field shows now (inputs, textareas, contenteditable boxes, custom dropdown buttons).
SHOWN_VALUE_JS = r"""
(el) => ('value' in el && typeof el.value === 'string' && el.tagName !== 'BUTTON') ? el.value : (el.innerText || '')
"""

# A suggestion box (react-select, Workday prompt, Lever location): typed key by key so the site's
# key listeners fire (Skyvern handler.py:8713-8730). Lever's box is class="location-input" with no
# role (ApplYourself tests/apply/fixtures/form_lever_minimal.html:26).
IS_TYPEAHEAD_JS = r"""
(el) => {
  const role = (el.getAttribute('role') || '').toLowerCase();
  return role === 'combobox' || !!el.getAttribute('aria-autocomplete') || el.hasAttribute('list')
    || /select__input|autocomplete|typeahead|location-input/i.test(el.className || '');
}
"""

# Lever's two traps (ApplYourself src/apply/fill.py:56-65): the location box is a search box, and
# only a clicked suggestion fills the hidden #selected-location the board checks; and a cookie banner
# (.cc-window) can sit over the first fields.
LEVER_CHECK_JS = r"""
() => {
  const hidden = document.querySelector('#selected-location');
  const shown = document.querySelector('#location-input, input[name="location"]');
  const banner = document.querySelector('.cc-window');
  const r = banner ? banner.getBoundingClientRect() : null;
  return {
    location_unpicked: !!(hidden && shown && shown.value.trim() && !hidden.value.trim()),
    cookie_banner: !!(r && r.width > 1 && r.height > 1 && getComputedStyle(banner).visibility !== 'hidden'),
  };
}
"""

# Workday's page position: its progress bar announces "current step 2 of 7" for screen readers
# (offeros apps/extension/src/lib/autofill/wizard-dom.ts:6-12; packages/autofill/src/wizard.ts:36).
FORM_STEP_JS = r"""
() => {
  const bar = document.querySelector('[data-automation-id="progressBar"]');
  if (!bar) return null;
  const re = /^(current\s+)?step\s+(\d+)\s+of\s+(\d+)$/i;
  let current = 0, total = 0, first = 0;
  for (const el of bar.querySelectorAll('*')) {
    if (el.children.length) continue;
    const m = re.exec((el.textContent || '').trim());
    if (!m) continue;
    total = Math.max(total, Number(m[3]));
    if (!first) first = Number(m[2]);
    if (m[1]) current = Number(m[2]);
  }
  if (!total) return null;
  return { current: current || first, total };
}
"""

# A Workday field: every one sits in a div[data-automation-id^="formField-"] (offeros
# apps/extension/src/lib/autofill/recipes.ts:14-16). Workday ignores script-set values, so these are
# typed as real keys and then left (blur), which is when Workday commits and checks them
# (ats-autofill skills/fill-app/workday.md:11-17; career-ops modes/apply.md:454).
IS_WORKDAY_FIELD_JS = r"""
(el) => !!el.closest('[data-automation-id^="formField-"]')
"""

# Did that click do anything? A MutationObserver counts the page's own changes from just before the
# click to after it settles (nanobrowser src/browser-control/core/interaction/evidence.ts:1-3, :278,
# :309-323: a baseline before dispatch, then changed | none). Our own ref tags are not counted.
WATCH_START_JS = r"""
() => {
  if (window.__j360watch) window.__j360watch.disconnect();
  window.__j360seen = 0;
  const mo = new MutationObserver((ms) => {
    for (const m of ms) {
      if (m.type === 'attributes' && m.attributeName === 'data-j360-ref') continue;
      window.__j360seen++;
    }
  });
  mo.observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
  window.__j360watch = mo;
  return true;
}
"""
WATCH_READ_JS = r"""
() => {
  const n = window.__j360seen;
  if (window.__j360watch) window.__j360watch.disconnect();
  window.__j360watch = null;
  return typeof n === 'number' ? n : -1;
}
"""
# A control's own state, for clicks that change a property but not the page (a checkbox's checked).
CONTROL_STATE_JS = r"""
(el) => JSON.stringify([el.checked, el.value, el.getAttribute('aria-expanded'), el.getAttribute('aria-checked'),
  el.getAttribute('aria-pressed'), el.isConnected])
"""

# The picked option shown near the field (react-select draws it in a sibling, not the input's value).
# Only the field's own small box (up to 3 levels up) counts, so a whole question block cannot.
CHOSEN_NEARBY_JS = r"""
(el, want) => {
  let n = el.parentElement;
  for (let i = 0; i < 3 && n; i++, n = n.parentElement) {
    if ((n.innerText || '').length > 400) break;
    if ((n.innerText || '').toLowerCase().includes(want)) return true;
  }
  return false;
}
"""
