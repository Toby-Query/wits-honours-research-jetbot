// The page. It draws; it does not compute. Every number shown arrived on the
// /stream from the Python twin, and every control posts to /cmd.
import * as THREE from 'three';
import { GLTFLoader } from './vendor/three/GLTFLoader.js';
import { OrbitControls } from './vendor/three/OrbitControls.js';

const $ = (id) => document.getElementById(id);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const post = (body) => fetch('/cmd', { method: 'POST', body: JSON.stringify(body) }).catch(() => {});
const fmt = (x, d = 2) => (x === undefined || x === null || !isFinite(x)) ? '–' : Number(x).toFixed(d);

// ?wait=5000 holds the load event open that long: for headless screenshots.
const waitMs = new URLSearchParams(location.search).get('wait');
if (waitMs) {
  const im = document.createElement('img');
  im.style.display = 'none';
  im.src = '/delay?ms=' + (+waitMs);
  document.body.appendChild(im);
}

// ---------------------------------------------------------------- 3D scene
const canvas = $('view');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 50);
camera.position.set(-0.45, 0.38, 0.55);
const lookFrom = new URLSearchParams(location.search).get('view');   // ?view=front | side
if (lookFrom === 'front') camera.position.set(0.30, 0.20, 0.26);
if (lookFrom === 'side') camera.position.set(0.03, 0.11, 0.36);
if (lookFrom === 'head-on') camera.position.set(0.42, 0.075, 0.0001);
if (lookFrom === 'rear') { camera.position.set(-0.16, 0.20, 0.22); }
const controls = new OrbitControls(camera, canvas);
controls.target.set(0, 0.06, 0);
controls.enableDamping = true;
controls.maxPolarAngle = Math.PI / 2 - 0.02;

scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8f94, 1.6));
const sun = new THREE.DirectionalLight(0xffffff, 2.2);
sun.position.set(-1.5, 3, 2);
scene.add(sun);

const floor = new THREE.Mesh(new THREE.PlaneGeometry(40, 40),
  new THREE.MeshStandardMaterial({ roughness: 1 }));
floor.rotation.x = -Math.PI / 2;
scene.add(floor);
let grid = null;
function theme() {
  scene.background = new THREE.Color(css('--bg'));
  floor.material.color.set(css('--floor'));
  if (grid) scene.remove(grid);
  grid = new THREE.Group();
  const fine = new THREE.GridHelper(10, 100, css('--line'), css('--line'));
  fine.material.opacity = 0.5; fine.material.transparent = true;
  const coarse = new THREE.GridHelper(10, 10, css('--muted'), css('--muted'));
  coarse.material.opacity = 0.35; coarse.material.transparent = true;
  fine.position.y = 0.0005; coarse.position.y = 0.001;
  grid.add(fine, coarse);
  scene.add(grid);
}
theme();
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', theme);

// Something for the onboard camera to look at: a start line and a few blocks.
const props = new THREE.Group();
const tape = new THREE.Mesh(new THREE.PlaneGeometry(0.02, 0.6),
  new THREE.MeshBasicMaterial({ color: 0xd9730d }));
tape.rotation.x = -Math.PI / 2; tape.position.set(0, 0.002, 0);
props.add(tape);
[[1.2, 0.0, 0x2f6fde], [1.0, 0.7, 0x2c8a4b], [0.3, -0.9, 0xc8372d], [-0.8, 0.5, 0xb7791f]]
  .forEach(([x, y, c]) => {
    const b = new THREE.Mesh(new THREE.BoxGeometry(0.12, 0.12, 0.12),
      new THREE.MeshStandardMaterial({ color: c, roughness: 0.8 }));
    b.position.set(x, 0.06, -y);
    props.add(b);
  });
scene.add(props);

const robot = new THREE.Group();
scene.add(robot);
const stand = new THREE.Mesh(new THREE.BoxGeometry(0.09, 0.03, 0.06),
  new THREE.MeshStandardMaterial({ color: 0x8a8f94, roughness: 0.9 }));
stand.position.set(-0.022, 0.015, 0); stand.visible = false;
scene.add(stand);

const trailGeom = new THREE.BufferGeometry();
const trail = new THREE.Line(trailGeom, new THREE.LineBasicMaterial({ color: 0x2f6fde }));
trail.frustumCulled = false;
scene.add(trail);

// onboard camera: lens centre from the CAD, looking forward
const eye = new THREE.PerspectiveCamera(100, 240 / 150, 0.01, 30);
eye.position.set(0.060, 0.0937, 0);
eye.rotation.y = -Math.PI / 2;
robot.add(eye);

let model = null, partMeta = {}, wheelL = [], wheelR = [], nodes = {};
const fallback = new THREE.Mesh(new THREE.BoxGeometry(0.134, 0.07, 0.096),
  new THREE.MeshStandardMaterial({ color: 0x57b947 }));
fallback.position.set(-0.022, 0.05, 0);
robot.add(fallback);

fetch('./model.json').then((r) => r.json()).then((m) => {
  partMeta = m.parts;
  if (m.camera) {                       // look out of the lens, at its real tilt
    eye.position.set(m.camera.lens[0] + 0.006, m.camera.lens[1], m.camera.lens[2]);
    eye.rotation.set(0, -Math.PI / 2, 0);
    eye.rotateX(m.camera.pitch_deg * Math.PI / 180);
  }
}).catch(() => {});
new GLTFLoader().load('./jetbot.glb', (g) => {
  robot.remove(fallback);
  model = g.scene;
  robot.add(model);
  model.traverse((o) => {
    if (!o.isMesh) return;
    let n = o;
    while (n && !/__\d+$/.test(n.name)) n = n.parent;
    const key = n ? n.name.replace(/__\d+$/, '') : 'other';
    const holder = n || o;
    (nodes[key] = nodes[key] || []).push(holder);
    holder.userData.home = holder.position.clone();
    o.userData.part = key;
    if (key === 'wheel_left') wheelL.push(holder);
    if (key === 'wheel_right') wheelR.push(holder);
  });
  const ex = new URLSearchParams(location.search).get('explode');   // e.g. ?explode=1
  if (ex) fetch('./model.json').then((r) => r.json()).then((mm) => { partMeta = mm.parts; $('explode').value = ex; explode(+ex); });
}, undefined, () => {});

// Pull the robot apart UPWARD, like lifting the layers off a stack: every
// part rises in proportion to its own height, so nothing goes through the
// floor, and the things that sit beside the stack move out sideways.
const SIDEWAYS = { wheel_left: [0, 0, -1], wheel_right: [0, 0, 1], motor_left: [0, 0, -0.55],
  motor_right: [0, 0, 0.55], fastener_left: [0, 0, -0.8], fastener_right: [0, 0, 0.8],
  camera: [1.2, 0, 0], camera_mount: [0.8, 0, 0], antenna_a: [-0.9, 0, -0.5], antenna_b: [-0.9, 0, 0.5],
  caster_a: [0.9, 0, 0], caster_b: [-0.9, 0, 0], base: [0, 0, 0], cells: [0, 0, 1.3] };
function explode(k) {
  for (const [key, list] of Object.entries(nodes)) {
    const meta = partMeta[key];
    if (!meta) continue;
    const side = SIDEWAYS[key] || [0, 0, 0];
    const rise = key === 'base' || key.startsWith('caster') ? 0 : meta.centre[1] * 1.9;
    const d = new THREE.Vector3(side[0] * 0.11, rise + 0.03, side[2] * 0.11).multiplyScalar(k);
    for (const n of new Set(list)) n.position.copy(n.userData.home).add(d);
  }
  // step back and look higher, so the top of the stack stays in the picture
  const lift = 0.15 * (k - exploded), back = 1 + 0.9 * k, was = 1 + 0.9 * exploded;
  const off = camera.position.clone().sub(controls.target).multiplyScalar(back / was);
  controls.target.y += lift;
  camera.position.copy(controls.target).add(off);
  exploded = k;
}
let exploded = 0;
$('explode').oninput = (e) => explode(+e.target.value);

// click a part to read what it is
const ray = new THREE.Raycaster(), mouse = new THREE.Vector2();
let downAt = null;
canvas.addEventListener('pointerdown', (e) => { downAt = [e.clientX, e.clientY]; });
canvas.addEventListener('pointerup', (e) => {
  if (!downAt || Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) > 4 || !model) return;
  mouse.set((e.clientX / innerWidth) * 2 - 1, -(e.clientY / innerHeight) * 2 + 1);
  ray.setFromCamera(mouse, camera);
  const hit = ray.intersectObject(model, true)[0];
  const box = $('info');
  if (!hit) { box.style.display = 'none'; return; }
  const meta = partMeta[hit.object.userData.part] || {};
  const s = (meta.size || [0, 0, 0]).map((v) => (v * 1000).toFixed(0));
  box.innerHTML = `<b>${meta.name || hit.object.userData.part}</b>${meta.desc || ''}` +
    `<div class="hint" style="margin-top:4px">${s[0]} × ${s[2]} × ${s[1]} mm, from Waveshare's CAD</div>`;
  box.style.display = 'block';
});

// ---------------------------------------------------------------- driving
const keys = new Set();
const KEYMAP = { w: 'w', a: 'a', s: 's', d: 'd', arrowup: 'w', arrowleft: 'a', arrowdown: 's', arrowright: 'd' };
addEventListener('keydown', (e) => {
  if (['INPUT', 'SELECT'].includes(document.activeElement.tagName) &&
      document.activeElement.type !== 'range' && document.activeElement.type !== 'checkbox') return;
  const k = KEYMAP[e.key.toLowerCase()];
  if (k) { keys.add(k); e.preventDefault(); }
});
addEventListener('keydown', (e) => {
  if (e.key.toLowerCase() === 'b' && !['INPUT', 'SELECT'].includes(document.activeElement.tagName)) location.href = './blueprint.html';
});
addEventListener('keyup', (e) => { const k = KEYMAP[e.key.toLowerCase()]; if (k) keys.delete(k); });
addEventListener('blur', () => keys.clear());
let wasDriving = false;
setInterval(() => {
  const s = +$('cmd').value;
  let f = (keys.has('w') ? 1 : 0) - (keys.has('s') ? 1 : 0);
  let t = (keys.has('a') ? 1 : 0) - (keys.has('d') ? 1 : 0);
  for (const k of 'wasd') $('k-' + k).classList.toggle('on', keys.has(k));
  if (!f && !t) { if (wasDriving) post({ drive: [0, 0] }); wasDriving = false; return; }
  let l, r;
  if (f && t) { l = f * s * (t > 0 ? 0.5 : 1); r = f * s * (t > 0 ? 1 : 0.5); }
  else if (f) { l = r = f * s; }
  else { l = -t * s; r = t * s; }
  wasDriving = true;
  post({ drive: [l, r] });
}, 100);
$('cmd').oninput = (e) => { $('cmd-val').textContent = (+e.target.value).toFixed(2); };
$('reset').onclick = () => post({ action: 'reset' });
let follow = true;
$('follow').onclick = () => { follow = !follow; $('follow').textContent = 'Follow: ' + (follow ? 'on' : 'off'); };
for (const f of ['lifted', 'held', 'charger']) {
  $('f-' + f).onchange = (e) => post({ action: 'flag', name: f, value: e.target.checked });
}
$('f-jetson').onchange = (e) => post({ action: 'flag', name: 'jetson', value: e.target.value });
$('soc').onchange = (e) => post({ action: 'set', name: 'soc0', value: +e.target.value }).then(loadDesign);
$('board-sel').onchange = (e) => post({ action: 'set', name: 'board', value: e.target.value }).then(loadDesign);
$('fault').onchange = (e) => post({ action: 'fault', value: e.target.value ? e.target.value.split(',') : null });
$('exp-run').onclick = () => post({ action: 'experiment', name: $('exp').value });
$('defaults').onclick = () => post({ action: 'defaults' }).then(loadDesign);
document.querySelectorAll('.tabs button').forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll('.tabs button, .tab').forEach((x) => x.classList.remove('on'));
    b.classList.add('on'); $(b.dataset.tab).classList.add('on');
  };
});

// ---------------------------------------------------------------- design info
let design = null;
function loadDesign() {
  return new Promise((res) => setTimeout(res, 150))
    .then(() => fetch('/design')).then((r) => r.json()).then((d) => {
      design = d;
      const sel = $('exp');
      if (!sel.options.length) {
        d.experiments.forEach((e) => sel.add(new Option(`${e.name}  (${e.duration.toFixed(0)} s)`, e.name)));
        sel.onchange = () => { $('exp-doc').textContent = d.experiments.find((e) => e.name === sel.value).doc; };
        sel.value = 'square'; sel.onchange();
      }
      const box = $('tun');
      box.innerHTML = '';
      d.tunable.forEach((t) => {
        const div = document.createElement('div');
        div.className = 'tun';
        const moved = Math.abs(t.value / t.default - 1) > 1e-6;
        div.innerHTML = `<div class="top"><code>${t.name === 'mass_override' ? 'mass' : t.name}</code>` +
          `<input type="number" step="any" value="${+t.value.toPrecision(4)}"></div>` +
          `<input type="range" min="${t.default * 0.3}" max="${t.default * 3}" step="${t.default / 100}" value="${t.value}">` +
          `<div class="src">${moved ? `was ${+t.default.toPrecision(4)} · ` : ''}${t.src}</div>`;
        const [num, rng] = div.querySelectorAll('input');
        const send = (v) => post({ action: 'set', name: t.name, value: +v }).then(loadDesign);
        num.onchange = () => send(num.value);
        rng.oninput = () => { num.value = (+rng.value).toPrecision(4); };
        rng.onchange = () => send(rng.value);
        box.appendChild(div);
      });
    }).catch(() => {});
}
loadDesign();

// ---------------------------------------------------------------- board diagram
// Laid out by hand to follow the schematic's power path, top to bottom.
const NODES = [
  { id: 'cells', x: 8, y: 8, w: 104, comp: ['cell1', 'cell2', 'cell3', 'holder'], title: 'Cells 3 × 18650',
    lines: (s) => [`${fmt(s['cell1.v'] + s['cell2.v'] + s['cell3.v'])} V  ${fmt(s['cell1.soc'] * 100, 0)} %`, `${fmt(s['cell1.i'])} A out`] },
  { id: 'prot', x: 122, y: 8, w: 104, comp: ['protector', 'R15'], title: 'Protection',
    lines: (s, f) => [`S-8254 · ${f.protector}`, `low cell ${fmt(s['protector.cell_min'])} V`] },
  { id: 'chg', x: 236, y: 8, w: 104, comp: ['charger', 'R16', 'ina219'], title: 'Charger · INA219',
    lines: (s) => [`reads ${fmt(s['ina219.bus_v'])} V`, `charge ${fmt(s['ina219.current'])} A`] },
  { id: 's1', x: 122, y: 84, w: 104, comp: ['S1'], title: 'S1 switch',
    lines: (s) => [`${fmt(s['S1.i'])} A`, `drops ${fmt(s['S1.v'] * 1000, 0)} mV`] },
  { id: 'buck', x: 8, y: 178, w: 104, comp: ['U3'], title: 'U3 APW7313',
    lines: (s, f) => [`${fmt(s['U3.v_out'], 3)} V  ${fmt(s['U3.i_out'])} A`, `${fmt(s['U3.eff'] * 100, 0)} %  ${fmt(s['U3.T'], 0)} °C${f.buck === 'reg' ? '' : ' ' + f.buck}`] },
  { id: 'jet', x: 8, y: 254, w: 104, comp: ['jetson'], title: 'Jetson Nano',
    lines: (s) => [`${fmt(s['jetson.v'], 3)} V`, `${fmt(s['jetson.p'], 1)} W`] },
  { id: 'pca', x: 8, y: 330, w: 104, comp: ['pca9685', 'oled'], title: 'PCA9685',
    lines: (s, f) => [`${fmt(f.pwm_hz, 0)} Hz PWM`, `3V3 ${fmt(s['jetson.i_3v3'] * 1000, 0)} mA`] },
  { id: 'dl', x: 122, y: 178, w: 104, comp: ['drive_left'], title: 'Driver L',
    lines: (s) => [`duty ${fmt(s['drive_left.duty'])}  ${fmt(s['drive_left.T'], 0)} °C`, `${fmt(s['drive_left.i_rms'])} A rms`] },
  { id: 'dr', x: 236, y: 178, w: 104, comp: ['drive_right'], title: 'Driver R',
    lines: (s) => [`duty ${fmt(s['drive_right.duty'])}  ${fmt(s['drive_right.T'], 0)} °C`, `${fmt(s['drive_right.i_rms'])} A rms`] },
  { id: 'ml', x: 122, y: 254, w: 104, comp: [], title: 'Motor L',
    lines: (s) => [`${fmt(s['drive_left.omega'] * 9.549, 0)} rpm`, `${fmt(s['drive_left.torque'] * 1000, 0)} mN·m`] },
  { id: 'mr', x: 236, y: 254, w: 104, comp: [], title: 'Motor R',
    lines: (s) => [`${fmt(s['drive_right.omega'] * 9.549, 0)} rpm`, `${fmt(s['drive_right.torque'] * 1000, 0)} mN·m`] },
];
const H = 56;
const WIRES = [
  { d: 'M112 36 H122', i: (s) => s['protector.i'] },
  { d: 'M226 36 H236', i: (s) => -s['ina219.current'] },
  { d: 'M174 64 V84', i: (s) => s['S1.i'] },
  { d: 'M174 140 V158', i: (s) => s['S1.i'] },
  { d: 'M60 158 H288', i: () => 0, rail: true },
  { d: 'M60 158 V178', i: (s) => s['U3.i_in'] },
  { d: 'M174 158 V178', i: (s) => s['drive_left.i_supply'] },
  { d: 'M288 158 V178', i: (s) => s['drive_right.i_supply'] },
  { d: 'M60 234 V254', i: (s) => s['U3.i_out'] },
  { d: 'M60 310 V330', i: (s) => s['jetson.i_3v3'] * 20 },
  { d: 'M174 234 V254', i: (s) => s['drive_left.i'] },
  { d: 'M288 234 V254', i: (s) => s['drive_right.i'] },
  { d: 'M112 358 H130 V246 H122', i: () => 0, ctl: true },
];
const svg = $('board');
const NS = 'http://www.w3.org/2000/svg';
const el = (tag, attrs, parent) => {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  (parent || svg).appendChild(e);
  return e;
};
WIRES.forEach((w) => {
  el('path', { d: w.d, class: 'wire', 'stroke-dasharray': w.ctl ? '2 3' : '' });
  if (!w.rail && !w.ctl) w.flow = el('path', { d: w.d, class: 'flow' });
  w.off = 0;
});
const railLabel = el('text', { x: 340, y: 152, class: 'rail', 'text-anchor': 'end' });
NODES.forEach((n) => {
  n.g = el('g', { class: 'node' });
  el('rect', { x: n.x, y: n.y, width: n.w, height: H }, n.g);
  el('text', { x: n.x + 8, y: n.y + 16, class: 't' }, n.g).textContent = n.title;
  n.t = [0, 1].map((k) => el('text', { x: n.x + 8, y: n.y + 32 + 14 * k, class: 'v' }, n.g));
});
const legend = el('text', { x: 8, y: 420, class: 'rail' });
const legend2 = el('text', { x: 8, y: 436, class: 'rail' });
const legend3 = el('text', { x: 8, y: 452, class: 'rail' });

function drawBoard(m, dtSec) {
  const s = m.sig;
  const bad = {}, warn = {};
  m.limits.forEach((l) => {
    if (!l.violated) return;
    (l.severity === 'error' ? bad : warn)[l.label.split('.')[0]] = true;
  });
  NODES.forEach((n) => {
    const lines = n.lines(s, m);
    n.t.forEach((t, k) => { t.textContent = lines[k] || ''; });
    const isBad = n.comp.some((c) => bad[c]) || n.comp.some((c) => s[c + '.failed'] > 0);
    n.g.setAttribute('class', 'node' + (isBad ? ' bad' : n.comp.some((c) => warn[c]) ? ' warn' : ''));
  });
  WIRES.forEach((w) => {
    if (!w.flow) return;
    const i = w.i(s) || 0;
    w.off -= Math.sign(i) * Math.min(60, 8 + Math.abs(i) * 40) * dtSec;
    w.flow.style.strokeDashoffset = w.off;
    w.flow.style.opacity = Math.abs(i) < 0.005 ? 0 : Math.min(1, 0.35 + Math.abs(i) * 0.5);
  });
  railLabel.textContent = `12VCC ${fmt(s['U3.v_in'])} V`;
  const dec = m.board === 'drv8870' ? 'drive / coast (fast decay): IN1 is PWMed, IN2 held low'
    : 'drive / brake (slow decay): PWM pin toggled, direction pins held';
  legend.textContent = `${m.board}: ${dec}`;
  legend2.textContent = `left winding: ${fmt(s['drive_left.ripple'])} A ripple, current ${s['drive_left.continuous'] ? 'continuous' : 'stops each period'}`;
  legend3.textContent = `tyre slip L ${fmt(m.forces.slip_l, 3)} R ${fmt(m.forces.slip_r, 3)} m/s · caster load ${fmt(m.forces.Nc, 2)} N`;
  $('board-note').textContent = 'Dashes move with the current. A node outlined in red has a part over a rating; see Ratings.';
}

function drawLimits(m) {
  const rows = m.limits.filter((l) => isFinite(l.margin))
    .sort((a, b) => (b.violated - a.violated) || (a.margin - b.margin)).slice(0, 26);
  $('limits').innerHTML = rows.map((l) => {
    const used = Math.max(0, Math.min(1, 1 - l.margin));
    const cls = l.violated ? (l.severity === 'error' ? 'bad' : 'warn') : '';
    return `<tr class="${cls}"><td>${l.label}<div class="bar"><i style="width:${(used * 100).toFixed(0)}%"></i></div>` +
      `${l.violated && l.why ? `<div class="hint">${l.why}</div>` : ''}</td>` +
      `<td class="n">${fmt(l.worst, 3)}</td></tr>`;
  }).join('');
  const nb = m.limits.filter((l) => l.violated && l.severity === 'error').length;
  const nw = m.limits.filter((l) => l.violated && l.severity === 'warn').length;
  const chip = $('c-limits');
  chip.textContent = nb ? `${nb} rating${nb > 1 ? 's' : ''} exceeded` : nw ? `${nw} warning${nw > 1 ? 's' : ''}` : 'ratings ok';
  chip.className = 'chip' + (nb ? ' bad' : nw ? ' warn' : '');
}

// ---------------------------------------------------------------- charts
const WINDOW = 12;                          // seconds shown
class Chart {
  constructor(id, series, unit) {
    this.c = $(id); this.series = series; this.unit = unit; this.data = [];
  }
  push(t, values) {
    this.data.push([t, ...values]);
    while (this.data.length && this.data[0][0] < t - WINDOW) this.data.shift();
    if (this.data.length > 1 && this.data[this.data.length - 2][0] > t) this.data = [[t, ...values]];
  }
  draw() {
    const c = this.c, dpr = Math.min(devicePixelRatio, 2);
    const w = c.clientWidth, h = c.clientHeight;
    if (c.width !== w * dpr) { c.width = w * dpr; c.height = h * dpr; }
    const g = c.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    if (this.data.length < 2) return;
    const padL = 34, padR = 78, padT = 6, padB = 14;
    let lo = Infinity, hi = -Infinity;
    for (const row of this.data) for (let k = 1; k < row.length; k++) { lo = Math.min(lo, row[k]); hi = Math.max(hi, row[k]); }
    const span = Math.max(hi - lo, this.minSpan || 0.1);
    const mid = (hi + lo) / 2; lo = mid - span * 0.58; hi = mid + span * 0.58;
    const t1 = this.data[this.data.length - 1][0], t0 = t1 - WINDOW;
    const X = (t) => padL + (t - t0) / WINDOW * (w - padL - padR);
    const Y = (v) => padT + (hi - v) / (hi - lo) * (h - padT - padB);
    g.font = '10px ui-monospace, Menlo, monospace';
    g.fillStyle = css('--muted'); g.strokeStyle = css('--line'); g.lineWidth = 1;
    for (const v of [lo + span * 0.08, mid, hi - span * 0.08]) {
      g.beginPath(); g.moveTo(padL, Y(v)); g.lineTo(w - padR, Y(v)); g.stroke();
      g.textAlign = 'right'; g.fillText(v.toFixed(Math.abs(v) < 10 ? 2 : 1), padL - 4, Y(v) + 3);
    }
    if (lo < 0 && hi > 0) { g.strokeStyle = css('--muted'); g.beginPath(); g.moveTo(padL, Y(0)); g.lineTo(w - padR, Y(0)); g.stroke(); }
    g.textAlign = 'left';
    g.fillText(`last ${WINDOW} s`, padL, h - 2);
    const ends = [];
    this.series.forEach((s, k) => {
      g.strokeStyle = css(s.color) || s.color; g.lineWidth = 1.6;
      g.setLineDash(s.dash || []);
      g.beginPath();
      this.data.forEach((row, i) => { const x = X(row[0]), y = Y(row[k + 1]); i ? g.lineTo(x, y) : g.moveTo(x, y); });
      g.stroke(); g.setLineDash([]);
      ends.push({ y: Y(this.data[this.data.length - 1][k + 1]), s, v: this.data[this.data.length - 1][k + 1] });
    });
    ends.sort((a, b) => a.y - b.y);                // keep end labels from colliding
    for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 11) ends[i].y = ends[i - 1].y + 11;
    ends.forEach((e) => {
      g.fillStyle = css(e.s.color) || e.s.color;
      g.fillRect(w - padR + 4, e.y - 3, 6, 6);
      g.fillStyle = css('--ink');
      g.fillText(`${e.s.name} ${e.v.toFixed(2)}`, w - padR + 13, e.y + 3);
    });
  }
}
const chV = new Chart('ch-v', [{ name: 'V', color: '--accent' }]);
chV.minSpan = 0.3;
const chI = new Chart('ch-i', [{ name: 'pack', color: '--muted' }, { name: 'L', color: '--accent' },
  { name: 'R', color: '--accent-2', dash: [4, 3] }]);
chI.minSpan = 0.5;
const chM = new Chart('ch-m', [{ name: 'robot', color: '--series-3' }, { name: 'L tyre', color: '--accent' },
  { name: 'R tyre', color: '--accent-2', dash: [4, 3] }]);
chM.minSpan = 0.4;

// ---------------------------------------------------------------- the stream
let latest = null, lastT = 0;
const es = new EventSource('/stream');
es.onmessage = (ev) => {
  const m = JSON.parse(ev.data);
  if (m.error) { $('err').textContent = 'the twin stopped: ' + m.error; $('err').style.display = 'block'; return; }
  $('err').style.display = 'none';
  latest = m;
  const t = m.tel;
  chV.push(m.t, [t.bus_v]);
  chI.push(m.t, [t.i_pack, t.i_l, t.i_r]);
  chM.push(m.t, [t.v, t.rim_l, t.rim_r]);
};
es.onerror = () => { $('err').textContent = 'lost the twin: is `python -m jetbot_twin serve` still running?'; $('err').style.display = 'block'; };

function resize() {
  renderer.setSize(innerWidth, innerHeight, false);
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
}
addEventListener('resize', resize);
resize();

let prevFrame = performance.now(), synced = false;
function frame(now) {
  const dtSec = Math.min(0.1, (now - prevFrame) / 1000); prevFrame = now;
  const m = latest;
  if (m) {
    const t = m.tel;
    const lift = m.flags.lifted ? 0.03 : 0;
    const before = robot.position.clone();
    robot.position.set(t.x, lift, -t.y);
    robot.rotation.y = t.th;
    // The floor is finite and the drive is not: keep it under the robot. It
    // moves in whole metres, one coarse square, so it looks like it stands still.
    floor.position.set(Math.round(t.x), 0, Math.round(-t.y));
    if (grid) grid.position.copy(floor.position);
    stand.visible = m.flags.lifted;
    stand.position.set(t.x - 0.022 * Math.cos(t.th), 0.015, -t.y + 0.022 * Math.sin(t.th));
    stand.rotation.y = t.th;
    wheelL.forEach((w) => { w.rotation.z = -t.phi_l; });
    wheelR.forEach((w) => { w.rotation.z = -t.phi_r; });
    if (follow) {
      const d = robot.position.clone().sub(before); d.y = 0;
      if (d.length() < 0.5) { camera.position.add(d); controls.target.add(d); }
      else { controls.target.set(t.x, 0.06, -t.y); camera.position.set(t.x - 0.45, 0.38, -t.y + 0.55); }
    }
    const pts = m.trail.map(([x, y]) => new THREE.Vector3(x, 0.003, -y));
    trailGeom.setFromPoints(pts);
    $('c-board').textContent = m.board === 'drv8870' ? '2 × DRV8870' : 'TB6612FNG';
    $('c-time').textContent = `t ${m.t.toFixed(1)} s`;
    $('c-pose').textContent = `x ${t.x.toFixed(2)}  y ${t.y.toFixed(2)}  ${(t.th * 180 / Math.PI).toFixed(0)}°`;
    $('c-state').textContent = m.script ? `running ${m.script}` : `cmd ${t.cmd_l.toFixed(2)} / ${t.cmd_r.toFixed(2)}`;
    if (!synced) {
      $('board-sel').value = m.board; $('f-jetson').value = m.flags.jetson;
      for (const f of ['lifted', 'held', 'charger']) $('f-' + f).checked = m.flags[f];
      synced = true;
    }
    if (m.t !== lastT) {
      drawBoard(m, dtSec); lastT = m.t;
      drawLimits(m);
    }
  }
  controls.update();
  renderer.setScissorTest(false);
  renderer.setViewport(0, 0, innerWidth, innerHeight);
  renderer.render(scene, camera);
  // the onboard view, drawn into the framed box bottom-left
  const box = $('cam').getBoundingClientRect();
  renderer.setScissorTest(true);
  renderer.setViewport(box.left, innerHeight - box.bottom, box.width, box.height);
  renderer.setScissor(box.left, innerHeight - box.bottom, box.width, box.height);
  if (model) model.visible = false;
  trail.visible = false;
  renderer.render(scene, eye);
  if (model) model.visible = true;
  trail.visible = true;
  chV.draw(); chI.draw(); chM.draw();
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
