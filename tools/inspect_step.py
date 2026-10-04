"""Walk the manufacturer's STEP assembly and record what is in it, placed.

    .venv/bin/python tools/inspect_step.py

Writes reference/step_tree.json: one record per leaf with its label path and
its bounding box, volume and centre IN THE ASSEMBLY FRAME. Everything in
jetbot_twin/hardware.py that says "measured from the STEP" is read from here.

The labels build123d gives a leaf are not placed -- the instance transform
lives on the parent compound -- so the tree is walked twice in step: once
through build123d for the names and once through OpenCascade with cumulative
locations for the geometry.
"""
import json
import time
from pathlib import Path

from build123d import Compound, import_step
from OCP.TopAbs import TopAbs_COMPOUND
from OCP.TopoDS import TopoDS_Iterator

ROOT = Path(__file__).resolve().parents[1]


def walk(node, shape, path, out):
    """Yield (label path, located TopoDS shape) for every leaf."""
    label = getattr(node, "label", "") or ""
    here = path + [label]
    kids = list(getattr(node, "children", []) or [])
    if not kids:
        out.append((here, shape))
        return
    subs = []
    it = TopoDS_Iterator(shape, True, True)
    while it.More():
        subs.append(it.Value())
        it.Next()
    if len(subs) != len(kids):
        # names and geometry disagree about the structure: keep the geometry
        out.append((here, shape))
        return
    for k, s in zip(kids, subs):
        walk(k, s, here, out)


def leaves(asm):
    out = []
    walk(asm, asm.wrapped, [], out)
    return out


def load():
    return import_step(str(ROOT / "reference" / "jetbot.STEP"))


if __name__ == "__main__":
    t0 = time.time()
    asm = load()
    print(f"imported in {time.time() - t0:.0f}s", flush=True)
    rows = []
    for path, shape in leaves(asm):
        c = Compound(shape)
        solids = c.solids()
        if not solids:
            continue
        bb = c.bounding_box()
        rows.append({
            "path": path[1:], "n_solids": len(solids),
            "volume_mm3": sum(s.volume for s in solids),
            "min": [bb.min.X, bb.min.Y, bb.min.Z],
            "max": [bb.max.X, bb.max.Y, bb.max.Z],
        })
    bb = asm.bounding_box()
    out = {"bbox": {"min": [bb.min.X, bb.min.Y, bb.min.Z],
                    "max": [bb.max.X, bb.max.Y, bb.max.Z]},
           "leaves": rows}
    (ROOT / "reference" / "step_tree.json").write_text(
        json.dumps(out, indent=1, ensure_ascii=False))
    print(f"{len(rows)} leaves, bbox {bb.size.X:.1f} x {bb.size.Y:.1f} x "
          f"{bb.size.Z:.1f} mm, {time.time() - t0:.0f}s")
