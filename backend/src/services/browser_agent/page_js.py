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
    if (isPassword) item.value = '';
    else if (tag === 'select') {
      item.value = el.selectedOptions.length ? clip(el.selectedOptions[0].text, maxName) : '';
      item.options = Array.from(el.options).slice(0, 40).map(o => clip(o.text, 80));
    } else if (tag === 'input' || tag === 'textarea') {
      if (type === 'checkbox' || type === 'radio') item.checked = el.checked;
      else if (type === 'file') item.value = Array.from(el.files || []).map(f => f.name).join(', ');
      else item.value = clip(el.value, 300);
    } else if (el.getAttribute('aria-checked') !== null) item.checked = el.getAttribute('aria-checked') === 'true';
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
  if (['combobox', 'option', 'listbox', 'searchbox'].includes(role)) return true;
  if (el.getAttribute('aria-autocomplete') || el.getAttribute('aria-activedescendant')) return true;
  if (el.getAttribute('aria-expanded') === 'true') return true;
  return !!el.closest('[role=listbox], [role=combobox]');
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
# key listeners fire (Skyvern handler.py:8713-8730).
IS_TYPEAHEAD_JS = r"""
(el) => {
  const role = (el.getAttribute('role') || '').toLowerCase();
  return role === 'combobox' || !!el.getAttribute('aria-autocomplete') || el.hasAttribute('list')
    || /select__input|autocomplete|typeahead/i.test(el.className || '');
}
"""
