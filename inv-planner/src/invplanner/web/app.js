/* Refinery inventory planner - front end.
 *
 * Deliberately dependency-free so it runs straight off the API with no build
 * step. The views map onto the workbook it replaces: alerts = the capacity
 * signal planners look for, projection = the per-product stream sheets,
 * schedule = the charge schedule tab, source data = the feed paste areas.
 */
const state = {
  scenario: null,
  scenarios: [],
  products: [],
  block: null,
  feeds: [],
  feed: null,
  uploads: [],
  schedOffset: 0,
  dashGroups: null,
  dashGroup: null,
  refKind: null,
  // The run being solved - started from this tab, or seen in the runs list.
  running: null,
  runPending: false,   // this tab's run request has not come back yet
  runsSeq: 0,
  runsPoll: null,
};

const $ = (sel) => document.querySelector(sel);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k === 'html') n.innerHTML = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    n.appendChild(typeof kid === 'object' ? kid : document.createTextNode(String(kid)));
  }
  return n;
};

const fmt = (n, d = 0) =>
  n === null || n === undefined || Number.isNaN(n) ? '—'
    : n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtK = (n) => {
  if (n === null || n === undefined) return '—';
  const a = Math.abs(n);
  if (a >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (a >= 1e3) return Math.round(n / 1e3) + 'k';
  return String(Math.round(n));
};
const shortDate = (iso) => {
  const [y, m, d] = iso.split('-');
  return `${+m}/${+d}/${y.slice(2)}`;
};
// Chart axes tick once a month, so the day would be noise. ISO reads as a
// sort key rather than a date on an axis - `26-07` in particular gets read as
// a year first.
const monthYear = (iso) => {
  const [y, m] = iso.split('-');
  return `${+m}/${y.slice(2)}`;
};

async function api(path, opts) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!res.ok) {
    const text = await res.text();
    toast(`Error: ${res.status} ${text.slice(0, 140)}`);
    throw new Error(text);
  }
  return res.json();
}

let toastTimer = null;
function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 2600);
}

/* ------------------------------------------------------------------ boot */
/* Rebuild the picker without changing what is selected. Finishing a run adds a
   scenario, and the list used to be reloaded through `boot`, which selects the
   newest - so a finished run quietly selected its own result, and the next run
   or new scenario started from that instead of the plan. */
async function loadScenarioList() {
  state.scenarios = await api('/api/scenarios');
  const sel = $('#scenario-select');
  sel.innerHTML = '';
  state.scenarios.forEach((s) => sel.appendChild(el('option', { value: s.id },
    s.name + (s.run_status === 'unverified' ? ' · unverified' : ''))));
  if (state.scenario) sel.value = state.scenario.id;
}

async function boot() {
  await loadScenarioList();
  if (!state.scenarios.length) {
    $('#scenario-meta').textContent = 'No scenario yet — create one to begin.';
    return;
  }
  $('#scenario-select').value = state.scenarios[0].id;
  await selectScenario(state.scenarios[0].id);
}

async function selectScenario(id) {
  state.scenario = await api(`/api/scenarios/${id}`);
  $('#scenario-meta').textContent =
    `as of ${state.scenario.as_of} · ${state.scenario.horizon_days} day horizon · reference v${state.scenario.reference_version_id}`;
  state.products = await api(`/api/scenarios/${id}/products`);
  const psel = $('#product-select');
  psel.innerHTML = '';
  state.products.forEach((p) =>
    psel.appendChild(el('option', { value: p.block_id }, `${p.code} — ${p.name} (${p.sheet})`)));
  state.block = state.products[0]?.block_id || null;
  if (state.block) psel.value = state.block;

  const offSel = $('#sched-offset');
  offSel.innerHTML = '';
  for (let i = 0; i < state.scenario.horizon_days; i += 21) {
    offSel.appendChild(el('option', { value: i }, `day ${i + 1}`));
  }
  refreshRunButton();
  await refreshActive();
}

/* "started 8:09 PM, stops by about 8:24 PM". The stop time comes from the
   run's own limit, which v2 runs to; greedy has none worth quoting. */
function runClock(r) {
  const hm = (d) => d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  const t0 = new Date(r.created_at);
  if (r.model_version === 'greedy' || !r.time_limit_seconds) return `started ${hm(t0)}`;
  const end = new Date(t0.getTime() + r.time_limit_seconds * 1000);
  return `started ${hm(t0)}, stops by about ${hm(end)}`;
}

const runOutcome = (status) => ({
  done: 'verified and proved optimal',
  not_proved_optimal: 'verified, not proved optimal',
  unverified: 'rejected by the simulator',
}[status] || status);

// A run setting as the planner reads it. Empty is 'default', as on the inputs.
const fmtSetting = (v) => (v === null || v === undefined ? 'default'
  : typeof v === 'boolean' ? (v ? 'on' : 'off')
  : typeof v === 'object' ? JSON.stringify(v) : String(v));

/* The run button is in one view and the scenario picker is in the page header,
   so nothing on screen told you which schedule you were about to solve. Opening
   a run's result changes the selection silently, which is how a run ended up
   solving the previous run's output. Name it on the button itself. */
function refreshRunButton() {
  const btn = $('#opt-run');
  if (!btn) return;
  // While a run is solving the button says so and cannot start another. It used
  // to flash "Solving…" for 2.6 s and stay clickable through the whole solve.
  const r = state.running;
  btn.disabled = !!r;
  if (r) {
    btn.classList.remove('warn');
    btn.textContent = `Solving on “${r.base_scenario_name}” · ${runClock(r)}`;
    btn.title = 'One run at a time - the button comes back when this one finishes';
    return;
  }
  const s = state.scenario;
  if (!s) {
    btn.textContent = 'Run optimizer';
    return;
  }
  btn.textContent = `Run optimizer on “${s.name}”`;
  const proposed = s.status === 'proposed';
  btn.classList.toggle('warn', proposed);
  btn.title = proposed
    ? 'This schedule is itself an optimizer result. Re-solving one is not '
      + 'supported: the charge floor is a fraction of whatever you feed in, so '
      + 'each pass adds a new minimum on every line-day the last one created, '
      + 'and the model goes infeasible. Pick the plan you started from.'
    : 'Solves this schedule and writes the answer back as a new scenario';
}

function activeView() {
  return document.querySelector('.tabs button.active').dataset.view;
}

async function refreshActive() {
  const v = activeView();
  if (v === 'alerts') await loadAlerts();
  else if (v === 'dashboard') await loadDashboard();
  else if (v === 'stream') { await populateStreams(); await loadStream(); }
  else if (v === 'projection') await loadProjection();
  else if (v === 'schedule') await loadSchedule();
  else if (v === 'feeds') await loadFeeds();
  else if (v === 'reference') await loadReference();
  else if (v === 'optparams') await loadOptParams();
  else if (v === 'optruns') await loadOptRuns();
}

/* ---------------------------------------------------------------- alerts */
async function loadAlerts() {
  if (!state.scenario) return;
  const days = $('#alert-window').value;
  const data = await api(`/api/scenarios/${state.scenario.id}/alerts?days=${days}`);
  const a = data.alerts;

  const overflow = a.filter((x) => x.overflow_days).length;
  const stockout = a.filter((x) => x.stockout_days).length;
  const band = a.filter((x) => !x.overflow_days && !x.stockout_days).length;
  $('#alert-summary').replaceChildren(
    stat(stockout, 'products run dry', stockout ? 'danger' : 'ok'),
    stat(overflow, 'products overflow tank', overflow ? 'warn' : 'ok'),
    stat(band, 'outside control band', 'info'),
    stat(state.products.length - a.length, 'products clear', 'ok'),
  );

  const host = $('#alerts-table');
  if (!a.length) {
    host.replaceChildren(el('div', { class: 'empty' },
      `No capacity or stockout problems in the next ${data.window_days} days.`));
    return;
  }
  const rows = a.map((x) => el('tr', {
    class: 'clickable',
    onclick: () => openProjection(x.block_id),
  },
    el('td', { class: 'sticky' }, `${x.code} — ${x.name}`),
    el('td', {}, x.sheet),
    el('td', {}, issuePill(x)),
    el('td', { class: 'num' }, x.first_stockout || x.first_overflow || '—'),
    el('td', { class: 'num' }, fmt(x.trough)),
    el('td', { class: 'num' }, fmt(x.peak)),
    el('td', { class: 'num' }, fmt(x.capacity)),
    el('td', { class: 'num' }, x.below_lcl_days || '—'),
    el('td', { class: 'num' }, x.above_ucl_days || '—'),
  ));
  host.replaceChildren(table(
    ['Product', 'Stream', 'Issue', 'First day', 'Low point (gal)', 'Peak (gal)',
     'Capacity (gal)', 'Days < LCL', 'Days > UCL'],
    rows, [0]));
}

function issuePill(x) {
  if (x.stockout_days) return el('span', { class: 'pill danger' }, `runs dry · ${x.stockout_days}d`);
  if (x.overflow_days) return el('span', { class: 'pill warn' }, `overflows · ${x.overflow_days}d`);
  return el('span', { class: 'pill info' }, 'outside band');
}

function stat(n, label, kind) {
  return el('div', { class: `stat ${kind || ''}` },
    el('div', { class: 'n' }, fmt(n)), el('div', { class: 'l' }, label));
}

function table(headers, rows, stickyCols = []) {
  const thead = el('thead', {}, el('tr', {},
    headers.map((h, i) => el('th', {
      class: [stickyCols.includes(i) ? 'sticky' : '',
              i >= 1 && /\(|days|Days/.test(h) ? 'num' : ''].join(' ').trim(),
    }, h))));
  return el('table', {}, thead, el('tbody', {}, rows));
}

async function openProjection(blockId) {
  state.block = blockId;
  $('#product-select').value = blockId;
  switchTab('projection');
}

/* ------------------------------------------------------------ dashboards */
/* Small multiples: one chart per product, sections stacked by stream. Each
 * chart carries the same three references as the workbook's chart tabs -
 * ending inventory, tank capacity, and the LCL/UCL control band. */
async function loadDashboardGroups() {
  if (state.dashGroups) return;
  state.dashGroups = await api('/api/dashboards');
  const host = $('#dash-groups');
  host.replaceChildren(...state.dashGroups.map((g) =>
    el('button', {
      class: 'group-tab' + (g.id === state.dashGroup ? ' active' : ''),
      'data-group': g.id,
      onclick: () => { state.dashGroup = g.id; loadDashboard(); },
    }, g.title)));
}

async function loadDashboard() {
  if (!state.scenario) return;
  await loadDashboardGroups();
  if (!state.dashGroup) state.dashGroup = state.dashGroups[0].id;
  document.querySelectorAll('.group-tab').forEach((b) =>
    b.classList.toggle('active', b.dataset.group === state.dashGroup));

  const days = $('#dash-days').value;
  const d = await api(`/api/scenarios/${state.scenario.id}/dashboard`
    + `?group=${state.dashGroup}&days=${days}`);

  $('#dash-title').textContent = d.title;
  $('#dash-blurb').textContent =
    `${d.blurb} — ${shortDate(d.from)} to ${shortDate(d.to)}`;

  const host = $('#dash-sections');
  if (!d.sections.length) {
    host.replaceChildren(el('div', { class: 'card' },
      el('div', { class: 'empty' }, 'No products in this group.')));
    return;
  }

  host.replaceChildren(...d.sections.map((sec) =>
    el('section', { class: 'dash-stream' },
      el('div', { class: 'dash-stream-head' },
        el('h3', {}, sec.stream),
        el('span', { class: 'muted' },
          `${sec.products.length} product${sec.products.length > 1 ? 's' : ''}`)),
      el('div', { class: 'chart-grid' },
        ...sec.products.map((p) => chartCard(p))))));
}

function chartCard(p) {
  const badge = p.dry_days
    ? el('span', { class: 'pill danger' }, `dry ${p.dry_days}d`)
    : p.overflow_days
      ? el('span', { class: 'pill warn' }, `over cap ${p.overflow_days}d`)
      : el('span', { class: 'pill ok' }, 'in bounds');

  const card = el('button', {
    class: 'chart-card',
    title: 'Open the full projection for this product',
    onclick: () => openProjection(p.block_id),
  },
    el('div', { class: 'chart-card-head' },
      el('div', {},
        el('b', {}, p.code), ' ',
        el('span', { class: 'chart-name' }, p.name)),
      badge),
    el('div', { class: 'chart-body', html: miniChart(p) }),
    el('div', { class: 'chart-foot' },
      el('span', {}, `peak ${fmtK(p.peak)}`),
      el('span', {}, `low ${fmtK(p.trough)}`),
      el('span', {}, p.capacity[0] ? `cap ${fmtK(p.capacity[0])}` : 'no capacity set'),
    ));
  return card;
}

function miniChart(p) {
  const W = 360, H = 118, P = { t: 8, r: 6, b: 14, l: 6 };
  const vals = p.end;
  const caps = p.capacity;
  const capMax = Math.max(...caps);
  let hi = Math.max(capMax || 0, ...vals, p.ucl || 0);
  let lo = Math.min(0, ...vals, p.lcl != null ? p.lcl : 0);
  if (!(hi > lo)) { hi = lo + 1; }
  const pad = (hi - lo) * 0.1;
  hi += pad; lo -= pad;

  const x = (i) => P.l + (i / Math.max(1, vals.length - 1)) * (W - P.l - P.r);
  const y = (v) => P.t + (1 - (v - lo) / (hi - lo)) * (H - P.t - P.b);
  const parts = [];

  if (p.lcl != null && p.ucl != null) {
    parts.push(`<rect x="${P.l}" y="${y(p.ucl)}" width="${W - P.l - P.r}" `
      + `height="${Math.max(0, y(p.lcl) - y(p.ucl))}" fill="var(--ok)" opacity="0.10"/>`);
  } else if (p.ucl != null) {
    parts.push(line(P.l, y(p.ucl), W - P.r, y(p.ucl), 'var(--ok)', 1, '2 4'));
  }
  if (lo < 0) parts.push(line(P.l, y(0), W - P.r, y(0), 'var(--danger)', 1, '3 3'));

  // capacity can step mid-horizon when a tank project lands, so draw the series
  if (capMax > 0) {
    const capPts = caps.map((c, i) => `${x(i).toFixed(1)},${y(c).toFixed(1)}`).join(' ');
    parts.push(`<polyline points="${capPts}" fill="none" stroke="var(--warn)" `
      + `stroke-width="1.4" stroke-dasharray="5 3"/>`);
  }

  const pts = vals.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const base = y(Math.max(lo, 0));
  parts.push(`<polygon points="${x(0)},${base} ${pts} ${x(vals.length - 1)},${base}" `
    + `fill="var(--accent)" opacity="0.12"/>`);
  parts.push(`<polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="1.8"/>`);

  // month ticks, unlabelled - the axis range is in the header
  let lastMonth = null;
  p.dates.forEach((iso, i) => {
    const m = iso.slice(0, 7);
    if (m !== lastMonth) {
      lastMonth = m;
      if (i > 0) parts.push(line(x(i), P.t, x(i), H - P.b, 'var(--border)', 1));
      parts.push(`<text class="axis-text" x="${x(i) + 3}" y="${H - 3}">`
        + `${monthYear(iso)}</text>`);
    }
  });

  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" `
    + `aria-label="${p.code} projected inventory">${parts.join('')}</svg>`;
}

/* ---------------------------------------------------------- stream sheet */
/* Rendered as an HTML string rather than through createElement: a stream can be
 * 18 products x 11 measures x 60 days, and per-cell DOM calls make it crawl. */
async function loadStream() {
  if (!state.scenario) return;
  const sheet = $('#stream-select').value;
  if (!sheet) return;
  const days = $('#stream-days').value;
  const offset = $('#stream-offset').value || 0;
  const keyOnly = $('#stream-key-rows').checked;
  const d = await api(`/api/scenarios/${state.scenario.id}/stream`
    + `?sheet=${encodeURIComponent(sheet)}&days=${days}&offset=${offset}`
    + `&key_rows_only=${keyOnly}`);

  const esc = (s) => String(s).replace(/[&<>]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));

  const head = ['<thead><tr>',
    '<th class="sticky sticky-1">Product</th>',
    '<th class="sticky sticky-2">Measure</th>',
    d.dates.map((iso) => {
      const dow = new Date(iso + 'T00:00:00').getDay();
      return `<th class="num${dow === 0 || dow === 6 ? ' weekend' : ''}">${shortDate(iso)}</th>`;
    }).join(''),
    '</tr></thead>'].join('');

  const body = [];
  for (const b of d.blocks) {
    const bad = b.flags.filter((f) => f.over || f.dry).length;
    body.push(
      `<tr class="stream-head"><td class="sticky sticky-1" colspan="2">`
      + `<b>${esc(b.code)}</b> ${esc(b.name)}`
      + (b.sold_code && b.sold_code !== b.code ? ` <span class="muted-inline">+ ${esc(b.sold_code)}</span>` : '')
      + `</td>`
      + `<td colspan="${d.dates.length}" class="stream-head-meta">`
      + `capacity ${fmt(b.capacity)} ${d.unit}`
      + (b.lcl != null ? ` · LCL ${fmtK(b.lcl)}` : '')
      + (b.ucl != null ? ` · UCL ${fmtK(b.ucl)}` : '')
      + (bad ? ` · <span class="pill warn">${bad} day${bad > 1 ? 's' : ''} out of bounds</span>` : '')
      + `</td></tr>`);

    for (const r of b.rows) {
      const m = r.measure;
      const isEnd = m === 'End Inventory';
      const cls = isEnd ? ' row-end' : (m === 'Begin Inventory' ? ' row-begin' : '');
      const cells = r.values.map((v, i) => {
        const f = b.flags[i] || {};
        let c = 'num';
        if (isEnd && f.dry) c += ' cell-dry';
        else if (isEnd && f.over) c += ' cell-over';
        else if (v < 0) c += ' neg';
        if (!v) c += ' zero';
        return `<td class="${c}">${v ? fmt(v) : '·'}</td>`;
      }).join('');
      body.push(`<tr class="${cls.trim()}">`
        + `<td class="sticky sticky-1"></td>`
        + `<td class="sticky sticky-2">${esc(r.label)}</td>${cells}</tr>`);
    }
  }

  $('#stream-grid').innerHTML =
    `<table class="stream-table">${head}<tbody>${body.join('')}</tbody></table>`;
}

async function populateStreams() {
  const streams = await api(`/api/scenarios/${state.scenario.id}/streams`);
  const sel = $('#stream-select');
  const current = sel.value;
  sel.innerHTML = '';
  streams.forEach((s) => sel.appendChild(
    el('option', { value: s.sheet }, `${s.sheet} (${s.products})`)));
  if (streams.some((s) => s.sheet === current)) sel.value = current;

  const off = $('#stream-offset');
  if (!off.options.length) {
    const dates = state.scenario.horizon_days;
    for (let i = 0; i < dates; i += 7) {
      off.appendChild(el('option', { value: i }, `day ${i + 1}`));
    }
  }
}

/* ------------------------------------------------------------ projection */
async function loadProjection() {
  if (!state.scenario || !state.block) return;
  const days = $('#proj-days').value;
  const d = await api(
    `/api/scenarios/${state.scenario.id}/projection?block_id=${encodeURIComponent(state.block)}&days=${days}`);
  drawChart(d);
  drawProjTable(d);
}

function drawChart(d) {
  const rows = d.rows;
  const W = 1200, H = 300, P = { t: 12, r: 66, b: 26, l: 8 };
  const cap = rows[0]?.capacity || 0;
  const vals = rows.map((r) => r.end);
  let hi = Math.max(cap, ...vals, d.ucl || 0);
  let lo = Math.min(0, ...vals);
  if (hi === lo) hi = lo + 1;
  const pad = (hi - lo) * 0.08;
  hi += pad; lo -= pad;

  const x = (i) => P.l + (i / Math.max(1, rows.length - 1)) * (W - P.l - P.r);
  const y = (v) => P.t + (1 - (v - lo) / (hi - lo)) * (H - P.t - P.b);

  const parts = [];
  // control band
  if (d.lcl != null && d.ucl != null) {
    parts.push(`<rect x="${P.l}" y="${y(d.ucl)}" width="${W - P.l - P.r}" height="${Math.max(0, y(d.lcl) - y(d.ucl))}" fill="var(--ok)" opacity="0.09"/>`);
  }
  // zero + capacity reference lines
  if (lo < 0) parts.push(line(P.l, y(0), W - P.r, y(0), 'var(--danger)', 1, '4 3'));
  if (cap) {
    parts.push(line(P.l, y(cap), W - P.r, y(cap), 'var(--warn)', 1.5, '6 4'));
    parts.push(`<text class="axis-text" x="${W - P.r + 6}" y="${y(cap) + 3}" fill="var(--warn)">cap ${fmtK(cap)}</text>`);
  }
  [d.lcl, d.ucl].forEach((v, i) => {
    if (v == null) return;
    parts.push(line(P.l, y(v), W - P.r, y(v), 'var(--ok)', 1, '2 4'));
    parts.push(`<text class="axis-text" x="${W - P.r + 6}" y="${y(v) + 3}" fill="var(--ok)">${i ? 'UCL' : 'LCL'} ${fmtK(v)}</text>`);
  });

  // area + line for ending inventory
  const pts = rows.map((r, i) => `${x(i).toFixed(1)},${y(r.end).toFixed(1)}`).join(' ');
  parts.push(`<polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="2"/>`);
  parts.push(`<polygon points="${x(0)},${y(Math.max(lo, 0))} ${pts} ${x(rows.length - 1)},${y(Math.max(lo, 0))}" fill="var(--accent)" opacity="0.10"/>`);

  // month ticks
  let lastMonth = null;
  rows.forEach((r, i) => {
    const m = r.date.slice(0, 7);
    if (m !== lastMonth) {
      lastMonth = m;
      parts.push(line(x(i), P.t, x(i), H - P.b, 'var(--border)', 1));
      parts.push(`<text class="axis-text" x="${x(i) + 4}" y="${H - P.b + 14}">${monthYear(r.date)}</text>`);
    }
  });
  // y labels
  [hi - pad, (hi + lo) / 2, lo + pad].forEach((v) => {
    parts.push(`<text class="axis-text" x="${W - P.r + 6}" y="${y(v) + 3}">${fmtK(v)}</text>`);
  });

  $('#proj-chart').innerHTML =
    `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Projected ending inventory">${parts.join('')}</svg>`;

  $('#proj-legend').replaceChildren(
    legend('var(--accent)', 'Ending inventory'),
    legend('var(--warn)', 'Tank capacity'),
    ...(d.lcl != null ? [legend('var(--ok)', 'Control band (LCL/UCL)')] : []),
    el('span', {}, `${d.code} — ${d.name}`),
  );
}

const line = (x1, y1, x2, y2, stroke, w, dash) =>
  `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${stroke}" stroke-width="${w}"${dash ? ` stroke-dasharray="${dash}"` : ''}/>`;

const legend = (color, label) =>
  el('span', {}, el('i', { style: `background:${color}` }), label);

function drawProjTable(d) {
  const rows = d.rows.map((r) => {
    const over = r.capacity > 0 && r.end > r.capacity;
    const neg = r.end < 0;
    return el('tr', {},
      el('td', { class: 'sticky' }, shortDate(r.date)),
      el('td', { class: 'num' }, fmt(r.begin)),
      el('td', { class: 'num' }, fmt(r.production_in)),
      el('td', { class: 'num' }, fmt(r.sales)),
      el('td', { class: 'num' }, fmt(r.forecast)),
      el('td', { class: 'num' }, fmt(r.blends)),
      el('td', { class: 'num' }, fmt(r.production_out)),
      el('td', { class: 'num' }, fmt(r.out_to_diesel + r.downgrade)),
      el('td', { class: `num ${neg ? 'neg' : over ? 'over' : ''}` }, fmt(r.end)),
      el('td', { class: 'num' }, fmt(r.excess)),
      el('td', {}, neg ? el('span', { class: 'pill danger' }, 'dry')
        : over ? el('span', { class: 'pill warn' }, 'over cap') : ''),
    );
  });
  $('#proj-table').replaceChildren(table(
    ['Date', 'Begin (gal)', 'Production in', 'Orders', 'Forecast', 'Blends',
     'Charged out', 'Downgraded', 'End (gal)', 'Headroom', ''],
    rows, [0]));
}

/* -------------------------------------------------------------- schedule */
async function loadSchedule() {
  if (!state.scenario) return;
  const days = $('#sched-days').value;
  const offset = $('#sched-offset').value || 0;
  const pair = state.scenario.pair || {};
  const q = `days=${days}&offset=${offset}`
    + (state.compare && pair.other_id ? `&compare=${pair.other_id}` : '');
  const d = await api(`/api/scenarios/${state.scenario.id}/schedule?${q}`);
  renderCompareBar(pair, d.compare);

  const head = ['Unit / charge product', ...d.dates.map(shortDate)];
  const rows = [];

  const crudeCells = d.dates.map((iso) => {
    const c = d.crude[iso] || {};
    return el('td', { class: 'num' },
      el('input', {
        class: 'cell-input' + (c.bbl ? ' on' : ''), type: 'number', value: c.bbl || '',
        title: `Crude charge ${iso}`,
        onchange: (e) => editCrude(iso, parseFloat(e.target.value) || 0, null),
      }));
  });
  rows.push(el('tr', {}, el('td', { class: 'sticky' }, 'Crude unit — charge (bbl)'), crudeCells));

  const modeCells = d.dates.map((iso) => {
    const c = d.crude[iso] || {};
    return el('td', { class: 'num' },
      el('button', {
        class: 'linkish ' + (c.mode ? `mode-${c.mode}` : ''),
        title: 'R = regular (makes 9117) · L = low volatility (makes 9118)',
        onclick: () => editCrude(iso, null, c.mode === 'R' ? 'L' : 'R'),
      }, c.mode || '·'));
  });
  rows.push(el('tr', {}, el('td', { class: 'sticky' }, 'Crude unit — mode'), modeCells));

  for (const unit of d.units) {
    const down = new Set(unit.downtime || []);
    rows.push(el('tr', { class: 'sched-unit' },
      el('td', { class: 'sticky' }, unit.unit),
      el('td', { colspan: d.dates.length },
        down.size ? `${down.size} day${down.size > 1 ? 's' : ''} of planned downtime in view` : '')));

    // Downtime toggle row: the planner's switch for "this unit cannot run".
    rows.push(el('tr', { class: 'sched-downtime' },
      el('td', { class: 'sticky' }, 'Down'),
      d.dates.map((iso) => el('td', { class: 'num' },
        el('button', {
          class: 'dt-cell' + (down.has(iso) ? ' on' : ''),
          title: down.has(iso)
            ? `${unit.unit} is down on ${iso} — click to clear`
            : `Mark ${unit.unit} down on ${iso}`,
          onclick: () => toggleDowntime(unit.unit, iso),
        }, down.has(iso) ? '×' : '·')))));

    for (const lineRow of unit.lines) {
      const cells = d.dates.map((iso) => {
        const v = lineRow.values[iso] || 0;
        const isDown = down.has(iso);
        // Comparing: show the other schedule under the cell and mark the day if
        // they disagree. The interesting part of a proposal is never the total,
        // it is which day it moved a campaign to.
        const other = lineRow.compare ? (lineRow.compare[iso] || 0) : null;
        const differs = other !== null && Math.abs(v - other) > 0.5;
        return el('td', {
          class: 'num' + (isDown ? ' cell-down' : '') + (differs ? ' cell-diff' : ''),
        },
          el('input', {
            class: 'cell-input' + (v ? ' on' : ''), type: 'number', value: v || '',
            disabled: isDown,
            title: isDown
              ? `${unit.unit} is down on ${iso}`
              : `${lineRow.code} ${lineRow.name} · ${iso}`
                + (other !== null ? `\n${d.compare.name}: ${fmt(other)}` : ''),
            onchange: (e) => editSchedule(lineRow.key, iso, parseFloat(e.target.value) || 0),
          }),
          other !== null
            ? el('div', { class: 'cell-compare' + (differs ? ' differs' : '') },
                other ? fmt(other) : '·')
            : null);
      });
      rows.push(el('tr', {},
        el('td', { class: 'sticky' }, `${lineRow.code} ${lineRow.name || ''}`), cells));
    }
  }
  $('#schedule-grid').replaceChildren(table(head, rows, [0]));
  drawDowntime(d);
  drawFill(d);
}

/* ------------------------------------------------------------------ fill */
/* Building a plan is not the same job as correcting one. The grid edits a cell
   at a time, which is right for a fix and hopeless for laying a rate down over a
   366-day window - and that is exactly what a cold start is: crude, ROSE and the
   Platformer written across every day, and the units the optimizer is meant to
   decide for emptied. Done by hand it is several hundred keystrokes per row, and
   the run is only as good as the last cell that got typed. */
function drawFill(d) {
  const sel = $('#fill-target');
  const units = d.units.map((u) => u.unit);
  const sig = units.join('|');
  if (sel.dataset.sig !== sig) {
    const keep = sel.value;
    sel.replaceChildren(
      el('optgroup', { label: 'Crude' },
        el('option', { value: 'CRUDE' }, 'Crude unit — charge')),
      ...d.units.map((u) => el('optgroup', { label: u.unit },
        u.lines.map((l) => el('option', { value: l.key },
          `${l.code} ${l.name || ''}`.trim())))),
      // Clearing a unit is its own operation, not a rate of zero on one line:
      // it is what hands the optimizer an empty schedule to decide for.
      el('optgroup', { label: 'Clear a whole unit' },
        units.map((u) => el('option', { value: u }, `${u} — clear every line`))));
    sel.dataset.sig = sig;
    if (keep) sel.value = keep;
    if (!sel.value) sel.value = 'CRUDE';
  }
  // Default to the whole window, and reset when the scenario changes - a range
  // left over from the previous scenario can sit outside this one entirely, and
  // a fill that silently wrote nothing would look like a fill that worked.
  const s = state.scenario;
  const last = new Date(Date.parse(`${s.as_of}T00:00:00Z`)
    + (s.horizon_days - 1) * 86400000).toISOString().slice(0, 10);
  const panel = $('#fill-panel');
  if (panel.dataset.scn !== String(s.id)) {
    panel.dataset.scn = String(s.id);
    $('#fill-start').value = s.as_of;
    $('#fill-end').value = last;
    $('#fill-note').textContent = '';
  }
  $('#fill-start').min = s.as_of; $('#fill-start').max = last;
  $('#fill-end').min = s.as_of; $('#fill-end').max = last;
  fillTargetChanged();
}

/* A whole-unit target can only clear, so the rate box is not a choice there and
   should not look like one. The crude mode is the mirror image: meaningless on
   any other row. */
function fillTargetChanged() {
  const t = $('#fill-target').value || '';
  const wholeUnit = t !== 'CRUDE' && !t.includes('#');
  const bbl = $('#fill-bbl');
  bbl.disabled = wholeUnit;
  bbl.placeholder = wholeUnit ? 'clears the unit' : '0 clears';
  if (wholeUnit) bbl.value = '';
  $('#fill-mode-field').style.display = t === 'CRUDE' ? '' : 'none';
}

async function applyFill() {
  if (!state.scenario) return;
  const target = $('#fill-target').value;
  const wholeUnit = target !== 'CRUDE' && !target.includes('#');
  const bbl = wholeUnit ? 0 : (parseFloat($('#fill-bbl').value) || 0);
  const start = $('#fill-start').value;
  const end = $('#fill-end').value;
  if (end && start && end < start) { toast('End date is before the start date'); return; }
  const mode = target === 'CRUDE' ? ($('#fill-mode').value || null) : null;
  // Overwriting hundreds of cells is not undoable from here, so the one thing
  // that is easy to get wrong - the range - is read back before it happens.
  const what = bbl
    ? `set ${target} to ${fmt(bbl)} bbl/day`
    : `clear ${target}`;
  if (!window.confirm(`${what} on every day from ${start} to ${end}?`)) return;

  const r = await api(`/api/scenarios/${state.scenario.id}/schedule/fill`, {
    method: 'POST',
    body: JSON.stringify({
      target, bbl, start, end, mode,
      overwrite: !$('#fill-empty-only').checked,
    }),
  });
  const bits = [`${r.cells} cell${r.cells === 1 ? '' : 's'} written`];
  if (r.skipped_downtime) bits.push(`${r.skipped_downtime} outage day(s) skipped`);
  if (r.siblings_cleared) {
    bits.push(`${r.siblings_cleared} cleared on ${r.siblings.join(', ')}`);
  }
  $('#fill-note').textContent =
    `${what} · ${r.start} to ${r.end} · ${bits.join(' · ')}`;
  toast(bits.join(' · '));
  loadSchedule();
}

/* -------------------------------------------------------------- downtime */
function drawDowntime(d) {
  const sel = $('#dt-unit');
  if (!sel.options.length) {
    d.units.filter((u) => !u.unit.startsWith('TRANSFER')
      && !u.unit.startsWith('SONNEBORN') && !u.unit.startsWith('RAILCAR'))
      .forEach((u) => sel.appendChild(el('option', { value: u.unit }, u.unit)));
  }
  if (!$('#dt-start').value) $('#dt-start').value = d.dates[0];
  if (!$('#dt-end').value) $('#dt-end').value = d.dates[0];

  const wins = d.downtime_windows || [];
  const host = $('#downtime-list');
  if (!wins.length) {
    host.replaceChildren(el('div', { class: 'empty' },
      'No planned downtime. Every unit is expected to run every day.'));
    return;
  }
  const rows = wins.map((wnd) => el('tr', {},
    el('td', { class: 'sticky' }, wnd.unit),
    el('td', {}, wnd.start),
    el('td', {}, wnd.end),
    el('td', { class: 'num' }, wnd.days),
    el('td', {}, wnd.reason || ''),
    el('td', {}, wnd.status === 'detected'
      ? el('span', {
          class: 'pill warn',
          title: 'Inferred from a gap in the workbook plan, not read from a '
               + 'maintenance calendar — confirm before relying on it',
        }, 'detected')
      : el('span', { class: 'pill ok' }, 'confirmed')),
    el('td', {}, el('button', {
      class: 'linkish', onclick: () => removeDowntime(wnd.id),
    }, 'remove')),
  ));
  host.replaceChildren(table(
    ['Unit', 'From', 'To', 'Days', 'Reason', 'Status', ''], rows, [0]));
}

async function toggleDowntime(unit, date) {
  const r = await api(`/api/scenarios/${state.scenario.id}/downtime/toggle`, {
    method: 'POST',
    body: JSON.stringify({ unit, date }),
  });
  toast(r.down ? `${unit} marked down on ${date}` : `${unit} cleared on ${date}`);
  loadSchedule();
}

async function addDowntime() {
  const unit = $('#dt-unit').value;
  const start = $('#dt-start').value;
  const end = $('#dt-end').value;
  if (!unit || !start || !end) return;
  if (end < start) { toast('End date is before the start date'); return; }
  await api(`/api/scenarios/${state.scenario.id}/downtime`, {
    method: 'POST',
    body: JSON.stringify({ unit, start, end, reason: $('#dt-reason').value }),
  });
  toast(`${unit} down ${start} to ${end}`);
  $('#dt-reason').value = '';
  loadSchedule();
}

async function removeDowntime(id) {
  await api(`/api/scenarios/${state.scenario.id}/downtime/${id}`,
    { method: 'DELETE' });
  toast('Downtime removed');
  loadSchedule();
}

/* The warm start / result pair. A planner holding their own schedule wants the
   optimizer's beside it; holding a proposal, they want their own back. Same
   question, so one control serves both directions. */
/* Hand the planner a block to paste rather than writing their workbook. That
   file carries 192 charts and a VBA project; editing it in place drops parts of
   it, and a forced recalculation ran for 28 minutes before it had to be killed. */
function exportSchedule() {
  if (!state.scenario) return;
  const days = $('#sched-days').value;
  const offset = $('#sched-offset').value || 0;
  window.location = `/api/scenarios/${state.scenario.id}`
    + `/schedule.xlsx?days=${days}&offset=${offset}`;
  toast(`Exporting ${days} days — one paste block per unit, each with its cell`);
}

function renderCompareBar(pair, compare) {
  const host = $('#sched-compare');
  if (!host) return;
  const exportBtn = el('button', {
    class: 'linkish',
    title: 'Download the visible window as blocks that paste into the workbook',
    onclick: exportSchedule,
  }, 'Export for Excel');
  // A schedule the simulator rejected is kept to be inspected, never exported.
  const exportCtl = pair && pair.role === 'result' && pair.run_status === 'unverified'
    ? el('span', {
        class: 'pill warn',
        title: 'The simulator rejected this schedule, so it cannot be exported',
      }, 'unverified · not exportable')
    : exportBtn;
  if (!pair || !pair.other_id) {
    host.replaceChildren(
      el('span', { class: 'muted' },
        'Run the optimizer on this schedule to compare it with a proposal.'),
      exportBtn);
    return;
  }
  const showing = state.compare;
  host.replaceChildren(
    exportCtl,
    el('span', { class: 'pill info' },
      pair.role === 'result' ? 'Optimizer proposal' : 'Your schedule'),
    el('button', {
      class: showing ? 'btn' : 'linkish',
      title: showing
        ? 'Hide the other schedule'
        : `Show ${pair.other_name} under each cell`,
      onclick: () => { state.compare = !state.compare; loadSchedule(); },
    }, showing ? `Hide ${pair.other_role}` : `Compare with ${pair.other_role}`),
    el('button', {
      class: 'linkish',
      title: 'Switch the grid to the other schedule',
      onclick: () => openResult(pair.other_id, null),
    }, `Switch to ${pair.other_role}`),
    // Spread, not `: null` - unlike el(), replaceChildren prints a null child
    // as the word "null", which is what the bar ended with.
    ...(compare
      ? [el('span', { class: 'muted' },
          `${fmt(compare.cells_changed)} cell${compare.cells_changed === 1 ? '' : 's'} differ`
          + ` · under each cell is ${compare.name}`)]
      : []),
  );
}

async function editSchedule(lineKey, date, bbl) {
  try {
    await api(`/api/scenarios/${state.scenario.id}/schedule`, {
      method: 'PATCH',
      body: JSON.stringify({ line_key: lineKey, date, bbl }),
    });
  } catch (e) {
    loadSchedule();  // refused: put the stored value back (see saveOptParam)
    return;
  }
  toast(`Saved ${bbl.toLocaleString()} bbl · projection re-simulated`);
}

async function editCrude(date, bbl, mode) {
  try {
    await api(`/api/scenarios/${state.scenario.id}/crude`, {
      method: 'PATCH',
      body: JSON.stringify({ date, bbl, mode }),
    });
    toast(mode ? `Crude mode ${mode} on ${date}` : `Crude charge saved`);
  } catch (e) {
    // refused - the redraw below puts the stored value back
  }
  loadSchedule();
}

/* ----------------------------------------------------------------- feeds */
async function loadFeeds() {
  state.feeds = await api('/api/feeds');
  await loadUploadSlots();
  const host = $('#feed-cards');
  host.replaceChildren(...state.feeds.map((f) =>
    el('button', {
      class: 'feed-card' + (state.feed === f.feed ? ' selected' : ''),
      onclick: () => openFeed(f.feed),
    },
      el('h3', {}, f.title),
      el('p', { class: 'muted' }, f.description),
      el('div', { class: 'meta' },
        el('div', {}, el('b', {}, fmt(f.rows)), 'values'),
        el('div', {}, el('b', {}, fmt(f.overrides)), 'overridden'),
        f.stale_overrides
          ? el('div', {}, el('b', { class: 'neg' }, fmt(f.stale_overrides)), 'stale')
          : null),
      el('p', { class: 'muted', style: 'margin-top:8px;font-size:11px' },
        f.last_sync ? `synced ${new Date(f.last_sync).toLocaleString()}` : 'never synced'),
    )));
  if (state.feed) await loadFeedRows();
}

/* One card per uploadable sheet. The file goes up as the raw request body
 * rather than a form post, which is why there is no <form> here at all. */
async function loadUploadSlots() {
  const d = await api('/api/feeds/uploads');
  state.uploads = d.kinds;
  $('#upload-slots').replaceChildren(...d.kinds.map((k) => {
    const input = el('input', {
      type: 'file',
      accept: '.xlsx,.xlsm',
      style: 'display:none',
      onchange: (e) => uploadSheet(k.kind, e.target.files[0], e.target),
    });
    return el('div', { class: 'feed-card' },
      el('h3', {}, k.title),
      el('p', { class: 'muted' }, k.shape),
      el('p', { class: 'muted', style: 'margin-top:8px;font-size:11px' },
        k.loaded
          ? `uploaded ${new Date(k.uploaded_at).toLocaleString()}`
          : 'reading from the planning workbook'),
      el('div', { style: 'margin-top:10px;display:flex;gap:10px;align-items:center' },
        input,
        el('button', { class: 'btn ghost', onclick: () => input.click() },
          k.loaded ? 'Replace file' : 'Choose file'),
        k.loaded
          ? el('button', {
            class: 'linkish',
            title: 'Delete the uploaded file and read this feed from the workbook again',
            onclick: () => clearUpload(k.kind),
          }, 'use workbook')
          : null));
  }));
}

async function uploadSheet(kind, file, input) {
  if (!file) return;
  toast(`Reading ${file.name}…`);
  let d;
  try {
    d = await api(`/api/feeds/upload/${kind}`, { method: 'POST', body: file,
      headers: {} });
  } finally {
    // Let the same file be picked again after a rejection, which the change
    // event would otherwise swallow as "no change".
    input.value = '';
  }
  const rows = d.feeds.reduce((n, f) => n + f.rows, 0);
  toast(`${file.name}: ${fmt(rows)} values into ${d.feeds.map((f) => f.feed).join(' + ')}`);
  d.notes.forEach((n) => console.info(`${kind}: ${n}`));
  await loadFeeds();
}

async function clearUpload(kind) {
  if (!confirm('Delete the uploaded file and read this feed from the planning '
    + 'workbook again? Your overrides are kept either way.')) return;
  await api(`/api/feeds/upload/${kind}`, { method: 'DELETE' });
  toast('Back on the workbook seed for this feed');
  await loadFeeds();
}

async function openFeed(feed) {
  state.feed = feed;
  await loadFeeds();
  await loadFeedRows();
  $('#feed-detail').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

async function loadFeedRows() {
  const feed = state.feed;
  const meta = state.feeds.find((f) => f.feed === feed);
  const search = $('#feed-search').value.trim();
  const only = $('#feed-filter').value;
  const d = await api(
    `/api/feeds/${feed}/rows?search=${encodeURIComponent(search)}&only=${only}&limit=800`);

  $('#feed-detail').hidden = false;
  $('#feed-title').textContent = meta.title;
  $('#feed-sub').textContent =
    `${fmt(d.total)} values · ${meta.unit} · source ${meta.source || 'n/a'}`;

  const rows = d.rows.map((r) => el('tr', {},
    el('td', { class: 'sticky' }, r.k1 + (r.label ? ` — ${r.label}` : '')),
    el('td', {}, r.k2 || ''),
    el('td', { class: 'num' }, fmt(r.source_value, 2)),
    el('td', { class: 'num' },
      el('input', {
        class: 'ovr-input' + (r.has_override ? ' set' : ''),
        type: 'number', step: 'any',
        value: r.override_value === null ? '' : r.override_value,
        placeholder: '—',
        onchange: (e) => saveOverride(r.id, e.target.value),
      })),
    el('td', { class: 'num' }, el('b', {}, fmt(r.effective_value, 2))),
    el('td', {},
      r.is_stale ? el('span', { class: 'pill warn', title: 'The source changed after this override was made' }, 'stale')
        : r.has_override ? el('span', { class: 'pill info' }, 'overridden') : ''),
    el('td', {}, r.override_by || ''),
    el('td', {}, r.has_override
      ? el('button', { class: 'linkish', onclick: () => saveOverride(r.id, '') }, 'clear')
      : ''),
  ));

  $('#feed-table').replaceChildren(table(
    [meta.key_labels[0], meta.key_labels[1] || '', `Source (${meta.unit})`,
     'Override', 'Effective', 'Status', 'By', ''],
    rows, [0]));
}

async function saveOverride(id, raw) {
  const value = raw === '' || raw === null ? null : parseFloat(raw);
  try {
    await api(`/api/staging/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ value, reason: 'edited in planner', actor: 'planner' }),
    });
  } catch (e) {
    await loadFeedRows();  // refused: put the stored value back (see saveOptParam)
    return;
  }
  toast(value === null ? 'Override cleared' : `Override saved (${fmt(value, 2)})`);
  await loadFeeds();
  await loadFeedRows();
}

/* --------------------------------------------------------- model inputs */
/* Yields, rates, capacities and control limits. Same source/override pattern as
 * the feeds: the imported value stays visible next to the edit. */
async function loadReference() {
  const d = await api('/api/reference');
  $('#ref-meta').textContent =
    `reference v${d.version} · ${d.source || ''} · ${d.edits} edit${d.edits === 1 ? '' : 's'}`;
  $('#ref-groups').replaceChildren(...d.groups.map((g) =>
    el('button', {
      class: 'feed-card' + (state.refKind === g.kind ? ' selected' : ''),
      onclick: () => openReference(g.kind),
    },
      el('h3', {}, g.title),
      el('p', { class: 'muted' }, g.description),
      el('div', { class: 'meta' },
        el('div', {}, el('b', {}, fmt(g.count)), 'values'),
        el('div', {}, el('b', {}, fmt(g.edited)), 'edited'),
        g.missing
          ? el('div', {}, el('b', { class: 'neg' }, fmt(g.missing)), 'not set')
          : null),
      el('p', { class: 'muted', style: 'margin-top:8px;font-size:11px' },
        g.unit)),
  ));
  if (state.refKind) await loadReferenceRows();
}

async function openReference(kind) {
  state.refKind = kind;
  await loadReference();
  await loadReferenceRows();
  $('#ref-detail').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

async function loadReferenceRows() {
  const kind = state.refKind;
  const search = $('#ref-search').value.trim();
  const only = $('#ref-edited-only').checked;
  const d = await api(`/api/reference/${kind}`
    + `?search=${encodeURIComponent(search)}&only_edited=${only}`);

  $('#ref-detail').hidden = false;
  $('#ref-title').textContent = d.title;
  $('#ref-sub').textContent =
    `${d.description} — ${fmt(d.total)} values in ${d.unit}, ${fmt(d.edited)} edited`;

  const dp = d.unit === 'fraction' ? 4 : 0;
  const rows = d.rows.map((r) => el('tr', {},
    el('td', { class: 'sticky' }, r.k1 + (r.label ? ` — ${r.label}` : '')),
    el('td', {}, r.k2 || ''),
    el('td', { class: 'num' },
      r.source_value === null ? el('span', { class: 'muted-inline' }, 'not set')
        : fmt(r.source_value, dp)),
    el('td', { class: 'num' },
      el('input', {
        class: 'ovr-input' + (r.edited ? ' set' : ''),
        type: 'number', step: 'any',
        value: r.override_value === null ? '' : r.override_value,
        placeholder: '—',
        title: 'Leave blank to use the imported value',
        onchange: (e) => saveReference(r.k1, r.k2, e.target.value),
      })),
    el('td', { class: 'num' }, el('b', {},
      r.effective_value === null ? '—' : fmt(r.effective_value, dp))),
    el('td', {}, r.edited
      ? el('span', { class: 'pill info', title: r.reason || '' }, 'edited')
      : ''),
    el('td', {}, r.override_by || ''),
    el('td', {}, r.edited
      ? el('button', {
          class: 'linkish', onclick: () => saveReference(r.k1, r.k2, ''),
        }, 'reset')
      : ''),
  ));

  $('#ref-table').replaceChildren(table(
    [d.keys[0] || 'Key', d.keys[1] || '', `Imported (${d.unit})`, 'Override',
     'In use', 'Status', 'By', ''],
    rows, [0]));
}

async function saveReference(k1, k2, raw) {
  const value = raw === '' || raw === null ? null : parseFloat(raw);
  try {
    await api(`/api/reference/${state.refKind}`, {
      method: 'PATCH',
      body: JSON.stringify({ k1, k2, value, reason: 'edited in planner' }),
    });
  } catch (e) {
    await loadReferenceRows();  // refused: put the stored value back (see saveOptParam)
    return;
  }
  toast(value === null
    ? 'Reset to the imported value — projections recalculated'
    : `Saved ${value} — projections recalculated`);
  await loadReference();
  await loadReferenceRows();
}

/* ---------------------------------------------------------- optimizer */
async function loadOptParams() {
  const d = await api('/api/optimizer/params');
  const p = d.params;

  $('#opt-ready').replaceChildren(
    stat(p.ready ? 'Ready' : p.missing.length, p.ready
      ? 'all inputs supplied' : 'input(s) still needed',
      p.ready ? 'ok' : 'danger'),
    stat(p.horizon_days, 'day detailed horizon', 'info'),
    stat(p.time_limit_seconds, 'second solve limit', 'info'),
  );

  // The two settings that are words, not numbers. Both have to be listed here
  // or they are unreachable: a number box turns "cost" into NaN the first time
  // it is touched, and a select falls back to its first option, so a value the
  // list omits reads back as whichever one happens to be first. v2 was saved in
  // the database and shown on screen as v1 for exactly that reason.
  const CHOICES = {
    model_version: {
      v0: 'v0 — keep my assignments',
      v1: 'v1 — decide MEK & extraction',
      v2: 'v2 — also decide the hydrotreater (~5 min)',
      greedy: 'greedy — rules, no solver (under a second)',
    },
    objective: {
      cost: 'cost — minimise what the plan gives up',
      margin: 'margin — maximise what it earns',
    },
    // Booleans belong here for the same reason the other two do: a number box
    // would turn the value into NaN, and the API reads the posted string.
    must_run: {
      true: 'on — every unit runs daily (the plant)',
      false: 'off — units may idle (cold start)',
    },
  };

  // Grouped, because these answer three different questions and a planner
  // filling one in is rarely touching the others.
  const GROUPS = [
    ['What things are worth', [
      'crude_price_per_bbl', 'lost_sale_margin_per_gal',
      'downgrade_discount_per_gal', 'netback_diesel_per_gal',
      'netback_gasoline_per_gal', 'switch_cost',
      'terminal_shortfall_per_gal']],
    ['How much freedom the model has', ['model_version', 'objective',
      'must_run', 'min_rate_fraction', 'charge_floor_fraction',
      'terminal_value_fraction', 'safety_stock_days', 'horizon_days']],
    ['Solver', ['time_limit_seconds', 'mip_gap_abs', 'mip_gap']],
  ];
  const byField = Object.fromEntries(d.schema.map((f) => [f.field, f]));

  const host = $('#opt-params');
  host.replaceChildren();
  for (const [title, fields] of GROUPS) {
    const rows = fields.filter((f) => byField[f]).map((field) => {
      const f = byField[field];
      const v = p[field];
      const needed = p.missing.includes(field);
      const blank = v === null || v === undefined;
      const choices = CHOICES[field];
      const input = choices
        ? el('select', {
            class: 'ovr-input set',
            onchange: (e) => saveOptParam(field, e.target.value, true),
          },
          Object.entries(choices).map(([opt, label]) => el('option',
            // String(v) so a boolean field matches its 'true'/'false' key; the
            // word-valued fields compare unchanged.
            Object.assign({ value: opt }, String(v) === opt ? { selected: true } : {}),
            label)))
        : el('input', {
            class: 'ovr-input' + (needed || blank ? '' : ' set'),
            type: 'number', step: 'any',
            value: blank ? '' : v,
            placeholder: needed ? 'required' : (blank ? 'default' : ''),
            onchange: (e) => saveOptParam(field, e.target.value),
          });
      return el('tr', {},
        el('td', { class: 'sticky' }, f.label),
        el('td', { class: 'num' }, input),
        el('td', {}, f.unit),
        el('td', {}, needed
          ? el('span', { class: 'pill danger' }, 'needed')
          // Empty and not required means the model falls back to its own
          // default. Marking that 'set' said someone had chosen a value.
          : blank && !choices
            ? el('span', {
                class: 'pill info',
                title: 'Left empty, so the model uses its default - see the help',
              }, 'default')
            : el('span', { class: 'pill ok' }, 'set')),
        el('td', { class: 'muted' }, f.help || ''),
      );
    });
    host.append(el('h3', { class: 'group-head' }, title));
    host.append(table(['Input', 'Value', 'Unit', 'Status', ''], rows, [0]));
  }
}

/* Every save that runs when a box loses focus redraws from the server if the
   save is refused. `api()` has already shown why; what must not happen is the
   refused value staying in the box, where it reads exactly like a saved one
   while the next run quietly uses the old value. */
async function saveOptParam(field, raw, isText) {
  const value = isText ? raw : (raw === '' ? null : parseFloat(raw));
  try {
    await api('/api/optimizer/params', {
      method: 'PATCH', body: JSON.stringify({ [field]: value }),
    });
    toast('Saved');
  } catch (e) {
    // refused - fall through and redraw what is stored
  }
  loadOptParams();
}

async function loadOptRuns() {
  // Numbered, because a list fetched mid-solve can land after the one fetched
  // once the solve ended, and would put the finished run back to 'solving'.
  const seq = ++state.runsSeq;
  const [runs, now] = await Promise.all([
    api('/api/optimizer/runs'), api('/api/optimizer/params')]);
  if (seq !== state.runsSeq) return;
  const live = runs.find((r) => r.in_progress) || null;
  if (live) state.running = live;
  else if (!state.runPending) state.running = null;
  refreshRunButton();
  // A run started in another tab, or before a reload, has no request here to
  // wait on - look again until the list says it has ended.
  clearTimeout(state.runsPoll);
  if (live && !state.runPending) state.runsPoll = setTimeout(loadOptRuns, 15000);
  const current = now.params;
  const host = $('#opt-runs');
  if (!runs.length) {
    host.replaceChildren(el('div', { class: 'empty' },
      'No runs yet. Set the inputs, then run the optimizer on the selected scenario.'));
    return;
  }
  host.replaceChildren();
  for (const r of runs) {
    if (r.in_progress) {
      host.append(el('div', { class: 'card run-card' },
        el('div', { class: 'run-head' },
          el('h3', {}, `Run #${r.id}`),
          el('span', { class: 'pill info' }, 'solving'),
          el('span', { class: 'pill info' }, r.model_version || 'v0'),
          el('span', { class: 'muted' }, `on ${r.base_scenario_name} · ${runClock(r)}`)),
        el('p', { class: 'run-message' },
          'Its answer lands here, and as a new scenario, when the solve ends.')));
      continue;
    }
    const k = r.kpis || {};
    const base = k.baseline || {};
    const opt = k.optimized || {};
    const v = k.verification || {};
    const shape = opt.campaign_shape || {};
    const pct = (b, o) => (b ? Math.round(100 * (o - b) / b) + '%' : '—');

    // A run the referee passed is usable whether or not the solver proved it
    // best. On a 4-6 month horizon the gap almost never closes in the time
    // budget, so 'not_proved_optimal' is the normal good outcome - it reads as
    // verified, with the unproved half said plainly rather than as a warning.
    const badge = r.status === 'done'
      ? el('span', { class: 'pill ok' }, 'verified')
      : r.status === 'not_proved_optimal'
        ? el('span', {
            class: 'pill ok',
            title: r.message || 'Feasible and verified; solver hit its time '
                 + 'limit before proving this is the best schedule.',
          }, 'verified · not proved optimal')
        : r.status === 'interrupted'
          ? el('span', { class: 'pill danger' }, 'interrupted')
          : el('span', {
              class: 'pill ' + (r.status === 'error' ? 'danger' : 'warn'),
              title: r.message || '',
            }, r.status);

    const head = el('div', { class: 'run-head' },
      el('h3', {}, `Run #${r.id}`),
      badge,
      el('span', { class: 'pill info' }, r.model_version || 'v0'),
      el('span', { class: 'muted' },
        `${new Date(r.created_at).toLocaleString()} · `
        + `${r.solve_seconds ? r.solve_seconds.toFixed(1) + 's' : '—'}`
        + (r.objective != null ? ` · $${fmt(r.objective)}` : '')),
      el('span', { class: 'muted' },
        `warm started from ${r.base_scenario_name || ('#' + r.base_scenario_id)}`),
    );

    // Both sides scored the same way - the comparison is meaningless otherwise.
    const cmp = table(
      ['', 'Your schedule', 'Optimizer', 'Change'],
      [
        ['Lost sales (gal)', base.lost_sales_gal, opt.lost_sales_gal],
        ['Downgrade (gal)', base.downgrade_gal, opt.downgrade_gal],
        ['Feed shortfall (gal)', base.feed_shortfall_gal,
          (v.actual || {}).feed_shortfall_gal],
      ].map(([label, b, o]) => el('tr', {},
        el('td', { class: 'sticky' }, label),
        el('td', { class: 'num' }, fmt(b)),
        el('td', { class: 'num' }, fmt(o)),
        el('td', { class: 'num ' + (o < b ? 'good' : (o > b ? 'bad' : '')) },
          pct(b, o)))),
      [0]);

    const checks = el('div', { class: 'stat-row' },
      stat(v.verified ? 'Yes' : 'No',
        'simulator reproduces the run', v.verified ? 'ok' : 'danger'),
      stat(v.executable ? 'Yes' : 'No',
        'schedule is executable', v.executable ? 'ok' : 'danger'),
      stat(v.fully_routed ? 'Yes' : 'No',
        'all overflow has an outlet', v.fully_routed ? 'ok' : 'warn'),
      stat(fmt(v.excluded_lost_sales_gal || 0), 'gal excluded from scoring', 'info'),
    );

    const campaigns = Object.keys(shape).length
      ? table(['Unit', 'Campaigns', 'Median days', 'Longest', 'Switches', 'Per month'],
          Object.entries(shape).map(([unit, c]) => el('tr', {},
            el('td', { class: 'sticky' }, unit),
            el('td', { class: 'num' }, c.campaigns),
            el('td', { class: 'num' }, c.median_campaign_days),
            el('td', { class: 'num' }, c.longest_campaign_days),
            el('td', { class: 'num' }, c.switches),
            el('td', { class: 'num' }, c.switches_per_month))), [0])
      : el('p', { class: 'muted' },
          'v0 keeps your assignments, so there are no campaigns to report.');

    // Why the run ended the way it did. This used to live only in the status
    // pill's tooltip, where "the schedule asks units to charge feed the tanks
    // cannot supply" was never read.
    const why = r.status === 'interrupted'
      ? 'The server stopped while this run was solving, so it never finished '
        + 'and nothing was saved. Start it again.'
      : r.message;
    const rejected = r.status === 'unverified';
    const whyLine = why ? el('p', {
      class: 'run-message' + (rejected || r.status === 'error'
        || r.status === 'interrupted' ? ' bad' : ''),
    }, why) : null;

    const used = r.settings || [];
    const changed = used.filter((s) => fmtSetting(s.value) !== fmtSetting(current[s.field]));
    const settings = used.length ? el('details', { class: 'run-settings' },
      el('summary', {}, 'Settings used'
        + (changed.length ? ` · ${changed.length} differ from the inputs now` : '')),
      table(['Input', 'This run', 'Now'], used.map((s) => el('tr', {},
        el('td', { class: 'sticky' }, s.label),
        el('td', {}, fmtSetting(s.value)),
        el('td', { class: changed.includes(s) ? 'changed' : '' },
          fmtSetting(current[s.field])))), [0])) : null;

    // A schedule the simulator rejected is kept to be inspected, never exported.
    const actions = el('div', { class: 'controls' },
      r.result_scenario_id ? el('button', {
        class: rejected ? 'btn ghost' : 'btn',
        title: rejected
          ? 'Open the schedule the simulator rejected, to see what went wrong'
          : 'Open the proposed schedule on the planning side',
        onclick: () => openResult(r.result_scenario_id),
      }, rejected ? 'Inspect the rejected schedule' : 'Open schedule') : null,
      r.result_scenario_id ? el('button', {
        class: 'linkish',
        title: 'Open it beside the schedule it warm started from',
        onclick: () => openResult(r.result_scenario_id, r.base_scenario_id),
      }, 'Compare with my schedule') : null,
      r.result_scenario_id && !rejected ? el('button', {
        class: 'linkish',
        title: 'Download it as blocks that paste into the workbook',
        onclick: () => {
          const days = (r.kpis && r.kpis.optimized && r.kpis.optimized.days) || 42;
          window.location = `/api/scenarios/${r.result_scenario_id}`
            + `/schedule.xlsx?days=${days}`;
        },
      }, 'Export for Excel') : null,
      rejected && r.result_scenario_id
        ? el('span', { class: 'muted' }, 'Not exportable: the simulator rejected it.')
        : null,
      v.note && v.note !== r.message ? el('span', { class: 'muted' }, v.note) : null,
    );

    host.append(el('div', { class: 'card run-card' },
      head, whyLine, cmp, checks,
      el('h4', { class: 'group-head' }, 'Campaign shape'), campaigns,
      settings, actions));
  }
}

async function runOptimizer() {
  if (!state.scenario) return;
  // A result scenario cannot be re-solved - see `refreshRunButton`. Caught here
  // rather than left to come back `no_solution` after a long solve, because the
  // failure looks like a bad model and is really a bad starting point.
  if (state.scenario.status === 'proposed') {
    // Peel every layer: chained runs nest their names, so one pass would point
    // at another result and send you round the loop again.
    let from = state.scenario.name;
    for (let prev = null; prev !== from; ) {
      prev = from;
      from = from.replace(/^Optimizer run \d+ \(from (.*)\)$/, '$1');
    }
    toast(`“${state.scenario.name}” is an optimizer result, so it cannot be `
      + `re-solved. Select “${from}” — the schedule it came from — and run that.`);
    return;
  }
  if (state.running) return;
  const base = state.scenario;
  // The request only answers when the solve is over, so the button shows the
  // run as solving now, from the inputs it is about to use. The server's own
  // row replaces this as soon as the runs list has it.
  const { params } = await api('/api/optimizer/params');
  state.running = {
    base_scenario_name: base.name, created_at: new Date().toISOString(),
    time_limit_seconds: params.time_limit_seconds, model_version: params.model_version,
  };
  state.runPending = true;
  refreshRunButton();
  const request = api('/api/optimizer/run', {
    method: 'POST',
    body: JSON.stringify({ scenario_id: base.id }),
  });
  // the run row is committed before the solve starts, so the list can show it
  setTimeout(() => { if (state.runPending) loadOptRuns(); }, 1500);
  let run = null;
  try {
    run = await request;
  } catch (e) {
    // api() has shown why - a refusal, or another run still solving
  }
  state.runPending = false;
  state.running = null;
  // Adds the new result to the picker without selecting it: the plan the run
  // started from stays selected, so the next run starts from it too.
  await loadScenarioList();
  await loadOptRuns();
  if (run && run.id) {
    toast(`Run #${run.id} finished: ${runOutcome(run.status)}. Its card is at the top.`);
  }
}

async function openResult(scenarioId, compareWith) {
  await loadScenarioList();
  $('#scenario-select').value = scenarioId;
  state.compare = compareWith != null;
  await selectScenario(scenarioId);
  setMode('planning');
  switchTab('schedule');
  toast(compareWith != null
    ? 'Opened beside the schedule it warm started from'
    : 'Opened — edit or compare it like any scenario');
}

/* ------------------------------------------------------------------- nav */
function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll('#mode-switch button').forEach((b) =>
    b.classList.toggle('active', b.dataset.mode === mode));
  document.querySelectorAll('.tabs button').forEach((b) => {
    b.hidden = b.dataset.mode !== mode;
  });
  const visible = [...document.querySelectorAll('.tabs button')]
    .filter((b) => !b.hidden);
  if (!visible.some((b) => b.classList.contains('active'))) {
    switchTab(visible[0].dataset.view);
  }
}

function switchTab(view) {
  document.querySelectorAll('.tabs button').forEach((b) =>
    b.classList.toggle('active', b.dataset.view === view));
  document.querySelectorAll('.view').forEach((v) =>
    v.classList.toggle('active', v.id === `view-${view}`));
  refreshActive();
}

/* A focused number input takes the scroll wheel as an increment, so scrolling
   the page past the box you just typed in edits it - and the edit saves when
   you click away. That turned a typed 0.30 into -36.7 (37 wheel clicks) and
   ruined a run in a way nothing on screen showed. The guard used to sit on the
   optimizer inputs alone, while the charge grid and both override tables are
   number inputs that save the same way. One listener covers every one of
   them, including rows drawn later: blur, so the wheel scrolls the page. */
document.addEventListener('wheel', (e) => {
  const t = e.target;
  if (t instanceof HTMLInputElement && t.type === 'number'
      && t === document.activeElement) {
    t.blur();
  }
}, { capture: true });

document.querySelectorAll('.tabs button').forEach((b) =>
  b.addEventListener('click', () => switchTab(b.dataset.view)));

$('#scenario-select').addEventListener('change', (e) => selectScenario(e.target.value));
$('#alert-window').addEventListener('change', loadAlerts);
$('#proj-days').addEventListener('change', loadProjection);
$('#product-select').addEventListener('change', (e) => {
  state.block = e.target.value;
  loadProjection();
});
$('#dash-days').addEventListener('change', loadDashboard);
$('#stream-select').addEventListener('change', loadStream);
$('#stream-days').addEventListener('change', loadStream);
$('#stream-offset').addEventListener('change', loadStream);
$('#stream-key-rows').addEventListener('change', loadStream);
$('#sched-days').addEventListener('change', loadSchedule);
$('#sched-offset').addEventListener('change', loadSchedule);
$('#fill-target').addEventListener('change', fillTargetChanged);
$('#fill-apply').addEventListener('click', applyFill);
$('#dt-add').addEventListener('click', addDowntime);
$('#ref-search').addEventListener('input', debounce(loadReferenceRows, 250));
$('#ref-edited-only').addEventListener('change', loadReferenceRows);
$('#opt-run').addEventListener('click', runOptimizer);
document.querySelectorAll('#mode-switch button').forEach((b) =>
  b.addEventListener('click', () => setMode(b.dataset.mode)));
setMode('planning');
$('#feed-search').addEventListener('input', debounce(loadFeedRows, 250));
$('#feed-filter').addEventListener('change', loadFeedRows);
$('#feed-sync').addEventListener('click', async () => {
  const r = await api(`/api/feeds/${state.feed}/sync`, { method: 'POST' });
  toast(`Synced ${r.rows} rows — overrides preserved`);
  await loadFeeds();
  await loadFeedRows();
});
$('#sync-all').addEventListener('click', async () => {
  const r = await api('/api/feeds/sync-all', { method: 'POST' });
  toast(`Re-synced ${r.batches.length} feeds — overrides preserved`);
  loadFeeds();
});
/* Both buttons that make a scenario run this, and that is the point.
 *
 * They used to differ: the header's asked only for a name and let the API fall
 * back to the date the reference document names - the workbook's own planning
 * date - while the Source data one asked for the day the uploaded inventory was
 * taken. The split was deliberate and it was wrong, because the failure it
 * produces is silent. Two scenarios were created from September uploads and
 * dated 2026-07-23: they opened with current tank levels, placed them seven
 * weeks in the past, and then ran seven weeks of demand and production that had
 * already happened. Nothing downstream can detect that, and there is no endpoint
 * to correct a scenario's `as_of` afterwards - the only repair is to make it
 * again.
 *
 * So there is one path now. Uploading is how data gets in, which makes "the day
 * the inventory was taken" the question worth asking every time. */
async function newScenario() {
  const today = new Date().toISOString().slice(0, 10);
  const name = prompt('Name for the new scenario', `Plan ${today}`);
  if (!name) return;
  const asOf = prompt('First day of the plan — the day the inventory you are '
    + 'planning from was taken', today);
  if (!asOf) return;
  const s = await api('/api/scenarios', {
    method: 'POST',
    body: JSON.stringify({
      name,
      as_of: asOf,
      copy_schedule_from: state.scenario ? state.scenario.id : null,
    }),
  });
  toast(`Scenario "${s.name}" created from the current source data`);
  await loadScenarioList();
  $('#scenario-select').value = s.id;
  await selectScenario(s.id);
}

$('#new-scenario').addEventListener('click', newScenario);
$('#upload-scenario').addEventListener('click', newScenario);

function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

boot().catch((e) => {
  $('#scenario-meta').textContent = 'Failed to load — is the API running?';
  console.error(e);
});
