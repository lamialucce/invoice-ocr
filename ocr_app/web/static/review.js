// Review screen: show fields next to the page image, let the employee fix
// values, then save or confirm. Plain JS, no build step.
const HEAD = [
  ['supplier_name', 'ספק'], ['supplier_tax_id', 'ע.מ / ח.פ ספק'], ['supplier_number', 'מספר ספק'],
  ['document_type', 'סוג מסמך'], ['document_number', 'מספר מסמך'], ['document_date', 'תאריך'],
  ['currency', 'מטבע'], ['subtotal', 'סה"כ לפני מע"מ'], ['vat', 'מע"מ'], ['total', 'סה"כ לתשלום'],
];
const LINE = [
  ['code', 'קוד ספק'], ['description', 'תיאור'], ['quantity', 'כמות'], ['unit', 'יחידה'],
  ['unit_price', 'מחיר ליחידה'], ['discount', 'הנחה %'], ['line_total', 'סה"כ שורה'],
];
const NUMERIC = new Set(['subtotal', 'vat', 'total', 'quantity', 'unit_price', 'discount', 'line_total']);
const TEXTUAL = new Set(['code', 'description', 'supplier_name']);
const DOC_TYPES = {invoice: 'חשבונית', invoice_receipt: 'חשבונית מס קבלה', delivery_note: 'תעודת משלוח',
  credit_note: 'חשבונית זיכוי', purchase_order: 'הזמנה (לא חשבונית)'};
const STATUS = {not_reviewed: 'מחכה לבדיקה', draft: 'בבדיקה', confirmed: 'אושר'};
const READ = {ok: '', missing: 'לא נמצא במסמך', unclear: 'כדאי לבדוק', check_failed: 'החשבון לא מסתדר'};
const QUALITY = {blurry: 'התמונה מטושטשת', too_dark: 'התמונה חשוכה מדי', low_resolution: 'התמונה קטנה מדי', low_contrast: 'התמונה דהויה או אפורה'};

let data, page = 1, zoom = 1, dirty = false;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const fmt = (v, k) => v === null || v === undefined ? '' :
  (NUMERIC.has(k) && typeof v === 'number' ? String(v) : String(v));

async function load() {
  data = await (await fetch(`/api/doc/${DOC_ID}`)).json();
  renderBanners(); renderHeader(); renderLines(); renderMath(data.math_warnings); renderStatus(); showPage(1);
}

function banner(text, cls = 'warn', extra = '') {
  return `<div class="banner ${cls}"><span>${esc(text)}</span>${extra}</div>`;
}

function renderBanners() {
  const b = [];
  for (const p of data.pages) {
    for (const w of p.quality_warnings) {
      b.push(banner(`עמוד ${p.page}: ${QUALITY[w] || w}. כדאי לצלם שוב כדי שהקריאה תהיה נכונה.`, 'bad',
        `<a class="btn" href="/">צילום חדש</a>`));
    }
  }
  for (const w of data.review.warnings || []) {
    if (!/מטושטשת|חשוכה/.test(w)) b.push(banner(w, /חסרים/.test(w) ? 'bad' : 'warn'));
  }
  const t = data.review.header.document_type;
  if (t && t.value === 'purchase_order') b.push(banner('זה נראה כמו הזמנה ולא חשבונית או תעודת משלוח. בדקו שזה המסמך הנכון.'));
  if (data.duplicates.length) b.push(banner('ייתכן שהקובץ הזה כבר הועלה בעבר.', 'warn',
    data.duplicates.map((d) => `<a href="/doc/${esc(d)}">למסמך הקודם</a>`).join(' ')));
  b.push(banner('קריאת המסמך אינה אישור שהסחורה הגיעה, ושום דבר לא נשלח לבינה אוטומטית.', 'info'));
  $('banners').innerHTML = b.join('');
}

function fieldHTML(key, label, item, target) {
  const v = fmt(item.value, key);
  const st = item.read_status || 'ok';
  const changed = item.changed ? ' changed' : '';
  const reasons = (item.reasons || []).join(' · ');
  const isNum = NUMERIC.has(key);
  let input;
  if (key === 'document_type') {
    input = `<select data-target="${target}" data-key="${key}">` +
      ['', ...Object.keys(DOC_TYPES)].map((k) => `<option value="${k}" ${k === (item.value || '') ? 'selected' : ''}>${k ? DOC_TYPES[k] : '—'}</option>`).join('') + '</select>';
  } else if (key === 'description') {
    input = `<textarea rows="2" dir="auto" data-target="${target}" data-key="${key}">${esc(v)}</textarea>`;
  } else {
    input = `<input ${isNum ? 'inputmode="decimal" dir="ltr"' : 'dir="auto"'} ${key === 'document_date' ? 'type="date"' : ''} value="${esc(v)}" data-target="${target}" data-key="${key}">`;
  }
  const raw = item.original_raw !== null && item.original_raw !== undefined && String(item.original_raw) !== v
    ? `<div class="asread">נקרא: <bdi>${esc(item.original_raw)}</bdi></div>` : '';
  const flag = READ[st] ? `<span class="flag f-${st}">${READ[st]}</span>` : '';
  const written = TEXTUAL.has(key)
    ? `<label class="written" ${item.changed ? '' : 'hidden'}><input type="checkbox" data-written="${target}" ${item.written_on_document ? 'checked' : ''}> התיקון זהה בדיוק למה שכתוב על הנייר</label>` : '';
  return `<div class="field st-${st}${changed}" data-src='${esc(JSON.stringify(item.source || null))}'>
    <label>${label} ${flag}</label>${input}${raw}${reasons && st !== 'ok' ? `<div class="reason">${esc(reasons)}</div>` : ''}${written}</div>`;
}

function renderHeader() {
  const h = data.review.header;
  $('header-fields').innerHTML = HEAD.filter(([k]) => h[k]).map(([k, l]) => fieldHTML(k, l, h[k], `header.${k}`)).join('');
}

function renderLines() {
  const lines = data.review.lines;
  $('lines').innerHTML = lines.map((l, i) => {
    const chk = (l.checks || [])[0];
    const chkTxt = chk ? (chk.result === 'ok' ? '<span class="ok">✔ כמות × מחיר = סה"כ</span>' :
      chk.result === 'failed' ? `<span class="bad">✖ כמות × מחיר = ${chk.expected}, בשורה ${chk.found}</span>` : '') : '';
    const note = chk && chk.note ? `<span class="hint">${esc(chk.note)}</span>` : '';
    const codes = (l.codes_in_description || []).length ? `<span class="hint">דגם בתיאור: <bdi>${esc(l.codes_in_description.join(', '))}</bdi></span>` : '';
    return `<div class="line-card ${l.deleted ? 'deleted' : ''}" data-line="${esc(l.line_id)}" data-row='${esc(JSON.stringify(l.row_bbox ? {page: l.page, bbox: l.row_bbox} : null))}'>
      <div class="row-between"><strong>שורה ${i + 1}${l.added_by_reviewer ? ' (נוספה ידנית)' : ''}</strong>
      <button type="button" class="btn small" data-del="${esc(l.line_id)}">${l.deleted ? 'שחזור שורה' : 'מחיקת שורה'}</button></div>
      <div class="line-grid">${LINE.map(([k, lab]) => fieldHTML(k, lab, l.fields[k] || {value: null, read_status: 'missing'}, `${l.line_id}.${k}`)).join('')}</div>
      <div class="line-foot">${chkTxt} ${note} ${codes}</div></div>`;
  }).join('');
  const active = lines.filter((l) => !l.deleted).length;
  $('lines-count').textContent = `${active} שורות`;
}

function renderMath(msgs) {
  $('math-card').hidden = !msgs || !msgs.length;
  $('math').innerHTML = (msgs || []).map((m) => `<li class="bad">${esc(m)}</li>`).join('');
}

function renderStatus() {
  const s = data.review.status;
  $('doc-status').textContent = STATUS[s] || s;
  $('doc-status').className = `chip r-${s}`;
  const locked = s === 'confirmed';
  document.querySelectorAll('.form-pane input, .form-pane textarea, .form-pane select, .form-pane button').forEach((el) => { el.disabled = locked; });
  $('save').hidden = locked; $('confirm').hidden = locked; $('exports').hidden = !locked;
}

// ---- page viewer
function showPage(n, bbox) {
  const p = data.pages.find((x) => x.page === n) || data.pages[0];
  if (!p) return;
  page = p.page;
  $('page-tabs').innerHTML = data.pages.length > 1 ? data.pages.map((x) =>
    `<button type="button" class="btn small ${x.page === page ? 'primary' : ''}" data-page="${x.page}">עמוד ${x.page}</button>`).join('') : '';
  const img = $('pageimg');
  const src = $('show-original').checked ? p.original_image : p.image;
  $('hl').style.display = 'none';
  const draw = () => { if (bbox && !$('show-original').checked) highlight(bbox); };
  if (img.getAttribute('src') !== src) { img.onload = draw; img.src = src; } else draw();
  img.style.width = `${zoom * 100}%`;
}

function highlight(b) {
  const img = $('pageimg'); const hl = $('hl');
  const s = img.clientWidth / img.naturalWidth;
  Object.assign(hl.style, {left: `${b[0] * s - 4}px`, top: `${b[1] * s - 4}px`,
    width: `${(b[2] - b[0]) * s + 8}px`, height: `${(b[3] - b[1]) * s + 8}px`, display: 'block'});
  const v = $('viewer');
  v.scrollTo({top: b[1] * s - v.clientHeight / 3, left: Math.max(0, b[0] * s - 40), behavior: 'smooth'});
}

document.addEventListener('focusin', (e) => {
  const f = e.target.closest('.field');
  if (!f) return;
  let src = JSON.parse(f.dataset.src || 'null');
  if (!src || !src.bbox) { const card = f.closest('.line-card'); const r = card && JSON.parse(card.dataset.row || 'null'); if (r) src = r; }
  if (src && src.bbox) showPage(src.page || page, src.bbox);
});

document.addEventListener('click', (e) => {
  const pb = e.target.closest('[data-page]');
  if (pb) showPage(+pb.dataset.page);
  const del = e.target.closest('[data-del]');
  if (del) {
    const l = data.review.lines.find((x) => x.line_id === del.dataset.del);
    l.deleted = !l.deleted; l._deletedChanged = true; dirty = true; renderLines();
  }
});

function itemFor(target) {
  const i = target.lastIndexOf('.'); const owner = target.slice(0, i); const key = target.slice(i + 1);
  if (owner === 'header') return data.review.header[key];
  const l = data.review.lines.find((x) => x.line_id === owner);
  if (!l) return null;
  if (!l.fields[key]) l.fields[key] = {value: null, read_status: 'missing'};
  return l.fields[key];
}

document.addEventListener('input', (e) => {
  if (e.target.dataset.written) { const it = itemFor(e.target.dataset.written); if (it) it.written_on_document = e.target.checked; }
  if (e.target.dataset.target) {
    // Keep the typed value in memory, so re-drawing the list does not lose it.
    const it = itemFor(e.target.dataset.target);
    if (it) { it.value = e.target.value; it.changed = true; }
    dirty = true; $('save-msg').textContent = 'יש שינויים שלא נשמרו';
    const f = e.target.closest('.field'); f.classList.add('changed');
    const w = f.querySelector('.written'); if (w) w.hidden = false;
  }
});

$('zoom-in').onclick = () => { zoom = Math.min(zoom * 1.25, 4); showPage(page); };
$('zoom-out').onclick = () => { zoom = Math.max(zoom / 1.25, 0.5); showPage(page); };
$('show-original').onchange = () => showPage(page);
$('add-line').onclick = () => {
  data.review.lines.push({line_id: `new-${Date.now()}`, page: null, row_bbox: null, fields: {}, deleted: false,
    added_by_reviewer: true, _new: true, checks: []});
  renderLines(); dirty = true;
};
window.addEventListener('beforeunload', (e) => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });

function collect() {
  const changes = {header: {}, lines: [], new_lines: [], written: {}};
  document.querySelectorAll('[data-target]').forEach((el) => {
    const [owner, key] = [el.dataset.target.slice(0, el.dataset.target.lastIndexOf('.')), el.dataset.key];
    if (owner === 'header') { changes.header[key] = el.value; return; }
    const l = data.review.lines.find((x) => x.line_id === owner);
    if (l && l._new) { (l._vals = l._vals || {})[key] = el.value; return; }
    let lc = changes.lines.find((x) => x.line_id === owner);
    if (!lc) { lc = {line_id: owner, fields: {}}; changes.lines.push(lc); }
    lc.fields[key] = el.value;
  });
  for (const l of data.review.lines) {
    if (l._new && !l.deleted && l._vals && Object.values(l._vals).some((v) => v)) changes.new_lines.push(l._vals);
    if (l._deletedChanged && !l._new) {
      let lc = changes.lines.find((x) => x.line_id === l.line_id);
      if (!lc) { lc = {line_id: l.line_id, fields: {}}; changes.lines.push(lc); }
      lc.deleted = l.deleted;
    }
  }
  document.querySelectorAll('[data-written]').forEach((el) => { changes.written[el.dataset.written] = el.checked; });
  return changes;
}

async function send(action) {
  if (action === 'confirm') {
    const flagged = document.querySelectorAll('.field.st-unclear:not(.changed), .field.st-check_failed:not(.changed)').length;
    if (flagged && !confirm(`יש ${flagged} שדות מסומנים לבדיקה שלא שונו. לאשר בכל זאת?`)) return;
  }
  $('save-msg').textContent = 'שומר...';
  const r = await fetch(`/api/doc/${DOC_ID}/review`, {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({action, changes: action === 'reopen' ? {} : collect()})});
  const res = await r.json();
  if (!res.ok) { $('save-msg').textContent = ''; alert((res.errors || ['השמירה נכשלה']).join('\n')); return; }
  dirty = false;
  await load();
  $('save-msg').textContent = action === 'confirm' ? 'המסמך אושר ונשמר' : 'נשמר';
}
$('save').onclick = () => send('save');
$('confirm').onclick = () => send('confirm');
$('reopen').onclick = () => send('reopen');
load();
