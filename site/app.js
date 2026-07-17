/* Smoke and Strife live dashboard — vanilla JS + hand-rolled SVG. */
'use strict';

const NS = 'http://www.w3.org/2000/svg';
const $ = (id) => document.getElementById(id);
const tooltip = $('tooltip');

// ── theme toggle ──────────────────────────────────────────────────────────
$('themeBtn').addEventListener('click', () => {
  const root = document.documentElement;
  const dark = root.dataset.theme === 'dark' ||
    (!root.dataset.theme && matchMedia('(prefers-color-scheme: dark)').matches);
  root.dataset.theme = dark ? 'light' : 'dark';
});

// ── helpers ───────────────────────────────────────────────────────────────
function el(name, attrs = {}, parent = null) {
  const e = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}
function ymLabel(ym) {
  const y = Math.floor(ym / 100), m = ym % 100;
  return `${['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][m - 1]} ${y}`;
}
function ymIndex(yms) { // map ym ints to consecutive x positions
  const map = new Map(); yms.forEach((v, i) => map.set(v, i)); return map;
}
function fmt(x, d = 4) { return (x >= 0 ? '' : '−') + Math.abs(x).toFixed(d); }
function stars(p) { return p < 0.01 ? '***' : p < 0.05 ? '**' : p < 0.1 ? '*' : ''; }
function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function showTip(html, ev) {
  tooltip.innerHTML = html; tooltip.style.display = 'block';
  const pad = 14, w = tooltip.offsetWidth, h = tooltip.offsetHeight;
  let x = ev.clientX + pad, y = ev.clientY + pad;
  if (x + w > innerWidth - 8) x = ev.clientX - w - pad;
  if (y + h > innerHeight - 8) y = ev.clientY - h - pad;
  tooltip.style.left = x + 'px'; tooltip.style.top = y + 'px';
}
function hideTip() { tooltip.style.display = 'none'; }

function linScale(d0, d1, r0, r1) {
  const f = (v) => r0 + (v - d0) / (d1 - d0 || 1) * (r1 - r0);
  f.ticks = (n = 5) => {
    const span = d1 - d0 || 1, step = Math.pow(10, Math.floor(Math.log10(span / n)));
    const err = span / n / step;
    const mult = err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1;
    const s = mult * step, out = [];
    for (let v = Math.ceil(d0 / s) * s; v <= d1 + 1e-9; v += s) out.push(+v.toFixed(10));
    return out;
  };
  return f;
}

// ── generic line chart with crosshair tooltip ─────────────────────────────
function lineChart(container, yms, seriesList, opts = {}) {
  const W = 1060, H = opts.height || 240, M = { t: 12, r: 110, b: 26, l: 52 };
  const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img' }, container);
  const x = linScale(0, yms.length - 1, M.l, W - M.r);
  const extents = seriesList.flatMap(s =>
    s.values.filter(Number.isFinite).concat(s.band ? s.band.flat() : []));
  let lo = Math.min(0, ...extents);
  let hi = Math.max(0, ...extents);
  if (opts.lo !== undefined) lo = Math.min(lo, opts.lo);
  if (opts.hi !== undefined) hi = Math.max(hi, opts.hi);
  const pad = (hi - lo || 1) * 0.05;
  const y = linScale(lo - (lo < 0 ? pad : 0), hi + pad, H - M.b, M.t);

  const g = el('g', { class: 'axis' }, svg);
  for (const t of y.ticks(4)) {
    el('line', { x1: M.l, x2: W - M.r, y1: y(t), y2: y(t), class: 'gridline' }, g);
    el('text', { x: M.l - 8, y: y(t) + 4, 'text-anchor': 'end' }, g).textContent =
      Math.abs(t) >= 1000 ? (t / 1000) + 'k' : t;
  }
  el('line', { x1: M.l, x2: W - M.r, y1: y(0), y2: y(0), class: 'baseline' }, svg);
  const step = Math.max(1, Math.ceil(yms.length / 9));
  for (let i = 0; i < yms.length; i += step)
    el('text', { x: x(i), y: H - 8, 'text-anchor': 'middle', class: 'axis' }, g)
      .textContent = ymLabel(yms[i]);

  for (const s of seriesList) {
    if (s.band) { // CI band
      const up = s.band.map((b, i) => `${x(i)},${y(b[1])}`);
      const dn = s.band.map((b, i) => `${x(i)},${y(b[0])}`).reverse();
      el('polygon', { points: up.concat(dn).join(' '), fill: s.color, opacity: 0.14 }, svg);
    }
    const pts = s.values.map((v, i) => Number.isFinite(v) ? `${x(i)},${y(v)}` : null)
      .filter(Boolean).join(' ');
    el('polyline', { points: pts, fill: 'none', stroke: s.color, 'stroke-width': 2,
      'stroke-linejoin': 'round', 'stroke-linecap': 'round' }, svg);
    const li = s.values.length - 1;
    el('text', { x: x(li) + 8, y: y(s.values[li]) + 4, class: 'dlabel' }, svg)
      .textContent = s.label; // direct label at line end (relief for low-contrast hues)
  }
  if (opts.zeroDash) el('line', { x1: M.l, x2: W - M.r, y1: y(0), y2: y(0), class: 'zline' }, svg);

  // crosshair + tooltip
  const cross = el('line', { y1: M.t, y2: H - M.b, stroke: css('--muted'),
    'stroke-width': 1, opacity: 0 }, svg);
  const dots = seriesList.map(s => el('circle', { r: 3.5, fill: s.color, opacity: 0 }, svg));
  svg.addEventListener('mousemove', (ev) => {
    const r = svg.getBoundingClientRect();
    const px = (ev.clientX - r.left) / r.width * W;
    const i = Math.max(0, Math.min(yms.length - 1,
      Math.round((px - M.l) / ((W - M.r - M.l) / (yms.length - 1)))));
    cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i));
    cross.setAttribute('opacity', 0.5);
    let html = `<b>${ymLabel(yms[i])}</b>`;
    seriesList.forEach((s, k) => {
      dots[k].setAttribute('cx', x(i)); dots[k].setAttribute('cy', y(s.values[i]));
      dots[k].setAttribute('opacity', 1);
      html += `<br><span class="sw" style="background:${s.color}"></span>${s.label}: <b>${
        opts.fmt ? opts.fmt(s.values[i]) : s.values[i]}</b>`;
      if (s.band) html += ` <span style="opacity:.7">[${fmt(s.band[i][0], 3)}, ${fmt(s.band[i][1], 3)}]</span>`;
    });
    showTip(html, ev);
  });
  svg.addEventListener('mouseleave', () => {
    cross.setAttribute('opacity', 0); dots.forEach(d => d.setAttribute('opacity', 0)); hideTip();
  });
  return svg;
}

// ── coefficient dot-and-CI plot ───────────────────────────────────────────
function coefPlot(container, rows) {
  // rows: {label, coef, se, p, group?}
  const W = 1060, rowH = 34, M = { t: 10, r: 40, b: 30, l: 250 };
  const H = M.t + rows.length * rowH + M.b;
  const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img' }, container);
  const lo = Math.min(0, ...rows.map(r => r.coef - 1.96 * r.se)) * 1.15;
  const hi = Math.max(0.001, ...rows.map(r => r.coef + 1.96 * r.se)) * 1.15;
  const x = linScale(lo, hi, M.l, W - M.r);
  const g = el('g', { class: 'axis' }, svg);
  for (const t of x.ticks(6)) {
    el('line', { x1: x(t), x2: x(t), y1: M.t, y2: H - M.b, class: 'gridline' }, g);
    el('text', { x: x(t), y: H - 10, 'text-anchor': 'middle' }, g).textContent = fmt(t, 2);
  }
  el('line', { x1: x(0), x2: x(0), y1: M.t, y2: H - M.b, class: 'zline' }, svg);
  rows.forEach((r, i) => {
    const cy = M.t + i * rowH + rowH / 2;
    const sig = r.p < 0.05;
    const color = sig ? css('--series-1') : css('--muted');
    el('text', { x: M.l - 12, y: cy + 4, 'text-anchor': 'end', class: 'dlabel' }, svg)
      .textContent = r.label;
    el('line', { x1: x(r.coef - 1.96 * r.se), x2: x(r.coef + 1.96 * r.se),
      y1: cy, y2: cy, stroke: color, 'stroke-width': 2, 'stroke-linecap': 'round' }, svg);
    el('circle', { cx: x(r.coef), cy, r: 5, fill: color,
      stroke: css('--surface-1'), 'stroke-width': 2 }, svg);
    el('text', { x: x(r.coef + 1.96 * r.se) + 8, y: cy + 4, class: 'dlabel',
      style: `fill:${sig ? css('--series-1') : css('--muted')}` }, svg)
      .textContent = fmt(r.coef, 3) + stars(r.p);
    const hit = el('rect', { x: 0, y: cy - rowH / 2, width: W, height: rowH,
      fill: 'transparent' }, svg);
    hit.addEventListener('mousemove', (ev) => showTip(
      `<b>${r.label}</b><br>coef ${fmt(r.coef)} &nbsp; se ${r.se.toFixed(4)}<br>` +
      `p = ${r.p < 0.001 ? '&lt;0.001' : r.p.toFixed(3)}${r.extra || ''}`, ev));
    hit.addEventListener('mouseleave', hideTip);
  });
  return svg;
}

// ── choropleth ────────────────────────────────────────────────────────────
const SEQ = ['--seq-100', '--seq-200', '--seq-300', '--seq-400', '--seq-500', '--seq-600', '--seq-700'];
let geo = null, latest = null, mapVar = 'total_frp';

function drawMap() {
  if (!geo || !latest) return;
  const container = $('map'); container.innerHTML = '';
  const lookup = new Map(latest.districts.map(d => [d.district, d]));
  const vals = latest.districts.map(d => d[mapVar]).filter(v => v > 0);
  const vmax = Math.max(1, ...vals);
  // log bins into the 7-step sequential ramp; zero stays near-surface
  const bin = (v) => v <= 0 ? -1 :
    Math.min(6, Math.floor(Math.log1p(v) / Math.log1p(vmax) * 7));

  const LON0 = 94.5, LON1 = 141.5, LAT0 = 6.5, LAT1 = -11.5;
  const W = 1060, H = W * (LAT0 - LAT1) / (LON1 - LON0);
  const px = (lon) => (lon - LON0) / (LON1 - LON0) * W;
  const py = (lat) => (LAT0 - lat) / (LAT0 - LAT1) * H;
  const svg = el('svg', { viewBox: `0 0 ${W} ${H.toFixed(0)}`, role: 'img' }, container);

  for (const f of geo.features) {
    const name = f.properties.district;
    const rec = lookup.get(name);
    const v = rec ? rec[mapVar] : null;
    const b = v === null ? -1 : bin(v);
    const fill = b < 0 ? css('--grid') : css(SEQ[b]);
    const polys = f.geometry.type === 'Polygon' ? [f.geometry.coordinates] : f.geometry.coordinates;
    let d = '';
    for (const poly of polys) for (const ring of poly) {
      d += 'M' + ring.map(c => `${px(c[0]).toFixed(1)},${py(c[1]).toFixed(1)}`).join('L') + 'Z';
    }
    const path = el('path', { d, fill, class: 'district' }, svg);
    path.addEventListener('mousemove', (ev) => showTip(
      `<b>${name}</b>${f.properties.province ? ' · ' + f.properties.province : ''}<br>` +
      (rec ? `fire FRP: <b>${rec.total_frp.toLocaleString()}</b> MW<br>` +
             `events: <b>${rec.events}</b> · political violence: <b>${rec.pv_events}</b>`
           : 'not in the conflict-matched panel'), ev));
    path.addEventListener('mouseleave', hideTip);
  }
  const ramp = $('mapRamp'); ramp.innerHTML = '';
  SEQ.forEach(s => { const i = document.createElement('i'); i.style.background = css(s); ramp.appendChild(i); });
  $('mapHi').textContent = 'high (log scale)';
}

document.querySelectorAll('.maprow button').forEach(b =>
  b.addEventListener('click', () => {
    document.querySelectorAll('.maprow button').forEach(x => x.setAttribute('aria-pressed', 'false'));
    b.setAttribute('aria-pressed', 'true'); mapVar = b.dataset.var; drawMap();
  }));

// ── page assembly ─────────────────────────────────────────────────────────
function tile(k, v, d) {
  return `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div><div class="d">${d}</div></div>`;
}

async function main() {
  let R;
  try {
    R = await (await fetch('data/results.json')).json();
  } catch (e) {
    document.querySelector('.wrap').insertAdjacentHTML('beforeend',
      '<div class="err">Could not load <code>data/results.json</code>. Run the pipeline first: <code>python -m pipeline.run_update</code></div>');
    return;
  }
  const M = R.meta, V = M.vintages;

  // vintage chips
  const prelim = M.preliminary_months.length;
  $('vintages').innerHTML = [
    `<span class="chip">Panel <b>${ymLabel(M.panel_start)} – ${ymLabel(M.panel_end)}</b></span>`,
    `<span class="chip">ACLED through <b>${ymLabel(V.acled_through)}</b></span>`,
    `<span class="chip${prelim ? ' prelim' : ''}">Fire final through <b>${ymLabel(V.fire_final_through)}</b>${
      prelim ? ` · <b>${prelim} preliminary month${prelim > 1 ? 's' : ''}</b> (NRT)` : ''}</span>`,
    `<span class="chip">ERA5 wind through <b>${ymLabel(V.wind_through)}</b></span>`,
    `<span class="chip">Updated <b>${new Date(M.generated_at).toISOString().slice(0, 10)}</b></span>`,
  ].join('');

  // tiles
  const t30 = R.thresholds.find(t => Math.abs(t.threshold - 0.30) < 1e-9);
  const full = R.full_iv.find(s => s.label === 'IV-1');
  $('tiles').innerHTML =
    tile('First-stage F', Math.round(R.first_stage.f_stat).toLocaleString(),
      `upwind FRP → local FRP · instrument is ${R.first_stage.f_stat > 10 ? 'strong' : 'WEAK'}`) +
    tile('Full-panel IV (all events)', `${fmt(full.coef, 4)}<span class="stars">${stars(full.p)}</span>`,
      `p = ${full.p.toFixed(2)} · the population-average null`) +
    tile('Conflict-active districts (τ≥30%)', `${fmt(t30.events.coef, 3)}<span class="stars">${stars(t30.events.p)}</span>`,
      `events per log-point FRP · p = ${t30.events.p.toFixed(3)} · ${t30.n_districts} districts`) +
    tile('Panel', `${M.n_obs.toLocaleString()}`,
      `district-months · ${M.n_districts} districts · ${(M.zero_share_events * 100).toFixed(1)}% zero-event`);

  // threshold coefficient plot + table
  const threshRows = [];
  for (const t of R.thresholds) {
    threshRows.push({ label: `τ ≥ ${(t.threshold * 100).toFixed(0)}% — all events`,
      ...t.events, extra: `<br>${t.n_districts} districts · ${t.n_obs.toLocaleString()} obs · first-stage F ${Math.round(t.first_stage_F)}` });
    threshRows.push({ label: `τ ≥ ${(t.threshold * 100).toFixed(0)}% — political violence`,
      ...t.pv_events, extra: `<br>${t.n_districts} districts · ${t.n_obs.toLocaleString()} obs` });
  }
  coefPlot($('threshChart'), threshRows);
  $('threshTable').innerHTML = '<table><tr><th>Threshold</th><th>Districts</th><th>Obs</th><th>Zero share</th><th>F</th><th>β events (p)</th><th>β pv (p)</th></tr>' +
    R.thresholds.map(t => `<tr><td>≥ ${(t.threshold * 100).toFixed(0)}%</td><td>${t.n_districts}</td><td>${t.n_obs.toLocaleString()}</td><td>${(t.zero_share * 100).toFixed(1)}%</td><td>${Math.round(t.first_stage_F)}</td>` +
      `<td class="${t.events.coef < 0 && t.events.p < .05 ? 'neg' : ''}">${fmt(t.events.coef)}${stars(t.events.p)} (${t.events.p.toFixed(3)})</td>` +
      `<td class="${t.pv_events.coef < 0 && t.pv_events.p < .05 ? 'neg' : ''}">${fmt(t.pv_events.coef)}${stars(t.pv_events.p)} (${t.pv_events.p.toFixed(3)})</td></tr>`).join('') + '</table>';

  // event types
  const et = R.event_types;
  const evRows = Object.values(et.fourway).map(r => ({ ...r, label: r.label }))
    .concat(Object.values(et.twoway).map(r => ({ ...r, label: r.label + ' (composite)' })));
  coefPlot($('eventChart'), evRows);
  $('eventTable').innerHTML = '<table><tr><th>Outcome</th><th>Coefficient</th><th>SE</th><th>p</th></tr>' +
    evRows.map(r => `<tr><td>${r.label}</td><td class="${r.coef < 0 && r.p < .05 ? 'neg' : ''}">${fmt(r.coef)}${stars(r.p)}</td><td>${r.se.toFixed(4)}</td><td>${r.p.toFixed(3)}</td></tr>`).join('') + '</table>';

  // expanding window
  const ex = R.expanding;
  if (ex.length > 1) {
    const yms = ex.map(d => d.end_ym);
    const c1 = css('--series-1'), c2 = css('--series-2');
    $('expLegend').innerHTML =
      `<span><span class="sw" style="background:${c1}"></span>All events</span>` +
      `<span><span class="sw" style="background:${c2}"></span>Political violence</span>`;
    lineChart($('expChart'), yms, [
      { label: 'all events', color: c1, values: ex.map(d => d.events.coef),
        band: ex.map(d => [d.events.coef - 1.96 * d.events.se, d.events.coef + 1.96 * d.events.se]) },
      { label: 'political violence', color: c2, values: ex.map(d => d.pv_events.coef),
        band: ex.map(d => [d.pv_events.coef - 1.96 * d.pv_events.se, d.pv_events.coef + 1.96 * d.pv_events.se]) },
    ], { height: 260, zeroDash: true, fmt: (v) => fmt(v, 4) });
  }

  // national series (two panels, one axis each)
  const ns = R.national_series;
  const yms = ns.map(d => d.year_month);
  const c1 = css('--series-1'), c2 = css('--series-2'), c3 = css('--series-3');
  $('tsLegend').innerHTML =
    `<span><span class="sw" style="background:${c1}"></span>All conflict events</span>` +
    `<span><span class="sw" style="background:${c2}"></span>Political violence</span>` +
    `<span><span class="sw" style="background:${c3}"></span>Fire radiative power (MW, panel total)</span>`;
  lineChart($('tsChart'), yms, [
    { label: 'events', color: c1, values: ns.map(d => d.events) },
    { label: 'political violence', color: c2, values: ns.map(d => d.pv_events) },
  ], { height: 220, fmt: (v) => v.toLocaleString() });
  lineChart($('frpChart'), yms, [
    { label: 'total FRP (MW)', color: c3, values: ns.map(d => d.total_frp) },
  ], { height: 160, fmt: (v) => Math.round(v).toLocaleString() + ' MW' });
  $('tsTable').innerHTML = '<table><tr><th>Month</th><th>Events</th><th>Political violence</th><th>Riots</th><th>Protests</th><th>Total FRP (MW)</th></tr>' +
    ns.slice(-24).map(d => `<tr><td>${ymLabel(d.year_month)}</td><td>${d.events}</td><td>${d.pv_events}</td><td>${d.riots}</td><td>${d.protests}</td><td>${Math.round(d.total_frp).toLocaleString()}</td></tr>`).join('') +
    '</table><p style="color:var(--muted);font-size:12px">Last 24 months shown.</p>';

  // map
  latest = R.district_latest;
  document.querySelector('#map').insertAdjacentHTML('beforebegin', '');
  try {
    geo = await (await fetch('data/districts.geojson')).json();
    drawMap();
  } catch (e) {
    $('map').innerHTML = '<p class="note">District boundaries file missing (data/districts.geojson).</p>';
  }

  $('foot').innerHTML =
    `Model: y<sub>dt</sub> = α<sub>d</sub> + γ<sub>t</sub> + β·logFRP&#770;<sub>dt</sub> + β₁·logFRP<sub>d,t−1</sub> + ε<sub>dt</sub>, ` +
    `with logFRP instrumented by upwind fire radiative power (300 km radius, ±45° cone around the district's monthly wind direction). ` +
    `Standard errors clustered by province. Estimates on conflict-active subsamples are local effects for districts where conflict recurs, ` +
    `not population averages; months flagged preliminary use near-real-time fire detections that have not yet passed science-quality processing. ` +
    `Conflict data © <a href="https://acleddata.com">ACLED</a>, used under its terms (aggregated counts only). ` +
    `Fire detections: NASA FIRMS VIIRS S-NPP. Winds: Copernicus ERA5. ` +
    `Code: <a href="https://github.com/Hariaksha/wlidfire-conflict">github.com/Hariaksha/wlidfire-conflict</a>. ` +
    `Paper: <a href="https://github.com/Hariaksha/wlidfire-conflict/blob/main/paper/draft.pdf">draft.pdf</a>.`;

  // redraw svg colors on theme flip
  new MutationObserver(() => { $('threshChart').innerHTML = ''; $('eventChart').innerHTML = '';
    $('expChart').innerHTML = ''; $('tsChart').innerHTML = ''; $('frpChart').innerHTML = '';
    mainRender(R); })
    .observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
}

// re-render charts only (colors are resolved at draw time)
function mainRender(R) {
  const threshRows = [];
  for (const t of R.thresholds) {
    threshRows.push({ label: `τ ≥ ${(t.threshold * 100).toFixed(0)}% — all events`, ...t.events });
    threshRows.push({ label: `τ ≥ ${(t.threshold * 100).toFixed(0)}% — political violence`, ...t.pv_events });
  }
  coefPlot($('threshChart'), threshRows);
  const et = R.event_types;
  coefPlot($('eventChart'), Object.values(et.fourway)
    .concat(Object.values(et.twoway).map(r => ({ ...r, label: r.label + ' (composite)' }))));
  const ex = R.expanding;
  if (ex.length > 1) {
    lineChart($('expChart'), ex.map(d => d.end_ym), [
      { label: 'all events', color: css('--series-1'), values: ex.map(d => d.events.coef),
        band: ex.map(d => [d.events.coef - 1.96 * d.events.se, d.events.coef + 1.96 * d.events.se]) },
      { label: 'political violence', color: css('--series-2'), values: ex.map(d => d.pv_events.coef),
        band: ex.map(d => [d.pv_events.coef - 1.96 * d.pv_events.se, d.pv_events.coef + 1.96 * d.pv_events.se]) },
    ], { height: 260, zeroDash: true, fmt: (v) => fmt(v, 4) });
  }
  const ns = R.national_series, yms = ns.map(d => d.year_month);
  lineChart($('tsChart'), yms, [
    { label: 'events', color: css('--series-1'), values: ns.map(d => d.events) },
    { label: 'political violence', color: css('--series-2'), values: ns.map(d => d.pv_events) },
  ], { height: 220, fmt: (v) => v.toLocaleString() });
  lineChart($('frpChart'), yms, [
    { label: 'total FRP (MW)', color: css('--series-3'), values: ns.map(d => d.total_frp) },
  ], { height: 160, fmt: (v) => Math.round(v).toLocaleString() + ' MW' });
  drawMap();
}

main();
