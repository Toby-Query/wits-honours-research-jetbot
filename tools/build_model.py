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
# single flat sheet standing on edge at the front of the chassis, facing
# forward and wholly ahead of both circuit boards, whose two edges fold
# forward at the top into arms; a pivot screw through those arms; a small tilting bracket; a yellow-green
# spacer; and the camera on that, overhanging the front of the robot. The CAD bracket is dropped and this one built in its
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


def inside_box(points, corners, shrink=0.0003):
    """Which points lie inside a box given by its eight corners (any
    orientation), ignoring a skin `shrink` deep so that touching is allowed."""
    o = corners.mean(0)
    hit = np.ones(len(points), bool)
    for far in (1, 2, 4):                      # trimesh orders corners in binary
        e = corners[far] - corners[0]
        half = np.linalg.norm(e) / 2
        hit &= np.abs((points - o) @ (e / (2 * half))) < half - shrink
    return hit


# The ribbon between the camera and the Jetson. Its Jetson end is where
# Waveshare's CAD has it (the CSI connector does not move); its camera end
# follows the camera. It is redrawn, not moved: moving the CAD cable with the
# camera is what left it hanging in mid-air.
FFC_JETSON = (0.0302, 0.0908, 0.00675)     # centre of the cable's end, from the CAD
FFC_CAMERA_CAD = (0.05365, 0.1047, 0.0001)
FFC_WIDTH = 0.016


def ribbon(a, a_dir, b, b_dir, reach=0.030, n=48):
    """A flat strip along a cubic from a (leaving along a_dir) to b
    (arriving against b_dir). Returns (vertices, faces, centreline)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    p1 = a + np.append(np.asarray(a_dir) * reach, 0.0)
    p2 = b + np.append(np.asarray(b_dir) * reach, 0.0)
    t = np.linspace(0, 1, n)[:, None]
    line = ((1 - t) ** 3 * a + 3 * (1 - t) ** 2 * t * p1
            + 3 * (1 - t) * t ** 2 * p2 + t ** 3 * b)
    left, right = line.copy(), line.copy()
    left[:, 2] -= FFC_WIDTH / 2
    right[:, 2] += FFC_WIDTH / 2
    verts = np.vstack([left, right])
    faces = []
    for k in range(n - 1):
        faces += [[k, k + 1, n + k], [k + 1, n + k + 1, n + k]]
    return verts, np.array(faces), line


def barrel(radius, length, centre):
    m = trimesh.creation.cylinder(radius=radius, height=length, sections=28)
    return np.asarray(m.vertices) + np.asarray(centre), np.asarray(m.faces)


# Where things are, in glTF metres (x forward, y up), read off the two
# photographs against known sizes: the camera board is 25 mm, the chassis box
# ends at x = 44.8 mm and both circuit boards end at x = 35.2 mm. The upright
# stands in the 9 mm between those, so it clears the boards entirely.
CHASSIS_TOP, CHASSIS_FRONT, BOARDS_FRONT = 0.0529, 0.0448, 0.0352
SHEET = 0.002                        # the upright is one flat sheet this thick
SHEET_X = 0.0415                     # its rear face. Waveshare's own two mount
                                     # screws are at x = 36 mm: the foot they
                                     # hold is BEHIND the sheet, so the sheet
                                     # stands ahead of them, near the chassis front
FOOT_BACK = 0.0325                   # how far back the foot reaches, under the screws
PIVOT = (0.0480, 0.1080)             # the hinge screw, out on the arms
ARM_R = 0.0042                       # the arms are short rounded tabs this big
PLATE_GAP = 0.0060                   # pivot to the back of the tilting plate
BOARD_AT = (PIVOT[0] + PLATE_GAP + 0.002 + 0.003 + 0.0005, PIVOT[1] + 0.003)
WIDTH = 0.024                        # of the sheet, across the robot
FOOT_WIDTH = 0.038                   # the base is wider, to take the screws


def tilting_parts(pitch):
    """What turns with the camera: {colour: [(vertices, faces), ...]}."""
    px, py = PIVOT
    tilt = {SPACER: [], GREEN: []}
    plate_x = px + PLATE_GAP + 0.001
    tilt[GREEN].append(box((0.002, 0.027, 0.027), (plate_x, BOARD_AT[1], 0.0)))
    tilt[SPACER].append(box((0.003, 0.027, 0.027), (plate_x + 0.0025, BOARD_AT[1], 0.0)))
    for z in (-WIDTH / 2 + 0.0030, WIDTH / 2 - 0.0030):     # ears, inside the arms
        tilt[GREEN].append(box((PLATE_GAP, 2 * ARM_R, 0.0015), (px + PLATE_GAP / 2, py, z)))
        tilt[GREEN].append(barrel(ARM_R, 0.0015, (px, py, z)))
    return {c: [(rot_z(v, PIVOT, pitch), f) for v, f in lst] for c, lst in tilt.items()}


def hinged_mount(pitch):
    """The mount, as ({colour: [(vertices, faces), ...]}, fixed boxes)."""
    px, py = PIVOT
    out = {SPACER: [], GREEN: [], BLACK: []}
    # --- fixed: ONE FLAT SHEET, standing on edge and facing forward. From the
    #     side it is a line. At the bottom it bends BACK into a foot that
    #     rests on the chassis, with the two screws through that foot, behind
    #     the sheet. At the top its two edges fold FORWARD into short rounded
    #     arms, and the pivot screw goes through those.
    top = py + ARM_R
    h = top - CHASSIS_TOP
    out[GREEN].append(box((SHEET, h, WIDTH), (SHEET_X + SHEET / 2, CHASSIS_TOP + h / 2, 0.0)))
    out[GREEN].append(box((SHEET_X + SHEET - FOOT_BACK, 0.0015, FOOT_WIDTH),
                          ((FOOT_BACK + SHEET_X + SHEET) / 2, CHASSIS_TOP + 0.00075, 0.0)))
    reach = px - (SHEET_X + SHEET)
    for z in (-WIDTH / 2 + 0.00075, WIDTH / 2 - 0.00075):
        out[GREEN].append(box((reach, 2 * ARM_R, 0.0015), (SHEET_X + SHEET + reach / 2, py, z)))
        out[GREEN].append(barrel(ARM_R, 0.0015, (px, py, z)))        # the rounded end
        out[BLACK].append(barrel(0.0026, 0.0022, (px, py, z + np.sign(z) * 0.0018)))
    fixed = [v for v, f in out[GREEN] if len(v) == 8]
    for colour, lst in tilting_parts(pitch).items():
        out[colour] += lst
    return out, fixed


def box_samples(corners, n=5):
    """A grid of points through a box given by its eight corners."""
    o = corners[0]
    e = [corners[k] - o for k in (1, 2, 4)]
    g = np.linspace(0.0, 1.0, n)
    return np.array([o + a * e[0] + b * e[1] + c * e[2] for a in g for b in g for c in g])


def tilt_range(fixed, place_at, cam_points):
    """Through what angles can the camera turn before it touches the mount?"""
    def clear(pitch):
        pts = [box_samples(v) for lst in tilting_parts(pitch).values()
               for v, f in lst if len(v) == 8]
        pts.append(place_at(cam_points, pitch))
        pts = np.vstack(pts)
        return not any(inside_box(pts, b, 0.0002).any() for b in fixed)
    lo = hi = 0
    while lo > -90 and clear(lo - 5):
        lo -= 5
    while hi < 90 and clear(hi + 5):
        hi += 5
    return clear(0), lo, hi


# --------------------------------------------------------------------------
# The cells. The kit ships without them, so Waveshare's CAD has an empty
# holder. Three 18650s are added, lying across the robot the way the holder
# takes them, alternating end to end as a series holder wires them.
# --------------------------------------------------------------------------
CELL_R, CELL_LEN = 0.0090, 0.0650          # an 18650 is nominally 18 x 65 mm.
# The holder's cradles are cut to a 9.1 mm radius, and its two outer walls
# carry a retaining ridge that stands in to 8.6 mm: a real cell is pressed
# past that ridge and the wall springs. The clash test allows that 0.4 mm.
CELL_WRAP, CELL_END = "#6a3fa0", "#c9ccd1"  # purple sleeve, as on this robot


def cells_in(holder):
    """Three cells placed in the holder's bounding box. Returns the meshes by
    colour and each cell's (centre, radius, half length) for checking."""
    lo, hi = holder.min(0), holder.max(0)
    pitch = 0.0192          # bay to bay, measured off the holder's own ribs --
                            # NOT a third of its width, which is 0.5 mm out
    y = lo[1] + 0.0022 + CELL_R                       # resting on the holder's floor
    xc, zc = (lo[0] + hi[0]) / 2, (lo[2] + hi[2]) / 2
    out, cyl = {CELL_WRAP: [], CELL_END: []}, []
    for k in range(3):
        x = xc + pitch * (k - 1)
        out[CELL_WRAP].append(barrel(CELL_R, CELL_LEN - 0.002, (x, y, zc)))
        way = 1 if k % 2 == 0 else -1                 # + end alternates
        out[CELL_END].append(barrel(CELL_R - 0.0008, 0.001, (x, y, zc + way * (CELL_LEN / 2 - 0.0005))))
        out[CELL_END].append(barrel(0.0032, 0.0012, (x, y, zc + way * (CELL_LEN / 2 + 0.0004))))
        out[CELL_END].append(barrel(CELL_R - 0.0008, 0.001, (x, y, zc - way * (CELL_LEN / 2 - 0.0005))))
        cyl.append((np.array([x, y, zc]), CELL_R, CELL_LEN / 2))
    return out, cyl


def inside_cyl(points, centre, r, half, shrink=0.0004):
    d = points - centre
    return (np.hypot(d[:, 0], d[:, 1]) < r - shrink) & (np.abs(d[:, 2]) < half - shrink)


def check(parts, mount, fixed, cable, cam_end, place_camera, cam_cad, cells=()):
    """Does the hand-built geometry fit the CAD it was added to?

    Waveshare's solids are taken as fitting each other. Everything added or
    moved here -- the mount, the camera, the cable, the screws -- is tested
    against them and against each other. Returns the number of failures.

    What this is not: a general clash test between Waveshare's own parts,
    and it tests their vertices against the added boxes, so a face that
    passes clean through a box without a vertex inside would be missed.
    """
    fails = 0

    def report(ok, what, detail=""):
        nonlocal fails
        fails += not ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {what}" + (f"  --  {detail}" if detail else ""))

    def verts(node):
        return np.vstack([np.vstack(vs) for vs, _ in parts[node]["by_colour"].values()])

    boxes = [v for lst in mount.values() for v, f in lst if len(v) == 8]
    solid = [n for n in parts if parts[n]["group"] not in
             ("camera_mount", "camera", "fastener")]
    print("geometry checks")
    hits = []
    for node in solid:
        pts = verts(node)
        n = sum(int(inside_box(pts, b).sum()) for b in boxes)
        if n:
            hits.append(f"{node} ({n} points)")
    report(not hits, "the mount passes through no other part", ", ".join(hits))

    cam = np.vstack([np.vstack(vs) for c, (vs, _) in parts["camera"]["by_colour"].items()
                     if c != "#d8d4c4"])
    screws = np.abs(np.abs(cam[:, 2]) - 0.0105) < 0.0025        # the four M1.6
    n = sum(int(inside_box(cam[~screws], b).sum()) for b in fixed)
    report(n == 0, "the camera clears the upright at this tilt", f"{n} points inside" if n else "")

    ok0, t_lo, t_hi = tilt_range(fixed, place_camera, cam_cad)
    report(ok0 and t_lo <= -20 and t_hi >= 20,
           "the camera can tilt at least 20 degrees each way on its hinge",
           f"it turns freely from {t_lo:+d} to {t_hi:+d} degrees")
    arm = max((b[:, 1].max() - b[:, 1].min()) for b in fixed[2:])
    report(arm <= 0.010, "the arms are short tabs, not tall slabs", f"{arm * 1e3:.1f} mm tall")
    behind = [np.vstack(vs) for c, (vs, _) in parts["camera_mount"]["by_colour"].items()
              if c == "#9aa0a6"]
    report(bool(behind) and behind[0][:, 0].max() < SHEET_X + 1e-4,
           "the foot's screws are behind the sheet",
           f"screws reach x = {behind[0][:, 0].max() * 1e3:.1f} mm, sheet's rear face is at "
           f"{SHEET_X * 1e3:.1f} mm" if behind else "no mount screws found")
    lo = min(b[:, 0].min() for b in boxes)
    thick = fixed[0][:, 0].max() - fixed[0][:, 0].min()
    report(thick <= 0.0025, "the upright is a thin sheet, edge-on from the side",
           f"{thick * 1e3:.1f} mm thick")
    sheet_lo = fixed[0][:, 0].min()
    report(sheet_lo > BOARDS_FRONT, "the upright is wholly ahead of the circuit boards",
           f"its rear face is at {sheet_lo * 1e3:.1f} mm, boards end at {BOARDS_FRONT * 1e3:.1f} mm")
    foot = min(b[:, 1].min() for b in boxes)
    report(abs(foot - CHASSIS_TOP) < 0.0005, "the mount stands on the chassis",
           f"its foot is {(foot - CHASSIS_TOP) * 1e3:+.2f} mm from the chassis top")

    ends = cable[[0, len(cable) // 2 - 1]]
    d_j = np.linalg.norm(ends[0][:2] - np.array(FFC_JETSON[:2]))
    d_c = np.linalg.norm(ends[1][:2] - cam_end[:2])
    report(d_j < 0.001, "the ribbon ends in the Jetson's connector", f"{d_j * 1e3:.2f} mm off")
    report(d_c < 0.001, "the ribbon ends in the camera's connector", f"{d_c * 1e3:.2f} mm off")
    n = sum(int(inside_box(cable, b).sum()) for b in boxes)
    for node in ("heatsink", "fan"):
        pts = verts(node)
        lo_, hi_ = pts.min(0), pts.max(0)
        n += int(np.all((cable > lo_ + 0.0005) & (cable < hi_ - 0.0005), axis=1).sum())
    report(n == 0, "the ribbon passes through nothing", f"{n} points inside something" if n else "")

    sc = cam[screws]
    centre = np.array([BOARD_AT[0], BOARD_AT[1]])
    centre = rot_z(np.array([[centre[0], centre[1], 0.0]]), PIVOT, CAM_PITCH_DEG)[0][:2]
    far = np.linalg.norm(sc[:, :2] - centre, axis=1).max() if len(sc) else 9.0
    report(len(sc) > 0 and far < 0.016, "the camera's screws are in the camera board",
           f"furthest is {far * 1e3:.1f} mm from the board's centre")
    ms = [np.vstack(vs) for c, (vs, _) in parts["camera_mount"]["by_colour"].items()
          if c == "#9aa0a6"]
    foot = fixed[1]
    ok = (bool(ms) and foot[:, 0].min() <= ms[0][:, 0].min() + 1e-4
          and ms[0][:, 0].max() <= foot[:, 0].max() + 1e-4
          and np.abs(ms[0][:, 2]).max() <= foot[:, 2].max() + 1e-4)
    report(ok, "the mount's screws go through its foot",
           "" if ms else "no mount screws found")

    if cells:
        holder = verts("battery")
        lo_, hi_ = holder.min(0), holder.max(0)
        fit = all(lo_[0] - 1e-4 <= c[0] - r and c[0] + r <= hi_[0] + 1e-4
                  and c[1] + r <= hi_[1] + 1e-4 and lo_[2] <= c[2] - h and c[2] + h <= hi_[2]
                  for c, r, h in cells)
        spare = (hi_[2] - lo_[2] - 2 * cells[0][2]) * 1e3
        report(fit, "the three cells fit inside the holder",
               f"{spare:.1f} mm of length to spare for the springs")
        hits = []
        for node in parts:
            if node == "cells" or parts[node]["group"] == "fastener":
                continue
            pts = verts(node)
            n = sum(int(inside_cyl(pts, *c).sum()) for c in cells)
            if n:
                hits.append(f"{node} ({n} points)")
        report(not hits, "the cells pass through nothing, their own holder included",
               ", ".join(hits))
        gap = min(abs(cells[k + 1][0][0] - cells[k][0][0]) for k in range(2)) - 2 * CELL_R
        report(gap > 0, "the cells do not overlap each other", f"{gap * 1e3:.1f} mm between them")

    low = min(verts(n)[:, 1].min() for n in parts)
    report(low > -0.0006, "nothing is below the floor", f"lowest point {low * 1e3:.2f} mm")
    return fails


def main():
    parts = {}                     # node name -> dict(meshes by colour, meta)
    all_leaves = tessellated()
    cam_c, cam_n, cad_pitch, lens_c = camera_frame(all_leaves)
    mount, fixed = hinged_mount(CAM_PITCH_DEG)

    def place_camera(g, pitch=CAM_PITCH_DEG):
        """CAD camera -> level, onto the spacer, then tilted about the hinge."""
        g = rot_z(g, cam_c[:2], -cad_pitch)
        g = g + np.array([BOARD_AT[0] - cam_c[0], BOARD_AT[1] - cam_c[1], 0.0])
        return rot_z(g, PIVOT, pitch)

    cam_cad = np.vstack([to_gltf(np.asarray(v, float)) for path, v, _ in all_leaves
                         if "Raspberry_Pi_Camera" in " ".join(path)])
    foot_x = (BOARDS_FRONT + CHASSIS_FRONT) / 2
    for path, v, f in all_leaves:
        v, f = np.asarray(v, float), np.asarray(f, int)
        group, name, desc, colour = classify(path[1:])
        label = " ".join(path)
        if group == "camera_mount" or "FFC" in label:
            continue                               # replaced, see above
        shift = None
        if "M1_6" in label:
            group, name = "camera", "Camera screws"   # they go where the camera goes
        elif "M3X6" in label:
            g0 = to_gltf(v)
            c0 = (g0.min(0) + g0.max(0)) / 2
            if 0.030 < c0[0] < 0.042 and 0.050 < c0[1] < 0.056 and abs(c0[2]) < 0.016:
                # the two that held the CAD's bracket: now through the new foot
                # the two that hold the mount's foot. They stay exactly where
                # Waveshare's CAD has them; the foot is built under them.
                group, name = "camera_mount", "Mount screws"
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
            g = place_camera(g)
        if shift is not None:
            g = g + shift
        vs.append(g)
        fs.append(f + off)

    # the ribbon: up out of the Jetson's connector, over, and down into the
    # camera's, which is on the top edge of the camera board
    cam_end = place_camera(np.array([FFC_CAMERA_CAD]))[0]
    up = rot_z(np.array([[0.0, 1.0, 0.0]]), (0.0, 0.0), CAM_PITCH_DEG)[0][:2]
    rv, rf, ffc_line = ribbon(FFC_JETSON, (0.0, 1.0), cam_end, up, reach=0.042)
    cvs, cfs = parts["camera"]["by_colour"].setdefault("#d8d4c4", ([], []))
    cvs.append(rv)
    cfs.append(rf + sum(len(a) for a in cvs[:-1]))

    p = parts.setdefault("camera_mount", {"by_colour": {}})
    p.update({"group": "camera_mount", "name": "Camera mount",
              "desc": "Hinged aluminium bracket with a tilting plate and spacer"})
    for colour, lst in mount.items():
        vs, fs = p["by_colour"].setdefault(colour, ([], []))
        for v, f in lst:
            off = sum(len(a) for a in vs)
            vs.append(v)
            fs.append(f + off)

    holder = np.vstack([np.vstack(vs) for vs, _ in parts["battery"]["by_colour"].values()])
    cell_meshes, cell_cyl = cells_in(holder)
    p = parts["cells"] = {"group": "cells", "name": "Cells", "by_colour": {},
                          "desc": "3 x 18650 lithium-ion cells in series. Not in "
                                  "Waveshare's CAD: the kit ships without them"}
    for colour, lst in cell_meshes.items():
        vs, fs = p["by_colour"].setdefault(colour, ([], []))
        for v, f in lst:
            off = sum(len(a) for a in vs)
            vs.append(v)
            fs.append(f + off)

    problems = check(parts, mount, fixed, rv, cam_end, place_camera, cam_cad, cell_cyl)
    if problems:
        raise SystemExit(f"{problems} geometry check(s) failed: nothing written")

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

    lens = place_camera(lens_c[None, :])[0]
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
