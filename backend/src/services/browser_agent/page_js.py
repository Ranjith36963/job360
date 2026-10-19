"""The script the browser agent runs inside a page to read it.

It tags every visible control with ``data-j360-ref`` so a later click/type
can find the same element again, and returns plain data only. It never
returns a password box's value. Kept as one string so the Python side has no
Playwright import at module level (rule #16).
"""
from __future__ import annotations

# Returns {elements: [...], text: str, has_password_box: bool}.
# Each element: {ref, tag, type, role, name, value, checked, required,
# disabled, autocomplete, in_form, options}.
SNAPSHOT_JS = r"""
(args) => {
  const maxElements = args.maxElements, maxText = args.maxText, maxName = 120;
  const sel = 'input, textarea, select, button, a[href], [role=button], [role=checkbox], [role=radio], '
    + '[role=combobox], [role=option], [role=tab], [role=link], [contenteditable=true]';
  const visible = (el) => {
    if (el.type === 'file') return true;  // file inputs are often hidden behind a styled button
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none';
  };
  const clip = (t, n) => { t = (t || '').replace(/\s+/g, ' ').trim(); return t.length > n ? t.slice(0, n) + '…' : t; };
  const labelOf = (el) => {
    const aria = el.getAttribute('aria-label');
    if (aria) return aria;
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const t = by.split(/\s+/).map(id => document.getElementById(id)).filter(Boolean).map(n => n.innerText).join(' ');
      if (t.trim()) return t;
    }
    if (el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l && l.innerText.trim()) return l.innerText;
    }
    const wrap = el.closest('label');
    if (wrap && wrap.innerText.trim()) return wrap.innerText;
    if (el.tagName === 'INPUT' && ['submit', 'button'].includes(el.type)) return el.value;
    return el.getAttribute('placeholder') || el.getAttribute('title') || el.innerText || el.getAttribute('name') || '';
  };
  let n = 0, hasPassword = false;
  const out = [];
  for (const el of document.querySelectorAll(sel)) {
    if (!visible(el)) continue;
    const tag = el.tagName.toLowerCase();
    const type = (el.type || el.getAttribute('type') || '').toLowerCase();
    const isPassword = type === 'password' || (el.getAttribute('autocomplete') || '').includes('password');
    if (isPassword) hasPassword = true;
    if (out.length >= maxElements) continue;
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
    if (isPassword) item.value = '';
    else if (tag === 'select') {
      item.value = el.selectedOptions.length ? clip(el.selectedOptions[0].text, maxName) : '';
      item.options = Array.from(el.options).slice(0, 40).map(o => clip(o.text, 80));
    } else if (tag === 'input' || tag === 'textarea') {
      if (type === 'checkbox' || type === 'radio') item.checked = el.checked;
      else if (type === 'file') item.value = Array.from(el.files || []).map(f => f.name).join(', ');
      else item.value = clip(el.value, 300);
    } else if (el.getAttribute('aria-checked') !== null) item.checked = el.getAttribute('aria-checked') === 'true';
    out.push(item);
  }
  const text = clip(document.body ? document.body.innerText : '', maxText);
  return { elements: out, text, has_password_box: hasPassword };
}
"""

# Reads one tagged element's facts again right before acting on it, so the
# safety check runs on what is on the page NOW, not on an old snapshot.
ELEMENT_FACTS_JS = r"""
(el) => {
  const aria = el.getAttribute('aria-label');
  let name = aria || '';
  if (!name && el.id) {
    const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
    if (l) name = l.innerText;
  }
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
