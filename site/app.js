/* Smoke and Strife live dashboard — vanilla JS + hand-rolled SVG. */
'use strict';

const NS = 'http://www.w3.org/2000/svg';
const $ = (id) => document.getElementById(id);
const tooltip = $('tooltip');
const reduceMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
if (typeof gsap !== 'undefined' && typeof ScrollTrigger !== 'undefined') gsap.registerPlugin(ScrollTrigger);

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

// ── plain-language translation of a threshold row's coefficients ──────────
// "One log-point" is a fixed multiplier (e ≈ 2.72x), not data-dependent.
// The percent-of-outcome-mean conversion (coef / mean * 100) IS data-
// dependent and must be computed live from whatever results.json currently
// holds, not hardcoded to one week's numbers.
const LOG_PT_MULT = Math.E;
function pctOfMean(coef, mean) { return mean > 0 ? (coef / mean) * 100 : null; }
function plainClause(coef, p, mean, noun) {
  const pct = pctOfMean(coef, mean);
  if (p >= 0.10 || pct === null)
    return `<span class="ns">no statistically significant change in ${noun} detected</span>`;
  const dir = coef < 0 ? 'fewer' : 'more';
  return `~<b>${Math.abs(pct).toFixed(0)}% ${dir} ${noun}</b> (p=${p.toFixed(3)})`;
}
function plainLanguageHTML(thresholds) {
  // Headline on the highest threshold with *any* significant result, since
  // that's what the tiles above already feature; if nothing is significant
  // anywhere, say so plainly rather than picking a number to feature.
  const sig = thresholds.filter(t => t.events.p < 0.10 || t.pv_events.p < 0.10);
  const t = sig.length ? sig[sig.length - 1] : thresholds[thresholds.length - 1];
  const mult = LOG_PT_MULT.toFixed(1);
  const pctJump = ((LOG_PT_MULT - 1) * 100).toFixed(0);
  if (!sig.length) {
    return `<div class="label">In plain terms</div>` +
      `<p>In the ${t.n_districts} districts where conflict recurs most often (≥${(t.threshold*100).toFixed(0)}% of months), ` +
      `a roughly ${pctJump}% jump in local fire intensity (fire intensity ×${mult}) shows ` +
      `<span class="ns">no statistically significant effect on conflict this week</span> — the population-average finding is a precise null.</p>`;
  }
  return `<div class="label">In plain terms</div>` +
    `<p>In the ${t.n_districts} districts where conflict recurs most often (≥${(t.threshold*100).toFixed(0)}% of months), ` +
    `a roughly ${pctJump}% jump in local fire intensity (a "one-log-point" increase — fire intensity ×${mult}) is associated with ` +
    `${plainClause(t.events.coef, t.events.p, t.mean_events, 'conflict events')} and ` +
    `${plainClause(t.pv_events.coef, t.pv_events.p, t.mean_pv_events, 'political-violence events')}.</p>` +
    `<p style="margin-top:6px">This is a <b>local effect for conflict-prone districts</b>, not a national average — ` +
    `across all districts the population-average effect is a precise null (see the "Full-panel IV" tile above).</p>`;
}
function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
let tooltipVisible = false;
function showTip(html, ev) {
  tooltip.innerHTML = html;
  const wasHidden = !tooltipVisible;
  if (wasHidden) tooltip.style.display = 'block'; // needed once, before measuring below
  const pad = 14, w = tooltip.offsetWidth, h = tooltip.offsetHeight;
  let x = ev.clientX + pad, y = ev.clientY + pad;
  if (x + w > innerWidth - 8) x = ev.clientX - w - pad;
  if (y + h > innerHeight - 8) y = ev.clientY - h - pad;
  tooltip.style.left = x + 'px'; tooltip.style.top = y + 'px';
  if (!wasHidden) return; // already showing (just repositioning) - don't re-pop every mousemove
  tooltipVisible = true;
  if (typeof gsap === 'undefined' || reduceMotion()) { tooltip.style.opacity = '1'; return; }
  gsap.fromTo(tooltip, { autoAlpha: 0, scale: 0.92 },
    { autoAlpha: 1, scale: 1, duration: 0.1, ease: 'power1.out', overwrite: true });
}
function hideTip() {
  tooltipVisible = false;
  if (typeof gsap === 'undefined' || reduceMotion()) { tooltip.style.display = 'none'; return; }
  gsap.to(tooltip, { autoAlpha: 0, scale: 0.92, duration: 0.1, ease: 'power1.in', overwrite: true });
}

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

  const lines = [];
  for (const s of seriesList) {
    let band = null;
    if (s.band) { // CI band
      const up = s.band.map((b, i) => `${x(i)},${y(b[1])}`);
      const dn = s.band.map((b, i) => `${x(i)},${y(b[0])}`).reverse();
      band = el('polygon', { points: up.concat(dn).join(' '), fill: s.color, opacity: 0.14 }, svg);
    }
    const pts = s.values.map((v, i) => Number.isFinite(v) ? `${x(i)},${y(v)}` : null)
      .filter(Boolean).join(' ');
    const poly = el('polyline', { points: pts, fill: 'none', stroke: s.color, 'stroke-width': 2,
      'stroke-linejoin': 'round', 'stroke-linecap': 'round' }, svg);
    const li = s.values.length - 1;
    const label = el('text', { x: x(li) + 8, y: y(s.values[li]) + 4, class: 'dlabel' }, svg);
    label.textContent = s.label; // direct label at line end (relief for low-contrast hues)
    lines.push({ poly, label, band });
  }
  if (opts.zeroDash) el('line', { x1: M.l, x2: W - M.r, y1: y(0), y2: y(0), class: 'zline' }, svg);

  // Draw-in reveal: each line "draws" left to right via the classic
  // stroke-dasharray/dashoffset trick (no paid DrawSVG plugin needed);
  // end labels and the CI band fade in once their line arrives.
  if (typeof gsap !== 'undefined' && !reduceMotion()) {
    lines.forEach(({ poly, label, band }) => {
      const len = poly.getTotalLength();
      gsap.set(poly, { strokeDasharray: len, strokeDashoffset: len });
      gsap.set(label, { autoAlpha: 0 });
      if (band) gsap.set(band, { autoAlpha: 0 });
      const tl = gsap.timeline({ defaults: { ease: 'power2.inOut' } });
      tl.to(poly, { strokeDashoffset: 0, duration: 1.1 });
      if (band) tl.to(band, { autoAlpha: 1, duration: 0.8 }, 0);
      tl.to(label, { autoAlpha: 1, duration: 0.3 }, '-=0.1');
    });
  }

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
  const zx = x(0);
  const animRows = [];
  rows.forEach((r, i) => {
    const cy = M.t + i * rowH + rowH / 2;
    const sig = r.p < 0.05;
    const color = sig ? css('--series-1') : css('--muted');
    el('text', { x: M.l - 12, y: cy + 4, 'text-anchor': 'end', class: 'dlabel' }, svg)
      .textContent = r.label;
    // Whisker/dot/label start collapsed at the zero baseline and animate out
    // to their true position - visually reinforces "does this differ from
    // zero", not just decorative motion.
    const whisker = el('line', { x1: zx, x2: zx,
      y1: cy, y2: cy, stroke: color, 'stroke-width': 2, 'stroke-linecap': 'round' }, svg);
    const dot = el('circle', { cx: zx, cy, r: 5, fill: color,
      stroke: css('--surface-1'), 'stroke-width': 2 }, svg);
    const label = el('text', { x: zx + 8, y: cy + 4, class: 'dlabel', opacity: 0,
      style: `fill:${sig ? css('--series-1') : css('--muted')}` }, svg);
    label.textContent = fmt(r.coef, 3) + stars(r.p);
    const hit = el('rect', { x: 0, y: cy - rowH / 2, width: W, height: rowH,
      fill: 'transparent' }, svg);
    hit.addEventListener('mousemove', (ev) => showTip(
      `<b>${r.label}</b><br>coef ${fmt(r.coef)} &nbsp; se ${r.se.toFixed(4)}<br>` +
      `p = ${r.p < 0.001 ? '&lt;0.001' : r.p.toFixed(3)}${r.extra || ''}`, ev));
    hit.addEventListener('mouseleave', hideTip);
    animRows.push({ whisker, dot, label,
      x1: x(r.coef - 1.96 * r.se), x2: x(r.coef + 1.96 * r.se), cx: x(r.coef),
      labelX: x(r.coef + 1.96 * r.se) + 8 });
  });

  if (typeof gsap === 'undefined') {
    // GSAP failed to load (e.g. CDN blocked) - set final positions directly
    // so the chart is still fully correct without it.
    animRows.forEach(t => {
      t.whisker.setAttribute('x1', t.x1); t.whisker.setAttribute('x2', t.x2);
      t.dot.setAttribute('cx', t.cx);
      t.label.setAttribute('x', t.labelX); t.label.setAttribute('opacity', 1);
    });
  } else if (reduceMotion()) {
    animRows.forEach(t => {
      gsap.set(t.whisker, { attr: { x1: t.x1, x2: t.x2 } });
      gsap.set(t.dot, { attr: { cx: t.cx } });
      gsap.set(t.label, { attr: { x: t.labelX }, opacity: 1 });
    });
  } else {
    const tl = gsap.timeline({ defaults: { duration: 0.5, ease: 'power3.out' } });
    animRows.forEach((t, i) => {
      tl.to(t.whisker, { attr: { x1: t.x1, x2: t.x2 } }, i * 0.035)
        .to(t.dot, { attr: { cx: t.cx } }, i * 0.035)
        .to(t.label, { attr: { x: t.labelX }, opacity: 1, duration: 0.35 }, i * 0.035 + 0.15);
    });
  }
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

  // bounding box computed from the loaded geojson itself (not hardcoded),
  // since Indonesia and Nigeria cover entirely different extents
  let LON0 = Infinity, LON1 = -Infinity, LAT0 = -Infinity, LAT1 = Infinity;
  for (const f of geo.features) {
    const rings = f.geometry.type === 'Polygon' ? [f.geometry.coordinates] : f.geometry.coordinates;
    for (const poly of rings) for (const ring of poly) for (const [lon, lat] of ring) {
      if (lon < LON0) LON0 = lon; if (lon > LON1) LON1 = lon;
      if (lat > LAT0) LAT0 = lat; if (lat < LAT1) LAT1 = lat;
    }
  }
  const padLon = (LON1 - LON0) * 0.03, padLat = (LAT0 - LAT1) * 0.03;
  LON0 -= padLon; LON1 += padLon; LAT0 += padLat; LAT1 -= padLat;
  const W = 1060, H = W * (LAT0 - LAT1) / (LON1 - LON0);
  const px = (lon) => (lon - LON0) / (LON1 - LON0) * W;
  const py = (lat) => (LAT0 - lat) / (LAT0 - LAT1) * H;
  const svg = el('svg', { viewBox: `0 0 ${W} ${H.toFixed(0)}`, role: 'img' }, container);

  const paths = [];
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
    paths.push(path);
  }
  const ramp = $('mapRamp'); ramp.innerHTML = '';
  SEQ.forEach(s => { const i = document.createElement('i'); i.style.background = css(s); ramp.appendChild(i); });
  $('mapHi').textContent = 'high (log scale)';

  // Staggered fade-in instead of every polygon popping in at once - total
  // duration is fixed (amount, not each) so this reads the same whether
  // there are 447 districts (Indonesia) or 775 LGAs (Nigeria).
  if (typeof gsap !== 'undefined' && !reduceMotion()) {
    gsap.from(paths, { autoAlpha: 0, duration: 0.5, ease: 'power1.out',
      stagger: { amount: 0.9, from: 'random' } });
  }
}

document.querySelectorAll('.maprow button').forEach(b =>
  b.addEventListener('click', () => {
    document.querySelectorAll('.maprow button').forEach(x => x.setAttribute('aria-pressed', 'false'));
    b.setAttribute('aria-pressed', 'true'); mapVar = b.dataset.var; drawMap();
  }));

// ── page assembly ─────────────────────────────────────────────────────────
function fmtTileNum(v, decimals) {
  return decimals > 0 ? fmt(v, decimals) : Math.round(v).toLocaleString();
}

// anim: { value, decimals, suffix } - suffix is static HTML (e.g. significance
// stars) appended after the number, not itself part of the count-up.
function tile(k, d, anim) {
  const zero = fmtTileNum(0, anim.decimals || 0);
  return `<div class="tile"><div class="k">${k}</div>` +
    `<div class="v" data-target="${anim.value}" data-decimals="${anim.decimals || 0}" ` +
    `data-suffix="${encodeURIComponent(anim.suffix || '')}">${zero}${anim.suffix || ''}</div>` +
    `<div class="d">${d}</div></div>`;
}

// Counts each tile's number up from zero on (re)render. Falls back to
// setting the final value directly if GSAP didn't load or the user
// prefers reduced motion.
// Each call tweens a fresh throwaway {v:0} object (GSAP's overwrite can't
// dedupe those against each other the way it can for a fixed DOM target),
// so a render() re-triggered before the previous count-up finishes would
// otherwise leave multiple tweens racing to write the same tile - kill
// any still-running ones from the last call before starting new ones.
let tileTweens = [];
function animateTiles() {
  tileTweens.forEach(t => t.kill());
  tileTweens = [];
  const els = document.querySelectorAll('#tiles .v[data-target]');
  els.forEach((el, i) => {
    const target = parseFloat(el.dataset.target);
    const decimals = parseInt(el.dataset.decimals, 10);
    const suffix = decodeURIComponent(el.dataset.suffix || '');
    if (typeof gsap === 'undefined' || reduceMotion()) {
      el.innerHTML = fmtTileNum(target, decimals) + suffix;
      return;
    }
    const obj = { v: 0 };
    const tw = gsap.to(obj, {
      v: target, duration: 1, ease: 'power2.out', delay: i * 0.05, overwrite: true,
      onUpdate: () => { el.innerHTML = fmtTileNum(obj.v, decimals) + suffix; },
      onComplete: () => { el.innerHTML = fmtTileNum(target, decimals) + suffix; },
    });
    tileTweens.push(tw);
  });
}

// Fades/slides each card section in as it scrolls into view, instead of
// everything below the fold being visible as a static wall at once.
// Re-created on every render() (country toggle, theme flip) since section
// heights/order/visibility can all change between renders - old triggers
// are killed first so they can't pile up or fire against stale layouts.
let scrollTriggers = [];
function setupScrollReveals() {
  scrollTriggers.forEach(st => st.kill());
  scrollTriggers = [];
  const sections = ['threshSection', 'eventSection', 'expSection', 'tsSection', 'mapSection']
    .map($).filter(el => el && getComputedStyle(el).display !== 'none');
  if (typeof gsap === 'undefined' || typeof ScrollTrigger === 'undefined') return;
  if (reduceMotion()) {
    sections.forEach(el => gsap.set(el, { clearProps: 'all' }));
    return;
  }
  sections.forEach(el => {
    const tween = gsap.from(el, {
      autoAlpha: 0, y: 40, duration: 0.6, ease: 'power2.out',
      scrollTrigger: { trigger: el, start: 'top 88%', once: true },
    });
    scrollTriggers.push(tween.scrollTrigger);
  });
  ScrollTrigger.refresh();
}

const PROFILES = {
  idn: { resultsUrl: 'data/results.json', geoUrl: 'data/districts.geojson' },
  nga: { resultsUrl: 'data/results_nigeria.json', geoUrl: 'data/districts_nigeria.geojson' },
};
const resultsCache = {};
let currentCountry = 'idn', currentR = null;

async function loadResults(country) {
  if (resultsCache[country]) return resultsCache[country];
  const R = await (await fetch(PROFILES[country].resultsUrl)).json();
  resultsCache[country] = R;
  return R;
}

// shared across both countries: vintage chips, threshold section, event
// section, national time series, map. Only the tiles/badges/notes and
// section order/visibility differ.
// Small pill elements (vintage chips, robustness badges) stagger in as a
// group instead of appearing instantly - reinforces "evidence assembling
// piece by piece", especially for the badges. Elements are freshly
// created each call (innerHTML reset), so no old-tween cleanup is needed.
function staggerChips(container) {
  const chips = [...container.children];
  if (chips.length && typeof gsap !== 'undefined' && !reduceMotion()) {
    gsap.from(chips, { autoAlpha: 0, y: 6, duration: 0.35, ease: 'power1.out',
      stagger: { amount: 0.4 } });
  }
}

function renderVintages(M) {
  const V = M.vintages;
  const prelim = M.preliminary_months.length;
  $('vintages').innerHTML = [
    `<span class="chip">Panel <b>${ymLabel(M.panel_start)} – ${ymLabel(M.panel_end)}</b></span>`,
    `<span class="chip">ACLED through <b>${ymLabel(V.acled_through)}</b></span>`,
    `<span class="chip${prelim ? ' prelim' : ''}">Fire final through <b>${ymLabel(V.fire_final_through)}</b>${
      prelim ? ` · <b>${prelim} preliminary month${prelim > 1 ? 's' : ''}</b> (NRT)` : ''}</span>`,
    `<span class="chip">ERA5 wind through <b>${ymLabel(V.wind_through)}</b></span>`,
    `<span class="chip">Updated <b>${new Date(M.generated_at).toISOString().slice(0, 10)}</b></span>`,
  ].join('');
  staggerChips($('vintages'));
}

function renderThreshSection(R) {
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
}
function clearChart(id) { $(id).innerHTML = ''; }

function renderEventNote(country) {
  $('eventNote').textContent = country === 'nga'
    ? 'The suppression effect is concentrated in riots and strategic developments (leadership/coup-related ' +
      'activity); protests, battles/violence, and violence against civilians show no significant response — a ' +
      'different pattern from Indonesia’s.'
    : 'The suppression effect is concentrated in riots and violence against civilians; planned protests and ' +
      'organized battles are unaffected — the pattern that distinguishes a spontaneous-crowd mechanism from a ' +
      'simple disruption-of-assembly story.';
}

function renderEventSection(R) {
  const et = R.event_types;
  const evRows = Object.values(et.fourway).map(r => ({ ...r, label: r.label }))
    .concat(Object.values(et.twoway).map(r => ({ ...r, label: r.label + ' (composite)' })));
  coefPlot($('eventChart'), evRows);
  $('eventTable').innerHTML = '<table><tr><th>Outcome</th><th>Coefficient</th><th>SE</th><th>p</th></tr>' +
    evRows.map(r => `<tr><td>${r.label}</td><td class="${r.coef < 0 && r.p < .05 ? 'neg' : ''}">${fmt(r.coef)}${stars(r.p)}</td><td>${r.se.toFixed(4)}</td><td>${r.p.toFixed(3)}</td></tr>`).join('') + '</table>';
}

function renderExpSection(R) {
  const ex = R.expanding || [];
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
}

function renderTsSection(R) {
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
}

async function renderMapSection(country, R) {
  latest = R.district_latest;
  try {
    geo = await (await fetch(PROFILES[country].geoUrl)).json();
    drawMap();
  } catch (e) {
    $('map').innerHTML = `<p class="note">District boundaries file missing (${PROFILES[country].geoUrl}).</p>`;
  }
}

function renderRobustBadges(rob) {
  const el = $('robustBadges');
  if (!rob || !rob.placebo_test) { el.style.display = 'none'; el.innerHTML = ''; return; }
  const chips = [];
  for (const [k, v] of Object.entries(rob.placebo_test.categories || {}))
    chips.push(`<span class="badge ${v.passed ? 'pass' : 'fail'}">${v.passed ? '✓' : '✗'} placebo test · ${k.replace(/_/g, ' ')}</span>`);
  for (const [k, v] of Object.entries((rob.conley_se || {}).categories || {}))
    chips.push(`<span class="badge ${v.passed ? 'pass' : 'fail'}">${v.passed ? '✓' : '✗'} Conley SE · ${k.replace(/_/g, ' ')}</span>`);
  if (rob.checked_at) chips.push(`<span class="badge">checked ${rob.checked_at}</span>`);
  el.innerHTML = chips.join('');
  el.style.display = 'flex';
  staggerChips(el);
}

function renderRobustNote(R) {
  const full = R.full_iv.find(s => s.label === 'IV-1');
  const rob = R.meta.robustness || {};
  $('robustNote').innerHTML =
    `<div class="label">Reading this dashboard</div>` +
    `<p>Averaged across all ${R.meta.n_districts} LGAs, the effect of fire intensity on conflict is ` +
    `<span class="ns">a precise null</span> (coef ${fmt(full.coef, 4)}, p=${full.p.toFixed(2)}) — unlike Indonesia's ` +
    `population-average result. The signal here is concentrated in two specific event types, ` +
    `<b>Riots</b> and <b>Strategic developments</b>, in districts where conflict recurs (τ ≥ 30% of months).</p>` +
    (rob.summary ? `<p style="margin-top:6px">${rob.summary}</p>` : '') +
    `<p style="margin-top:6px;font-size:12px" class="ns">Point-in-time robustness checks (placebo instrument test, Conley ` +
    `spatial-correlation SEs) were run manually${rob.checked_at ? ' on ' + rob.checked_at : ''} and are not part of the ` +
    `automatic weekly re-estimation; this validation is less extensive than what Indonesia's paper underwent.</p>`;
  $('robustNote').style.display = 'block';
}

function renderIndonesiaTiles(R) {
  const t30 = R.thresholds.find(t => Math.abs(t.threshold - 0.30) < 1e-9);
  const full = R.full_iv.find(s => s.label === 'IV-1');
  const M = R.meta;
  $('tiles').innerHTML =
    tile('First-stage F', `upwind FRP → local FRP · instrument is ${R.first_stage.f_stat > 10 ? 'strong' : 'WEAK'}`,
      { value: R.first_stage.f_stat, decimals: 0 }) +
    tile('Full-panel IV (all events)', `p = ${full.p.toFixed(2)} · the population-average null`,
      { value: full.coef, decimals: 4, suffix: `<span class="stars">${stars(full.p)}</span>` }) +
    tile('Conflict-active districts (τ≥30%)',
      `events per log-point FRP · p = ${t30.events.p.toFixed(3)} · ${t30.n_districts} districts` +
      (t30.events.p < 0.10 ? ` · ≈ ${Math.abs(pctOfMean(t30.events.coef, t30.mean_events)).toFixed(0)}% ` +
        `${t30.events.coef < 0 ? 'fewer' : 'more'} events` : ''),
      { value: t30.events.coef, decimals: 3, suffix: `<span class="stars">${stars(t30.events.p)}</span>` }) +
    tile('Panel', `district-months · ${M.n_districts} districts · ${(M.zero_share_events * 100).toFixed(1)}% zero-event`,
      { value: M.n_obs, decimals: 0 });
  animateTiles();
}

async function renderNigeriaTiles(R) {
  let idnF = null;
  try { idnF = (await loadResults('idn')).first_stage.f_stat; } catch (e) { /* comparison omitted if unavailable */ }
  const fw = R.event_types.fourway;
  const M = R.meta;
  $('tiles').innerHTML =
    tile('First-stage F',
      idnF ? `upwind FRP → local FRP · stronger instrument than Indonesia's ${Math.round(idnF).toLocaleString()}`
           : 'upwind FRP → local FRP · instrument is strong',
      { value: R.first_stage.f_stat, decimals: 0 }) +
    tile('Riots', `p = ${fw.riots.p.toFixed(3)} · τ≥30% conflict-active districts`,
      { value: fw.riots.coef, decimals: 4, suffix: `<span class="stars">${stars(fw.riots.p)}</span>` }) +
    tile('Strategic developments', `p = ${fw.strategic_developments.p.toFixed(3)} · τ≥30% conflict-active districts`,
      { value: fw.strategic_developments.coef, decimals: 4, suffix: `<span class="stars">${stars(fw.strategic_developments.p)}</span>` }) +
    tile('Panel', `district-months · ${M.n_districts} LGAs · ${(M.zero_share_events * 100).toFixed(1)}% zero-event`,
      { value: M.n_obs, decimals: 0 });
  animateTiles();
}

function renderFooter(country) {
  if (country === 'nga') {
    $('foot').innerHTML =
      `Model: y<sub>dt</sub> = α<sub>d</sub> + γ<sub>t</sub> + β·logFRP&#770;<sub>dt</sub> + β₁·logFRP<sub>d,t−1</sub> + ε<sub>dt</sub>, ` +
      `with logFRP instrumented by upwind fire radiative power (same 300 km/±45° upwind-cone instrument as Indonesia). ` +
      `Standard errors clustered by state. Estimates on conflict-active subsamples (τ≥30%) are local effects for LGAs where ` +
      `conflict recurs, not population averages; months flagged preliminary use near-real-time fire detections not yet ` +
      `passed science-quality processing. Districts: GADM level-2 LGA boundaries. ` +
      `Conflict data © <a href="https://acleddata.com">ACLED</a>, used under its terms (aggregated counts only). ` +
      `Fire detections: NASA FIRMS VIIRS S-NPP. Winds: Copernicus ERA5. ` +
      `Code: <a href="https://github.com/Hariaksha/wlidfire-conflict">github.com/Hariaksha/wlidfire-conflict</a>.`;
  } else {
    $('foot').innerHTML =
      `Model: y<sub>dt</sub> = α<sub>d</sub> + γ<sub>t</sub> + β·logFRP&#770;<sub>dt</sub> + β₁·logFRP<sub>d,t−1</sub> + ε<sub>dt</sub>, ` +
      `with logFRP instrumented by upwind fire radiative power (300 km radius, ±45° cone around the district's monthly wind direction). ` +
      `Standard errors clustered by province. Estimates on conflict-active subsamples are local effects for districts where conflict recurs, ` +
      `not population averages; months flagged preliminary use near-real-time fire detections that have not yet passed science-quality processing. ` +
      `Conflict data © <a href="https://acleddata.com">ACLED</a>, used under its terms (aggregated counts only). ` +
      `Fire detections: NASA FIRMS VIIRS S-NPP. Winds: Copernicus ERA5. ` +
      `Code: <a href="https://github.com/Hariaksha/wlidfire-conflict">github.com/Hariaksha/wlidfire-conflict</a>. ` +
      `Paper: <a href="https://github.com/Hariaksha/wlidfire-conflict/blob/main/paper/draft.pdf">draft.pdf</a>.`;
  }
}

function orderSections(country) {
  const wrap = $('content');
  const threshSection = $('threshSection'), eventSection = $('eventSection');
  if (country === 'nga') {
    // event-type decomposition is Nigeria's primary chart, promoted above
    // the (secondary, here) conflict-active-threshold cut.
    wrap.insertBefore(eventSection, threshSection);
  } else {
    wrap.insertBefore(threshSection, eventSection);
  }
  $('expSection').style.display = country === 'nga' ? 'none' : '';
  $('plainLang').style.display = country === 'nga' ? 'none' : '';
  $('robustBadges').style.display = country === 'nga' ? 'flex' : 'none';
  $('robustNote').style.display = country === 'nga' ? 'block' : 'none';
}

async function render(country, R) {
  ['threshChart', 'eventChart', 'expChart', 'tsChart', 'frpChart'].forEach(clearChart);
  orderSections(country);
  renderVintages(R.meta);
  if (country === 'nga') {
    await renderNigeriaTiles(R);
    renderRobustBadges(R.meta.robustness);
    renderRobustNote(R);
  } else {
    renderIndonesiaTiles(R);
    $('plainLang').innerHTML = plainLanguageHTML(R.thresholds);
  }
  renderThreshSection(R);
  renderEventNote(country);
  renderEventSection(R);
  renderExpSection(R);
  renderTsSection(R);
  await renderMapSection(country, R);
  renderFooter(country);
  setupScrollReveals();
}

// Crossfades #content (everything below the header/toggle) around a
// country switch or the initial load, instead of an instant DOM swap.
// overwrite:true is required here - without it, a rapid second toggle
// click starts a new tween on #content while the first is still running,
// and the two competing autoAlpha tweens can leave it stuck at whatever
// opacity they happened to blend to.
function fadeContent(dir) {
  return new Promise((resolve) => {
    const content = $('content');
    if (typeof gsap === 'undefined' || reduceMotion()) {
      content.style.opacity = dir === 'out' ? '0' : '';
      resolve();
      return;
    }
    if (dir === 'out') {
      gsap.to(content, { autoAlpha: 0, y: 6, duration: 0.16, ease: 'power1.in', overwrite: true, onComplete: resolve });
    } else {
      gsap.fromTo(content, { autoAlpha: 0, y: 6 },
        { autoAlpha: 1, y: 0, duration: 0.4, ease: 'power2.out', overwrite: true, onComplete: resolve });
    }
  });
}

let switching = false;
async function switchCountry(country) {
  if (switching) return; // ignore clicks while a transition is already in flight
  switching = true;
  document.querySelectorAll('#countryToggle button').forEach(b =>
    b.setAttribute('aria-pressed', String(b.dataset.country === country)));
  if (currentR) await fadeContent('out');
  let R;
  try {
    R = await loadResults(country);
  } catch (e) {
    document.querySelector('.wrap').insertAdjacentHTML('beforeend',
      `<div class="err">Could not load <code>${PROFILES[country].resultsUrl}</code>. Run the pipeline first.</div>`);
    await fadeContent('in');
    switching = false;
    return;
  }
  currentCountry = country;
  currentR = R;
  await render(country, R);
  await fadeContent('in');
  switching = false;
}

document.querySelectorAll('#countryToggle button').forEach(b =>
  b.addEventListener('click', () => { if (b.dataset.country !== currentCountry) switchCountry(b.dataset.country); }));

// redraw svg colors + re-render on theme flip
new MutationObserver(() => {
  if (!currentR) return;
  render(currentCountry, currentR);
}).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

switchCountry('idn');
