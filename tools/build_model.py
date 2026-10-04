"""Turn the manufacturer's STEP into what the twin draws and exports.

    .venv/bin/python tools/build_model.py

Reads   reference/jetbot.STEP          Waveshare's own assembly
Writes  web/jetbot.glb                 named parts, for the browser
        web/model.json                 what each part is, and where
        export/meshes/*.stl            body and wheel, for URDF / SDF

Nothing is redrawn: every triangle comes from Waveshare's solids. The only
things added are names (theirs are in a character set that does not survive
the file format), colours, and the change of frame:

    STEP    x backward, y up,   z left,  mm, origin mid-chassis
    twin    x forward,  y left, z up,    m,  origin on the floor under the axle
    glTF    x forward,  y up,   z right, m,  same origin
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import trimesh
from build123d import Compound

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inspect_step import leaves, load  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
AXLE_X, FLOOR_Y = -22.0, -52.85          # STEP coordinates of the twin's origin

# (group, description, colour, printed name) chosen by what the label contains
RULES = [
    ("wheel", "wheel", "Wheel", "65 mm wheel, rubber tyre on a plastic hub", "#2a2a2a"),
    ("Motor_Gear_Box", "motor", "Gearmotor", "TT gearmotor, 1:48", "#d9b23a"),
    ("M3_X_30", "fastener", "Motor bolts", "M3 x 30 through the gearbox", "#9aa0a6"),
    ("____-0_STEP", "chassis", "Chassis", "Folded aluminium box, anodised green", "#57b947"),
    ("____-1_STEP", "base", "Base plate", "Bottom cover", "#4aa23c"),
    ("____PCB_STEP / _______STEP", "battery", "Cell holder", "3 x 18650 holder", "#1c1c1c"),
    ("____PCB_STEP / PCB_STEP", "board", "Expansion board",
     "Waveshare JetBot expansion board: protection, 5 V buck, motor driver, PCA9685", "#1f6b3a"),
    ("1_14_TFT", "board", "OLED", "0.91 in 128 x 32 SSD1306 display", "#101418"),
    ("12V_STEP", "board", "DC jack", "12.6 V charger input", "#151515"),
    ("____PCB_STEP", "board", "Power switch", "S1 slide switch", "#151515"),
    ("244-63448", "heatsink", "Heatsink", "Jetson Nano module and heatsink", "#3a3d42"),
    ("Jetson_nano", "jetson", "Jetson Nano", "Jetson Nano developer kit carrier", "#1d5b34"),
    ("PORG_", "jetson", "Jetson Nano", "Jetson Nano developer kit carrier", "#9aa0a6"),
    ("M2_WIFI", "jetson", "Wireless card", "Intel 8265 M.2 card under the module", "#1d5b34"),
    ("Fan-40x40", "fan", "Fan", "40 mm fan on the heatsink", "#141414"),
    ("Raspberry_Pi_Camera", "camera", "Camera", "IMX219-160, 8 MP, 160 degree lens", "#161616"),
    ("FFC", "camera", "Camera cable", "CSI ribbon", "#c9c2a8"),
    ("___________STEP", "camera_mount", "Camera mount", "Aluminium camera bracket", "#57b947"),
    ("______-3_STEP", "camera_mount", "Camera plate", "Camera backing plate", "#2b2b2b"),
    ("Antenna", "antenna", "Antenna", "Dual-band Wi-Fi antenna", "#111111"),
    ("Wifi_antena_cable", "antenna", "Antenna lead", "U.FL pigtail to the wireless card", "#222222"),
    ("Caster_ball", "caster", "Caster", "Ball caster", "#c7ccd1"),
    ("______-4_STEP", "standoff", "Standoff", "Brass standoff under the Jetson", "#c9a44c"),
    ("______-2_STEP", "standoff", "Spacer", "Board spacer", "#c9a44c"),
    ("SCREWS", "fastener", "Screw", "Fastener", "#9aa0a6"),
]


# A node made of many solids is named for the whole, not for whichever came first.
NODE_NAMES = {
    "jetson": ("Jetson Nano", "Jetson Nano developer kit: carrier board, connectors, wireless card"),
    "heatsink": ("Heatsink", "Jetson Nano module under its heatsink"),
    "fastener": ("Screws", "Fasteners"),
    "standoff": ("Standoffs", "Brass standoffs and spacers between the boards"),
    "camera": ("Camera", "IMX219-160, 8 MP, 160 degree lens, and its ribbon"),
    "camera_mount": ("Camera mount", "Hinged aluminium bracket: folded upright, pivot "
                     "screw, tilting plate, spacer. Modelled from a photograph of "
                     "this robot, not from Waveshare's CAD, which shows a fixed one"),
    "antenna": ("Antenna", "Dual-band Wi-Fi antenna and its lead"),
    "wheel": ("Wheel", "65 mm wheel, rubber tyre on a plastic hub"),
}


def classify(path):
    s = " / ".join(path)
    for key, group, name, desc, colour in RULES:
        if key in s:
            return group, name, desc, colour
    return "other", "Part", "", "#808080"


def to_gltf(v):
    """STEP mm -> glTF m, x forward, y up, z right."""
    out = np.empty_like(v)
    out[:, 0] = -(v[:, 0] - AXLE_X)
    out[:, 1] = v[:, 1] - FLOOR_Y
    out[:, 2] = -v[:, 2]
    return out / 1000.0


def gltf_to_twin(v):
    """glTF (x fwd, y up, z right) -> twin (x fwd, y left, z up)."""
    return np.column_stack([v[:, 0], -v[:, 2], v[:, 1]])


def mesh_of(shape, tol=0.12, ang=0.45):
    verts, tris = Compound(shape).tessellate(tol, ang)
    if not tris:
        return None
    v = np.array([[p.X, p.Y, p.Z] for p in verts], float)
    return v, np.array(tris, int)


def simplify(v, f, reduction):
    try:
        import fast_simplification
    except ImportError:
        return v, f
    v2, f2 = fast_simplification.simplify(np.asarray(v, np.float32),
                                          np.asarray(f), target_reduction=reduction)
    return np.asarray(v2, float), np.asarray(f2, int)


def rgba(hexcolour):
    h = hexcolour.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)] + [255]


CACHE = ROOT / "reference" / "mesh_cache.npz"


def tessellated():
    """Every leaf as (label path, vertices, triangles), in STEP coordinates.

    Meshing Waveshare's solids takes about ten minutes, nearly all of it on
    a handful of Jetson connectors, so the result is kept. Delete
    reference/mesh_cache.npz to redo it.
    """
    if CACHE.exists():
        z = np.load(CACHE, allow_pickle=False)
        paths = json.loads(str(z["paths"]))
        return [(p, z[f"v{k}"], z[f"f{k}"]) for k, p in enumerate(paths)]
    t0 = time.time()
    asm = load()
    print(f"STEP read in {time.time() - t0:.0f} s", flush=True)
    out = []
    all_leaves = leaves(asm)
    for n_leaf, (path, shape) in enumerate(all_leaves):
        if n_leaf % 60 == 0:
            print(f"  meshing {n_leaf}/{len(all_leaves)}  "
                  f"({time.time() - t0:.0f} s)", flush=True)
        m = mesh_of(shape)
        if m is not None:
            out.append((path, m[0], m[1]))
    arrays = {"paths": np.array(json.dumps([p for p, _, _ in out]))}
    for k, (_, v, f) in enumerate(out):
        arrays[f"v{k}"], arrays[f"f{k}"] = v.astype(np.float32), f.astype(np.int32)
    np.savez_compressed(CACHE, **arrays)
    return out


# --------------------------------------------------------------------------
# The camera mount. Waveshare's CAD has a fixed bracket; the kit as shipped
# (and as photographed on this robot, 2026-10-04) has a HINGED one: a folded
# upright, a pivot screw, a small tilting plate, a yellow-green spacer, and
# the camera on that. The CAD bracket is dropped and this one built in its
# place. Its proportions are taken from the photograph against the 25 mm
# camera board, not from a drawing: it is a likeness, not a measurement.
# --------------------------------------------------------------------------
CAM_PITCH_DEG = 0.0        # lens axis above horizontal; negative looks down.
                           # ASSUMED level, from the photograph. Set to yours.
GREEN, SPACER, BLACK = "#57b947", "#c5d93a", "#151515"


def rot_z(points, centre, deg):
    """Rotate glTF points about a z-parallel axis through `centre` (x, y)."""
    a = np.radians(deg)
    c, s_ = np.cos(a), np.sin(a)
    out = points.copy()
    dx, dy = points[:, 0] - centre[0], points[:, 1] - centre[1]
    out[:, 0] = centre[0] + c * dx - s_ * dy
    out[:, 1] = centre[1] + s_ * dx + c * dy
    return out


def camera_frame(leaves):
    """Where the CAD puts the camera board, and which way it faces."""
    board = lens = None
    for path, v, _ in leaves:
        if "Raspberry_Pi_Camera" not in " ".join(path):
            continue
        g = to_gltf(np.asarray(v, float))
        size = g.max(0) - g.min(0)
        if abs(size[2] - 0.025) < 0.001 and board is None:
            board = g
        if abs(size[2] - 0.010) < 0.001:
            lens = g
    c = (board.min(0) + board.max(0)) / 2
    # the board is a thin plate turning about z: its normal is the short
    # direction of its x-y footprint
    xy = board[:, :2] - c[:2]
    w, vecs = np.linalg.eigh(np.cov(xy.T))
    n = vecs[:, 0]
    lc = (lens.min(0) + lens.max(0)) / 2
    if np.dot(lc[:2] - c[:2], n) < 0:
        n = -n                                     # normal points out of the lens
    pitch = np.degrees(np.arctan2(n[1], n[0]))
    return c, n, pitch, lc


def box(size, centre, deg=0.0, pivot=None):
    m = trimesh.creation.box(extents=size)
    v = np.asarray(m.vertices) + np.asarray(centre)
    if deg:
        v = rot_z(v, pivot, deg)
    return v, np.asarray(m.faces)


def barrel(radius, length, centre):
    m = trimesh.creation.cylinder(radius=radius, height=length, sections=28)
    return np.asarray(m.vertices) + np.asarray(centre), np.asarray(m.faces)


def hinged_mount(c, n, turn):
    """The mount, as {colour: [(vertices, faces), ...]}, and the pivot."""
    t = np.array([-n[1], n[0]])                    # along the board, upward
    back = lambda d: np.array([c[0] - n[0] * d, c[1] - n[1] * d, 0.0])
    deg = np.degrees(np.arctan2(n[1], n[0]))
    pivot = back(0.0115)[:2] - t * 0.004           # just behind and below centre
    pieces = {SPACER: [], GREEN: [], BLACK: []}
    # yellow-green spacer, then the tilting plate, both lying on the board
    pieces[SPACER].append(box((0.003, 0.027, 0.027), back(0.0022), deg, back(0.0022)[:2]))
    pieces[GREEN].append(box((0.002, 0.027, 0.027), back(0.0047), deg, back(0.0047)[:2]))
    # its two ears, reaching back to the pivot
    for z in (-0.0095, 0.0095):
        ear = back(0.0085) + np.array([-t[0] * 0.004, -t[1] * 0.004, z])
        pieces[GREEN].append(box((0.008, 0.009, 0.002), ear, deg, ear[:2]))
    moved = {k: [(rot_z(v, pivot, turn), f) for v, f in lst] for k, lst in pieces.items()}
    px, py = rot_z(np.array([[pivot[0], pivot[1], 0.0]]), pivot, turn)[0][:2]
    # fixed parts: the pivot barrel, its screw, and the folded upright
    moved[GREEN].append(barrel(0.0042, 0.017, (px, py, 0.0)))
    for z in (-0.0125, 0.0125):
        moved[GREEN].append(box((0.014, 0.011, 0.002), (px - 0.004, py, z)))
        moved[BLACK].append(barrel(0.0026, 0.002, (px, py, z + np.sign(z) * 0.0018)))
    floor_y = 0.0529                               # top of the chassis box
    h = py - floor_y + 0.004
    xb = px - 0.011
    moved[GREEN].append(box((0.002, h, 0.027), (xb, floor_y + h / 2, 0.0)))
    for z in (-0.0125, 0.0125):
        moved[GREEN].append(box((0.012, h, 0.002), (xb + 0.005, floor_y + h / 2, z)))
    moved[GREEN].append(box((0.014, 0.002, 0.027), (xb + 0.006, floor_y + 0.001, 0.0)))
    return moved, pivot


def main():
    parts = {}                     # node name -> dict(meshes by colour, meta)
    all_leaves = tessellated()
    cam_c, cam_n, cad_pitch, lens_c = camera_frame(all_leaves)
    turn = CAM_PITCH_DEG - cad_pitch               # CAD tilt -> the tilt wanted
    mount, cam_pivot = hinged_mount(cam_c, cam_n, turn)
    for path, v, f in all_leaves:
        v, f = np.asarray(v, float), np.asarray(f, int)
        group, name, desc, colour = classify(path[1:])
        if group == "camera_mount":
            continue                               # replaced, see above
        side = ""
        cz = v[:, 2].mean()
        if group in ("wheel", "motor") or (group == "fastener" and "M3_X_30" in " ".join(path)):
            side = "_left" if cz > 0 else "_right"
        if group == "wheel":
            # tyre and hub are separate solids: dark tyre, white hub
            is_tyre = (v[:, 0].max() - v[:, 0].min()) > 60 and "ty" in " ".join(path).lower()
            colour = "#262626" if is_tyre else "#f1f1ee"
        if group == "antenna" or group == "caster":
            side = "_a" if (cz > 0 if group == "antenna" else v[:, 0].mean() < 0) else "_b"
        if group == "board" and name != "Expansion board":
            node = "board"                       # small parts ride with the board
        else:
            node = group + side
        p = parts.setdefault(node, {"group": group, "name": name, "desc": desc,
                                    "by_colour": {}})
        if name == "Expansion board":
            p["name"], p["desc"] = name, desc
        vs, fs = p["by_colour"].setdefault(colour, ([], []))
        off = sum(len(a) for a in vs)
        g = to_gltf(v)
        if group == "camera":
            g = rot_z(g, cam_pivot, turn)
        vs.append(g)
        fs.append(f + off)

    p = parts["camera_mount"] = {
        "group": "camera_mount", "name": "Camera mount", "by_colour": {},
        "desc": "Hinged aluminium bracket with a tilting plate and spacer"}
    for colour, lst in mount.items():
        vs, fs = p["by_colour"].setdefault(colour, ([], []))
        for v, f in lst:
            off = sum(len(a) for a in vs)
            vs.append(v)
            fs.append(f + off)

    scene = trimesh.Scene()
    meta = {"frame": "glTF: x forward, y up, z right, metres; origin on the "
                     "floor under the middle of the axle", "parts": {}}
    body_v, body_f = [], []
    wheel_stl = None
    tri_total = 0
    for node, p in sorted(parts.items()):
        allv = np.vstack([np.vstack(vs) for vs, _ in p["by_colour"].values()])
        lo, hi = allv.min(0), allv.max(0)
        centre = (lo + hi) / 2
        pivot = centre if p["group"] == "wheel" else np.zeros(3)
        for k, (colour, (vs, fs)) in enumerate(sorted(p["by_colour"].items())):
            v, f = np.vstack(vs), np.vstack(fs)
            # Do NOT thin these meshes. The Jetson is 380 small separate
            # solids, and a decimator run over the lot collapses the small
            # ones into holes: the board ends up looking like lace.
            tm = trimesh.Trimesh(v - pivot, f, process=False)
            tm.merge_vertices()
            metal = p["group"] in ("chassis", "base", "camera_mount", "standoff",
                                   "fastener", "caster", "heatsink")
            tm.visual = trimesh.visual.TextureVisuals(
                material=trimesh.visual.material.PBRMaterial(
                    baseColorFactor=rgba(colour),
                    metallicFactor=0.55 if metal else 0.0,
                    roughnessFactor=0.45 if metal else 0.75,
                    doubleSided=True))
            tri_total += len(f)
            geom = f"{node}__{k}"
            T = np.eye(4)
            T[:3, 3] = pivot
            scene.add_geometry(tm, node_name=geom, geom_name=geom,
                               parent_node_name=None, transform=T)
            if p["group"] == "wheel":
                if node == "wheel_left":
                    wheel_stl = (wheel_stl or []) + [(v - pivot, f)]
            else:
                off = sum(len(a) for a in body_v)
                body_v.append(v)
                body_f.append(f + off)
        name, desc = NODE_NAMES.get(p["group"], (p["name"], p["desc"]))
        meta["parts"][node] = {
            "group": p["group"], "name": name, "desc": desc,
            "centre": centre.round(5).tolist(),
            "size": (hi - lo).round(5).tolist(),
            "pieces": len(p["by_colour"]),
        }

    lens = rot_z(lens_c[None, :], cam_pivot, turn)[0]
    meta["camera"] = {"lens": lens.round(5).tolist(), "pitch_deg": CAM_PITCH_DEG,
                      "cad_pitch_deg": round(float(cad_pitch), 2)}
    print(f"camera: CAD has it at {cad_pitch:+.1f} deg; set to {CAM_PITCH_DEG:+.1f} deg; "
          f"lens at x {lens[0] * 1e3:.1f}, y {lens[1] * 1e3:.1f} mm")
    (ROOT / "web").mkdir(exist_ok=True)
    glb = ROOT / "web" / "jetbot.glb"
    glb.write_bytes(scene.export(file_type="glb"))
    (ROOT / "web" / "model.json").write_text(json.dumps(meta, indent=1))

    mesh_dir = ROOT / "export" / "meshes"
    mesh_dir.mkdir(parents=True, exist_ok=True)
    axle_h = meta["parts"]["wheel_left"]["centre"][1]
    bv = gltf_to_twin(np.vstack(body_v))
    bv[:, 2] -= axle_h                              # base_link is at the axle
    body = trimesh.Trimesh(bv, np.vstack(body_f), process=False)
    print(f"  body mesh for export: {len(body.faces)} triangles", flush=True)
    v2, f2 = simplify(body.vertices, body.faces,          # lighter, for other simulators
                      max(0.0, 1 - 80000 / len(body.faces)))
    trimesh.Trimesh(v2, f2, process=False).export(mesh_dir / "body.stl")
    off, wv, wf = 0, [], []
    for v, f in wheel_stl:
        wv.append(gltf_to_twin(v))
        wf.append(f + off)
        off += len(v)
    trimesh.Trimesh(np.vstack(wv), np.vstack(wf), process=False).export(
        mesh_dir / "wheel.stl")

    print(f"{len(parts)} named parts, {tri_total} triangles, "
          f"{glb.stat().st_size / 1e6:.1f} MB -> {glb}")
    for node, m in meta["parts"].items():
        c = m["centre"]
        print(f"  {node:<14} {m['name']:<16} at x {c[0] * 1e3:+7.1f} "
              f"y {c[1] * 1e3:6.1f} z {c[2] * 1e3:+7.1f} mm")


if __name__ == "__main__":
    main()
