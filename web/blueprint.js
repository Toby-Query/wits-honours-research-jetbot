// Blueprint view: the board as an editable node graph.
//
// Every component of the circuit is a node -- inputs down the left, an
// instrument and its numbers in the middle, outputs down the right -- and
// every net is a wire between pins. It is drawn from /design, which is the
// live elecsim circuit: nothing here is specific to the JetBot except the
// order the columns are laid out in and a few instruments. A different
// design served the same way would draw itself.
//
// Every number is an input. Its starting value is the JetBot's own (from
// hardware.py); change it and the Python twin is changed, re-solves, and
// sends back what happened -- including which ratings you have just broken.
// Nothing is computed in the browser.

const $ = (id) => document.getElementById(id);
const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const post = (body) => fetch('/cmd', { method: 'POST', body: JSON.stringify(body) }).catch(() => {});
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

function fmt(v) {
  if (v === undefined || v === null || !isFinite(v)) return '–';
  const a = Math.abs(v);
  if (a < 1e-6) return '0';
  if (a >= 1000) return v.toFixed(0);
  if (a >= 100) return v.toFixed(1);
  if (a >= 1) return v.toFixed(3);
  if (a >= 0.001) return v.toFixed(4);
  return v.toExponential(2);
}
const sig4 = (v) => (v === 0 ? '0' : String(+Number(v).toPrecision(4)));

// ---------------------------------------------------------------- layout
// Columns follow the power path, left to right. Anything not named here
// (a part you added) lands in a last column of its own.
const COLUMNS = [
  ['cell3', 'cell2', 'cell1', 'holder'],
  ['protector', 'R15', 'charger', 'R16', 'ina219'],
  ['S1', 'C_bulk', 'U3', 'C_5V'],
  ['jetson', 'pca9685', 'oled'],
  ['drive_left', 'drive_right', 'bridge_left', 'motor_left', 'bridge_right', 'motor_right'],
];
const COL_W = 372, GAP_Y = 22, PAD = 30;

// what to show first on each kind of part: [signal, label, unit]
const READ = {
  Cell: [['v', 'terminal', 'V'], ['i', 'current out', 'A'], ['soc', 'charge', ''], ['heat', 'heat', 'W']],
  Resistor: [['v', 'drop', 'V'], ['i', 'current', 'A'], ['heat', 'heat', 'W']],
  PackProtector: [['i', 'current', 'A'], ['cell_min', 'lowest cell', 'V'], ['cell_max', 'highest cell', 'V'], ['trips', 'trips', '']],
  VoltageSource: [['v', 'terminal', 'V'], ['i', 'current out', 'A'], ['cc', 'current-limited', '']],
  ShuntMonitor: [['bus_v', 'reads bus', 'V'], ['current', 'reads current', 'A'], ['v_shunt', 'shunt drop', 'V']],
  Switch: [['i', 'current', 'A'], ['v', 'drop', 'V'], ['heat', 'heat', 'W']],
  Capacitor: [['v', 'voltage', 'V'], ['i', 'current', 'A']],
  BuckConverter: [['v_in', 'input', 'V'], ['v_out', 'output', 'V'], ['i_out', 'load', 'A'], ['i_in', 'draws', 'A'], ['eff', 'efficiency', ''], ['T', 'temperature', '°C']],
  JetsonNano: [['v', '5 V rail', 'V'], ['i', 'draws', 'A'], ['p', 'power', 'W'], ['i_3v3', '3V3 supplied', 'A']],
  PwmController: [['freq', 'PWM', 'Hz'], ['v', 'supply', 'V']],
  ConstantCurrentLoad: [['v', 'supply', 'V'], ['i', 'draws', 'A']],
  PwmMotorDrive: [['duty', 'duty', ''], ['i', 'motor current', 'A'], ['i_rms', 'rms', 'A'], ['i_supply', 'from the rail', 'A'], ['rpm_', 'wheel', 'rpm'], ['torque', 'torque', 'N·m'], ['T', 'driver temp', '°C']],
  HBridge: [['i', 'output', 'A'], ['i_supply', 'from the rail', 'A'], ['v_out', 'across motor', 'V'], ['T', 'temperature', '°C']],
  DCMotor: [['i', 'current', 'A'], ['v', 'voltage', 'V'], ['rpm', 'speed', 'rpm'], ['torque', 'torque', 'N·m']],
};
// the numbers worth showing without opening "more"
const FIRST = {
  Cell: ['capacity_ah', 'r0', 'soc'], Resistor: ['r', 'p_max'], PackProtector: ['r_on', 'i_oc', 'v_uv'],
  VoltageSource: ['v', 'i_limit'], ShuntMonitor: ['r_shunt'], Switch: ['r_on', 'i_max'], Capacitor: ['c', 'esr'],
  BuckConverter: ['v_set', 'i_limit', 'rds_on'], JetsonNano: ['power', 'v_min'], PwmController: ['prescale'],
  ConstantCurrentLoad: ['i'], PwmMotorDrive: ['r', 'l', 'ke', 'r_on', 'gear_eff'], HBridge: ['r_hs', 'r_ls'],
  DCMotor: ['r', 'l', 'ke'],
};
const LEFT = new Set(['power_in', 'digital_in', 'analog_in', 'ground', 'bidir']);

let design = null, latest = null, view = { k: 0.8 }, settled = false;
const N = {};                       // name -> node record
let pos = {};
try { pos = JSON.parse(localStorage.getItem('jetbot-bp-pos') || '{}'); } catch (e) { pos = {}; }

function netClass(net) {
  if (net.name === 'GND' || net.name === 'PACK-') return 'gnd';
  if (net.signal) return 'signal';
  return 'power';
}

// ---------------------------------------------------------------- building
function propRow(c, key, p) {
  const row = document.createElement('div');
  row.className = 'bprop' + (p.edited ? ' edited' : '');
  const d = p.default;
  let lo = d > 0 ? d * 0.2 : d < 0 ? d * 3 : 0, hi = d > 0 ? d * 3 : d < 0 ? d * 0.2 : 1;
  if (p.min !== null && p.min !== undefined) lo = Math.max(lo, p.min);
  if (p.max !== null && p.max !== undefined) hi = Math.min(hi, p.max);
  if (key === 'soc') { lo = 0; hi = 1; }
  const tip = [p.doc, p.src ? `${p.assumed ? 'ASSUMED: ' : 'from: '}${p.src}` : '', `JetBot value ${sig4(d)} ${p.unit}`]
    .filter(Boolean).join('\n');
  row.title = tip;
  row.innerHTML = `<span class="plab${p.assumed ? ' assumed' : ''}">${key}</span>` +
    `<input class="bnum" type="number" step="any" value="${sig4(p.value)}">` +
    `<span class="punit">${p.unit}</span>` +
    `<input type="range" min="${lo}" max="${hi}" step="${(hi - lo) / 400 || 0.001}" value="${p.value}">`;
  const [num, rng] = row.querySelectorAll('input');
  const send = (v) => {
    v = +v;
    if (!isFinite(v)) return;
    post({ action: 'param', comp: c.name, name: key, value: v });
    p.value = v;
    row.classList.toggle('edited', Math.abs(v - d) > 1e-12 * Math.max(1, Math.abs(d)));
  };
  num.onchange = () => { rng.value = num.value; send(num.value); };
  rng.oninput = () => { num.value = sig4(rng.value); send(rng.value); };
  return row;
}

function build() {
  const host = $('bp-nodes');
  host.innerHTML = '';
  for (const k of Object.keys(N)) delete N[k];
  const byName = Object.fromEntries(design.components.map((c) => [c.name, c]));
  const placed = new Set();
  const cols = COLUMNS.map((col) => col.filter((n) => byName[n]));
  cols.forEach((col) => col.forEach((n) => placed.add(n)));
  const rest = design.components.map((c) => c.name).filter((n) => !placed.has(n));
  if (rest.length) cols.push(rest);

  cols.forEach((col, ci) => col.forEach((name) => {
    const c = byName[name];
    const el = document.createElement('div');
    el.className = 'bnode';
    el.dataset.name = name;
    const pins = c.pins.filter((p) => p.net);
    const passive = pins.filter((p) => !LEFT.has(p.kind) && !['power_out', 'digital_out', 'analog_out'].includes(p.kind));
    const isLeft = (p) => LEFT.has(p.kind) || (passive.length > 1 && p === passive[0] && !pins.some((q) => LEFT.has(q.kind) && q.kind !== 'ground'));
    const pinHtml = (p, out) => `<div class="pin${out ? ' out' : ''}${p.kind === 'ground' || p.kind.startsWith('power') ? ' sq' : ''}" ` +
      `data-pin="${name}.${p.name}" title="${p.kind} · net ${p.net}"><span class="dot"></span>${p.name}</div>`;
    const first = FIRST[c.type] || Object.keys(c.params).slice(0, 3);
    const keys = Object.keys(c.params);
    const main = keys.filter((k) => first.includes(k));
    const more = keys.filter((k) => !first.includes(k));
    el.innerHTML =
      `<div class="bhead" title="${c.doc}"><b>${name}</b><small>${c.type}</small></div>` +
      `<div class="bbody"><div class="bpins">${pins.filter(isLeft).map((p) => pinHtml(p, false)).join('')}</div>` +
      `<div class="bmid">${WIDGET[c.type] ? '<canvas class="bwidget"></canvas>' : ''}` +
      `<div class="bread"></div><div class="blim"></div><div class="bedit main"></div>` +
      `${more.length ? `<details class="bmore"><summary>${more.length} more numbers</summary><div class="bedit more"></div></details>` : ''}</div>` +
      `<div class="bpins out">${pins.filter((p) => !isLeft(p)).map((p) => pinHtml(p, true)).join('')}</div></div>` +
      `<div class="bfault"></div>${c.note ? `<div class="bfoot">${c.note}</div>` : ''}`;
    main.forEach((k) => el.querySelector('.bedit.main').appendChild(propRow(c, k, c.params[k])));
    more.forEach((k) => el.querySelector('.bedit.more').appendChild(propRow(c, k, c.params[k])));
    host.appendChild(el);
    const rec = { el, c, col: ci, read: el.querySelector('.bread'), lim: el.querySelector('.blim'),
      fault: el.querySelector('.bfault'), canvas: el.querySelector('canvas'), phase: 0 };
    N[name] = rec;
    makeDraggable(rec);
    const det = el.querySelector('details');
    if (det) det.addEventListener('toggle', () => requestAnimationFrame(() => layout(false)));
  }));
  layout(false);
}

function layout(forget) {
  if (forget) { pos = {}; localStorage.removeItem('jetbot-bp-pos'); }
  const colY = {};
  for (const [name, n] of Object.entries(N)) {
    if (pos[name]) { n.x = pos[name][0]; n.y = pos[name][1]; }
    else {
      n.x = PAD + n.col * COL_W;
      n.y = colY[n.col] === undefined ? PAD : colY[n.col];
    }
    colY[n.col] = Math.max(colY[n.col] || 0, n.y + n.el.offsetHeight + GAP_Y);
    n.el.style.left = n.x + 'px'; n.el.style.top = n.y + 'px';
  }
  applyView();
}

function makeDraggable(n) {
  const head = n.el.querySelector('.bhead');
  head.addEventListener('pointerdown', (e) => {
    head.setPointerCapture(e.pointerId);
    const sx = e.clientX, sy = e.clientY, ox = n.x, oy = n.y;
    const move = (ev) => {
      n.x = Math.max(0, ox + (ev.clientX - sx) / view.k);
      n.y = Math.max(0, oy + (ev.clientY - sy) / view.k);
      n.el.style.left = n.x + 'px'; n.el.style.top = n.y + 'px';
      drawEdges();
    };
    const up = () => {
      head.removeEventListener('pointermove', move);
      pos[n.c.name] = [n.x, n.y];
      localStorage.setItem('jetbot-bp-pos', JSON.stringify(pos));
      applyView();
    };
    head.addEventListener('pointermove', move);
    head.addEventListener('pointerup', up, { once: true });
  });
}

// ---------------------------------------------------------------- wires
function pinCentre(key) {
  const [name] = key.split('.');
  const n = N[name];
  const dot = n && n.el.querySelector(`[data-pin="${key}"] .dot`);
  if (!dot || n.el.classList.contains('hide')) return null;
  const r = dot.getBoundingClientRect(), b = $('bp-scale').getBoundingClientRect();
  return { x: (r.left + r.width / 2 - b.left) / view.k, y: (r.top + r.height / 2 - b.top) / view.k,
    out: dot.parentElement.classList.contains('out') };
}

let edges = [];
function drawEdges() {
  const svg = $('bp-edges');
  svg.innerHTML = '';
  edges = [];
  const showGnd = $('rails').checked;
  for (const net of design.netlist) {
    const cls = netClass(net);
    if (cls === 'gnd' && !showGnd) continue;
    const pts = net.pins.map((k) => ({ k, p: pinCentre(k) })).filter((x) => x.p);
    if (pts.length < 2) continue;
    const src = pts.find((x) => x.p.out) || pts[0];
    for (const dst of pts) {
      if (dst === src) continue;
      const a = src.p, b = dst.p;
      const dx = Math.max(40, Math.abs(b.x - a.x) * 0.45);
      const ax = a.x + (a.out ? dx : -dx), bx = b.x + (b.out ? dx : -dx);
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', `M${a.x},${a.y} C${ax},${a.y} ${bx},${b.y} ${b.x},${b.y}`);
      path.setAttribute('stroke', css('--n-' + cls));
      const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
      path.appendChild(title);
      svg.appendChild(path);
      edges.push({ path, title, net });
    }
  }
}

function applyView() {
  let w = 0, h = 0;
  for (const n of Object.values(N)) {
    if (n.el.classList.contains('hide')) continue;
    w = Math.max(w, n.x + n.el.offsetWidth); h = Math.max(h, n.y + n.el.offsetHeight);
  }
  w += PAD; h += PAD;
  $('bp-scale').style.transform = `scale(${view.k})`;
  $('bp-scale').style.width = w + 'px'; $('bp-scale').style.height = h + 'px';
  $('bp-world').style.width = w * view.k + 'px'; $('bp-world').style.height = h * view.k + 'px';
  $('bp-edges').setAttribute('width', w); $('bp-edges').setAttribute('height', h);
  $('zoom').value = view.k;
  view.w = w; view.h = h;
  drawEdges();
}
function zoomTo(k) { view.k = clamp(k, 0.3, 1.6); applyView(); }
function fit() {
  const box = $('bp');
  zoomTo(Math.min(box.clientWidth / view.w, box.clientHeight / view.h, 1.2));
}

// ---------------------------------------------------------------- instruments
// Not a picture of the part: what the part is doing, right now.
function prep(c) {
  const dpr = Math.min(devicePixelRatio, 2), w = c.clientWidth, h = c.clientHeight;
  if (c.width !== w * dpr) { c.width = w * dpr; c.height = h * dpr; }
  const g = c.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  g.font = '10px ui-monospace, Menlo, monospace';
  return [g, w, h];
}
const WIDGET = {
  Cell(n, s, m, dt) {
    const [g, w, h] = prep(n.canvas);
    const soc = s.soc || 0, x = 14, y = 16, bw = w - 40, bh = h - 30;
    g.strokeStyle = css('--muted'); g.lineWidth = 1.5;
    g.strokeRect(x, y, bw, bh); g.fillStyle = css('--muted'); g.fillRect(x + bw, y + bh * 0.3, 5, bh * 0.4);
    g.fillStyle = s.v < n.c.params.v_min.value + 0.3 ? css('--bad') : soc < 0.2 ? css('--warn') : css('--good');
    g.fillRect(x + 2, y + 2, (bw - 4) * soc, bh - 4);
    g.fillStyle = css('--ink');
    g.fillText(`${(soc * 100).toFixed(0)} %   ${fmt(s.v)} V   sag ${fmt((s.ocv - s.v) * 1000)} mV`, x, 11);
  },
  PwmMotorDrive(n, s, m, dt) {
    const [g, w, h] = prep(n.canvas);
    // left: the wheel, turning at the speed the twin says it is turning
    n.phase += (s.omega || 0) * dt;
    const cx = 34, cy = h / 2, r = 22;
    g.strokeStyle = css('--muted'); g.lineWidth = 2;
    g.beginPath(); g.arc(cx, cy, r, 0, 7); g.stroke();
    for (let k = 0; k < 3; k++) {
      const a = -n.phase + k * 2.094;
      g.beginPath(); g.moveTo(cx, cy); g.lineTo(cx + r * Math.cos(a), cy + r * Math.sin(a)); g.stroke();
    }
    // right: one PWM period -- the gate, and the winding current under it
    const x0 = 72, x1 = w - 8, y0 = 8, y1 = h - 14, span = x1 - x0;
    const d = Math.abs(s.duty || 0), ipk = Math.max(s.i_peak || 0, 1e-6), scale = Math.max(ipk, 0.5);
    g.fillStyle = css('--accent'); g.globalAlpha = 0.18;
    g.fillRect(x0, y0, span * d, y1 - y0); g.globalAlpha = 1;
    g.strokeStyle = css('--line'); g.lineWidth = 1; g.strokeRect(x0, y0, span, y1 - y0);
    const Y = (i) => y1 - (y1 - y0 - 4) * i / scale;
    const lo = s.continuous ? Math.max(ipk - (s.ripple || 0), 0) : 0;
    g.strokeStyle = css('--accent'); g.lineWidth = 2; g.beginPath();
    g.moveTo(x0, Y(lo)); g.lineTo(x0 + span * d, Y(ipk));
    if (s.continuous || d >= 0.999) g.lineTo(x1, Y(lo));
    else {
      // discontinuous: the current is gone before the period is
      const fall = Math.min(1 - d, (1 - d) * 0.5 + 0.02);
      g.lineTo(x0 + span * (d + fall), Y(0)); g.lineTo(x1, Y(0));
    }
    g.stroke();
    g.fillStyle = css('--muted');
    g.fillText(`${ipk.toFixed(2)} A peak, ${s.continuous ? 'continuous' : 'stops'}`, x0, h - 3);
  },
  BuckConverter(n, s, m) {
    const [g, w, h] = prep(n.canvas);
    const rows = [['in', s.v_in, 13], ['out', s.v_out, 13]];
    rows.forEach(([lab, v, max], k) => {
      const y = 12 + k * 20;
      g.fillStyle = css('--muted'); g.fillText(lab, 8, y + 8);
      g.fillStyle = css('--line'); g.fillRect(34, y, w - 100, 10);
      g.fillStyle = k ? css('--good') : css('--accent'); g.fillRect(34, y, (w - 100) * clamp(v / max, 0, 1), 10);
      g.fillStyle = css('--ink'); g.fillText(`${fmt(v)} V`, w - 60, y + 9);
    });
    g.fillStyle = m.buck === 'reg' ? css('--muted') : css('--bad');
    g.fillText(`${m.buck === 'reg' ? 'regulating' : m.buck.toUpperCase()} · ${(s.eff * 100 || 0).toFixed(0)} % · loses ${fmt(s.heat)} W`, 8, h - 6);
  },
  JetsonNano(n, s) {
    const [g, w, h] = prep(n.canvas);
    // a meter for the 5 V rail, with the tolerance the Jetson allows marked
    const lo = 4.2, hi = 5.6, x0 = 10, x1 = w - 10, y = 26;
    const X = (v) => x0 + (x1 - x0) * clamp((v - lo) / (hi - lo), 0, 1);
    g.fillStyle = css('--line'); g.fillRect(x0, y, x1 - x0, 8);
    g.fillStyle = css('--good'); g.globalAlpha = 0.45;
    g.fillRect(X(n.c.params.v_min.value), y, X(n.c.params.v_max.value) - X(n.c.params.v_min.value), 8);
    g.globalAlpha = 1;
    const ok = s.v >= n.c.params.v_min.value && s.v <= n.c.params.v_max.value;
    g.fillStyle = ok ? css('--ink') : css('--bad'); g.fillRect(X(s.v) - 1.5, y - 6, 3, 20);
    g.fillStyle = css('--ink'); g.fillText(`${fmt(s.v)} V   ${fmt(s.p)} W`, x0, 14);
    g.fillStyle = s.browned_out ? css('--bad') : css('--muted');
    g.fillText(s.browned_out ? 'BROWNED OUT: it has reset' : 'green band: what the Jetson tolerates', x0, h - 8);
  },
  PackProtector(n, s, m) {
    const [g, w, h] = prep(n.canvas);
    const on = m.protector === 'on', y = h / 2 - 4;
    g.strokeStyle = on ? css('--good') : css('--bad'); g.lineWidth = 2.5;
    g.beginPath(); g.moveTo(10, y); g.lineTo(w * 0.36, y);
    if (on) g.lineTo(w * 0.64, y); else g.lineTo(w * 0.6, y - 16);
    g.moveTo(w * 0.64, y); g.lineTo(w - 10, y); g.stroke();
    g.fillStyle = on ? css('--muted') : css('--bad');
    const why = { on: 'closed', uv: 'OPEN: a cell is too low', ov: 'OPEN: a cell is too high', oc: 'OPEN: overcurrent' }[m.protector];
    g.fillText(`${why} · ${fmt(s.i)} A of ${sig4(n.c.params.i_oc.value)} A`, 10, h - 8);
  },
  Switch(n, s) {
    const [g, w, h] = prep(n.canvas);
    const y = h / 2 - 4, on = s.closed > 0 && !s.failed;
    g.strokeStyle = on ? css('--good') : css('--muted'); g.lineWidth = 2.5;
    g.beginPath(); g.moveTo(10, y); g.lineTo(w * 0.36, y);
    if (on) g.lineTo(w * 0.64, y); else g.lineTo(w * 0.6, y - 16);
    g.moveTo(w * 0.64, y); g.lineTo(w - 10, y); g.stroke();
    const rated = n.c.params.i_max ? n.c.params.i_max.value : null;
    g.fillStyle = rated && Math.abs(s.i) > rated ? css('--warn') : css('--muted');
    g.fillText(`${fmt(Math.abs(s.i))} A through it${rated ? `, rated ${sig4(rated)} A` : ''}`, 10, h - 8);
  },
  PwmController(n, s) {
    const [g, w, h] = prep(n.canvas);
    const chans = Object.keys(s).filter((k) => k.startsWith('duty')).sort((a, b) => +a.slice(4) - +b.slice(4));
    const bw = (w - 16) / Math.max(chans.length, 1);
    chans.forEach((k, i) => {
      const x = 8 + i * bw, d = s[k];
      g.fillStyle = css('--line'); g.fillRect(x, 8, bw - 4, h - 26);
      g.fillStyle = css('--accent'); g.fillRect(x, 8 + (h - 26) * (1 - d), bw - 4, (h - 26) * d);
      g.fillStyle = css('--muted'); g.fillText(k.slice(4), x + 1, h - 6);
    });
  },
};

// ---------------------------------------------------------------- painting
function sigsOf(m, name) {
  const out = {}, pre = name + '.';
  for (const k in m.sig) if (k.startsWith(pre)) out[k.slice(pre.length)] = m.sig[k];
  if (out.omega !== undefined) out.rpm_ = out.omega * 9.549;
  return out;
}

function paint(m, dt, slow) {
  const lims = {};
  for (const l of m.limits) (lims[l.label.split('.')[0]] = lims[l.label.split('.')[0]] || []).push(l);
  for (const [name, n] of Object.entries(N)) {
    const s = sigsOf(m, name);
    if (n.canvas && n.el.offsetParent) WIDGET[n.c.type](n, s, m, dt);
    if (!slow) continue;
    const rows = READ[n.c.type] || Object.keys(s).filter((k) => k !== 'failed').slice(0, 6).map((k) => [k, k, '']);
    n.read.innerHTML = rows.filter(([k]) => s[k] !== undefined)
      .map(([k, lab, u]) => `<span>${lab}</span><b>${k === 'soc' || k === 'eff' ? (s[k] * 100).toFixed(1) + ' %' : fmt(s[k]) + (u ? ' ' + u : '')}</b>`).join('');
    const L = (lims[name] || []).filter((l) => isFinite(l.margin)).sort((a, b) => a.margin - b.margin);
    n.lim.innerHTML = L.slice(0, 4).map((l) => {
      const used = clamp(1 - l.margin, 0, 1);
      const cls = l.violated ? (l.severity === 'error' ? 'x' : 'w') : '';
      return `<div class="${cls}" title="${l.why}"><span>${l.label.slice(name.length + 1)}</span><span>${fmt(l.worst)}</span></div><i class="${cls}"><u style="width:${(used * 100).toFixed(0)}%"></u></i>`;
    }).join('');
    const bad = L.filter((l) => l.violated && l.severity === 'error');
    const warn = L.filter((l) => l.violated && l.severity !== 'error');
    const failed = s.failed > 0;
    n.el.classList.toggle('burnt', bad.length > 0 || failed);
    n.el.classList.toggle('warn', !bad.length && warn.length > 0);
    const list = bad.length ? bad : warn;
    n.fault.className = 'bfault' + (failed || list.length ? ' on ' + (bad.length || failed ? 'burn' : 'warn') : '');
    n.fault.innerHTML = failed ? 'failed open (injected fault)' :
      list.map((l) => `${l.why || l.label}: reached ${fmt(l.worst)} against ${fmt(l.bound)}`).join('<br>');
    for (const pinEl of n.el.querySelectorAll('.pin')) {
      const p = n.c.pins.find((q) => `${name}.${q.name}` === pinEl.dataset.pin);
      const v = m.nets[p.net];
      pinEl.classList.toggle('live', p.kind !== 'ground' && Math.abs(v) > (p.kind.startsWith('digital') ? 0.004 : 0.3));
    }
  }
  if (slow) {
    for (const e of edges) {
      const v = m.nets[e.net.name];
      e.path.classList.toggle('dim', netClass(e.net) !== 'gnd' && Math.abs(v) < (e.net.signal ? 0.004 : 0.3));
      e.title.textContent = e.net.signal ? `${e.net.name}: duty ${fmt(v)}` : `${e.net.name}: ${fmt(v)} V`;
    }
    const nb = m.limits.filter((l) => l.violated && l.severity === 'error').length;
    const nw = m.limits.filter((l) => l.violated && l.severity !== 'error').length;
    const st = $('status');
    st.textContent = `t ${m.t.toFixed(1)} s · bus ${fmt(m.tel.bus_v)} V · pack ${fmt(m.tel.i_pack)} A` +
      (nb ? ` · ${nb} rating${nb > 1 ? 's' : ''} exceeded` : nw ? ` · ${nw} warning${nw > 1 ? 's' : ''}` : ' · every rating respected') +
      (m.edited ? ` · ${m.edited} number${m.edited > 1 ? 's' : ''} changed from the JetBot's` : '');
    st.className = nb ? 'bad' : nw ? 'warn' : '';
    // The readouts and rating bars have just given the nodes their real
    // heights; stack the columns again now that they are known.
    if (!settled) { settled = true; layout(false); fit(); }
  }
}

// ---------------------------------------------------------------- controls
function loadDesign() {
  return fetch('/design').then((r) => r.json()).then((d) => {
    const sigNets = new Set();
    const netlist = Object.entries(d.nets).map(([name, pins]) => ({ name, pins }));
    const kind = {};
    d.components.forEach((c) => c.pins.forEach((p) => { kind[`${c.name}.${p.name}`] = p.kind; }));
    netlist.forEach((n) => { n.signal = n.pins.every((k) => ['digital_in', 'digital_out', 'bidir'].includes(kind[k])); });
    d.netlist = netlist;
    design = d;
    settled = false;
    build();
    applyFilter();
  });
}
function applyFilter() {
  const q = $('filter').value.trim().toLowerCase();
  for (const [name, n] of Object.entries(N)) {
    const hit = !q || name.toLowerCase().includes(q) || n.c.type.toLowerCase().includes(q) ||
      n.c.pins.some((p) => (p.net || '').toLowerCase().includes(q));
    n.el.classList.toggle('hide', !hit);
  }
  applyView();
}
$('filter').oninput = applyFilter;
$('rails').onchange = drawEdges;
$('zoom').oninput = (e) => zoomTo(+e.target.value);
$('zin').onclick = () => zoomTo(view.k * 1.15);
$('zout').onclick = () => zoomTo(view.k / 1.15);
$('fit').onclick = fit;
$('tidy').onclick = () => layout(true);
$('bp').addEventListener('wheel', (e) => {
  if (!(e.ctrlKey || e.metaKey)) return;
  e.preventDefault();
  zoomTo(view.k * (e.deltaY < 0 ? 1.08 : 1 / 1.08));
}, { passive: false });
addEventListener('resize', applyView);
addEventListener('keydown', (e) => {
  if (e.key.toLowerCase() === 'b' && !['INPUT', 'SELECT'].includes(document.activeElement.tagName)) location.href = './index.html';
});

function sendHold() {
  $('cl-v').textContent = (+$('cl').value).toFixed(2);
  $('cr-v').textContent = (+$('cr').value).toFixed(2);
  post({ action: 'hold', value: [+$('cl').value, +$('cr').value] });
}
$('cl').oninput = () => { if ($('link').checked) $('cr').value = $('cl').value; sendHold(); };
$('cr').oninput = () => { if ($('link').checked) $('cl').value = $('cr').value; sendHold(); };
$('stop').onclick = () => { $('cl').value = $('cr').value = 0; sendHold(); };
for (const f of ['held', 'lifted', 'charger']) {
  $('f-' + f).onchange = (e) => post({ action: 'flag', name: f, value: e.target.checked });
}
$('f-jetson').onchange = (e) => post({ action: 'flag', name: 'jetson', value: e.target.value });
const later = (fn) => new Promise((r) => setTimeout(r, 250)).then(fn);
$('board-sel').onchange = (e) => post({ action: 'set', name: 'board', value: e.target.value }).then(() => later(loadDesign));
$('repair').onclick = () => post({ action: 'repair' }).then(() => later(loadDesign));
$('resetv').onclick = () => post({ action: 'param_reset' }).then(() => later(loadDesign));

// ---------------------------------------------------------------- the stream
const es = new EventSource('/stream');
es.onmessage = (ev) => {
  const m = JSON.parse(ev.data);
  if (m.error) { $('err').textContent = 'the twin stopped: ' + m.error; $('err').style.display = 'block'; return; }
  $('err').style.display = 'none';
  latest = m;
};
es.onerror = () => { $('err').textContent = 'lost the twin: is `python -m jetbot_twin serve` still running?'; $('err').style.display = 'block'; };

let prev = performance.now(), lastSlow = 0, synced = false;
function frame(now) {
  const dt = Math.min(0.1, (now - prev) / 1000); prev = now;
  if (latest && design) {
    if (!synced) {
      $('board-sel').value = latest.board; $('f-jetson').value = latest.flags.jetson;
      for (const f of ['held', 'lifted', 'charger']) $('f-' + f).checked = latest.flags[f];
      if (latest.hold) { $('cl').value = latest.hold[0]; $('cr').value = latest.hold[1]; }
      $('cl-v').textContent = (+$('cl').value).toFixed(2); $('cr-v').textContent = (+$('cr').value).toFixed(2);
      synced = true;
    }
    const slow = now - lastSlow > 120;
    if (slow) lastSlow = now;
    paint(latest, dt, slow);
  }
  requestAnimationFrame(frame);
}
const waitMs = new URLSearchParams(location.search).get('wait');
if (waitMs) { const im = document.createElement('img'); im.style.display = 'none'; im.src = '/delay?ms=' + (+waitMs); document.body.appendChild(im); }
loadDesign().then(() => { fit(); requestAnimationFrame(frame); });
