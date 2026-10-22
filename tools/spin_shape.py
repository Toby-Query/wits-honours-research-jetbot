"""How far the robot turned, from a top-down video, without a lit display.

    .venv/bin/python tools/spin_shape.py VIDEO --sheet SHEET.jpg
    .venv/bin/python tools/spin_shape.py VIDEO --quarter-turns N [--out FILE.json]

For robot 2, whose OLED is broken, so tools/spin_video.py (which follows the
lit display) cannot be used. The answer comes in two parts:

1. **Whole quarter-turns, counted by eye.** --sheet writes a strip of frames
   through the spin, 20 frames apart, cropped on the robot. Follow the camera
   ribbon (the grey loop): count the quarter-turns from where it points at the
   start to the nearest quarter of where it points at the end. Counter-
   clockwise from above is positive.
2. **The rest, measured.** The boards' straight edges at rest, before and
   after, give the robot's angle to a fraction of a degree, but only up to a
   quarter-turn (a rectangle looks the same four ways round). That fraction,
   between -45 and +45 degrees, is added to the count.

This does not depend on the video's timing, which matters because
slow-motion files drop and repeat frames (data/real/robot2-2026-10-22/NOTES.md).

Checked against tools/spin_video.py on robot 1's slow-motion spin of
2026-10-04 (spin_slowmo.mp4): 1032.4 against 1032.7 degrees. On its 30 fps
wide shot (spin_no_tape.mp4) the robot is too small for clean edges before
the spin and the tool says so; film close, from above, as robot 2's were.

Two automatic ways of counting the turns were tried first and dropped:
matching the robot's whole outline frame by frame (it looks much the same a
third of a turn round, so the match slips by that much now and then), and
choosing the steadiest path through each frame's best few matches (it gained
or lost a whole turn on robot 1's videos). Counting quarter-turns on the
sheet is quick and has not been wrong.
"""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np

W = 540             # robot-finding is done on frames shrunk to this width


def robot_mask(small):
    """The robot's pixels: dark parts and the green chassis, keeping only the
    pieces that touch green (only the robot is green; shadows are not)."""
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    dark = v < 70
    green = ((h > 30) & (h < 90) & (s > 90) & (v > 60)).astype(np.uint8)
    m = (dark | (green > 0)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab = cv2.connectedComponents(m)
    near_green = cv2.dilate(green, np.ones((9, 9), np.uint8)) > 0
    counts = np.bincount(lab[near_green & (m > 0)], minlength=n)
    keep = counts > 30
    keep[0] = False
    return keep[lab].astype(np.uint8)


def read(cap, k):
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.set(cv2.CAP_PROP_POS_FRAMES, min(max(k, 0), n - 1))
    ok, f = cap.read()
    if not ok:
        raise SystemExit(f"cannot read frame {k}")
    return f


def centre_of(frame):
    scale = W / frame.shape[1]
    m = robot_mask(cv2.resize(frame, (W, int(round(frame.shape[0] * scale)))))
    ys, xs = np.nonzero(m)
    if len(xs) < 200:
        return None
    return xs.mean() / scale, ys.mean() / scale


def edge_angle(frame):
    """The boards' edge direction in the image, degrees mod 90, from the
    straight lines near the robot's middle, weighted by length. Image angles
    run clockwise (y points down). Returns (angle, scatter, lines used)."""
    c = centre_of(frame)
    if c is None:
        raise SystemExit("no robot in the frame")
    k = frame.shape[1] / 1080.0
    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(grey, (5, 5), 0), 60, 150)
    near = np.zeros_like(edges)
    cv2.circle(near, (int(c[0]), int(c[1])), int(230 * k), 255, -1)
    lines = cv2.HoughLinesP(edges & near, 1, np.pi / 720, int(60 * k),
                            minLineLength=int(90 * k), maxLineGap=6)
    if lines is None:
        raise SystemExit("no straight edges found on the robot")
    x1, y1, x2, y2 = lines.reshape(-1, 4).T.astype(float)
    ang = np.degrees(np.arctan2(y2 - y1, x2 - x1)) % 90.0
    ln = np.hypot(x2 - x1, y2 - y1)
    # mean on the mod-90 circle (angles times four), then again over the
    # lines within 4 degrees of it
    mean = (np.degrees(np.angle((ln * np.exp(4j * np.radians(ang))).sum())) / 4) % 90
    dev = (ang - mean + 45) % 90 - 45
    keep = np.abs(dev) < 4
    if not keep.any():
        return float(mean), float("nan"), 0
    mean = (np.degrees(np.angle((ln[keep] * np.exp(4j * np.radians(ang[keep]))).sum())) / 4) % 90
    dev = (ang[keep] - mean + 45) % 90 - 45
    return float(mean), float(np.sqrt(np.average(dev ** 2, weights=ln[keep]))), int(keep.sum())


def motion(cap):
    """(first, last) frame in which the robot is moving, from how much its
    pixels change between sampled frames (about 600 samples a video)."""
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    every = max(1, n // 600)
    prev, diffs, idx = None, [], []
    for k in range(0, n, every):
        f = read(cap, k)
        scale = W / f.shape[1]
        m = robot_mask(cv2.resize(f, (W, int(round(f.shape[0] * scale)))))
        if prev is not None:
            diffs.append(float((m ^ prev).sum()) / max(1, m.sum()))
            idx.append(k)
        prev = m
    diffs = np.array(diffs)
    moving = diffs > max(0.15, 4 * np.median(diffs))
    if not moving.any():
        raise SystemExit("the robot never appears to move")
    # the spin is the longest stretch of motion (a hand placing the robot,
    # or the phone being touched, is shorter)
    runs, start = [], None
    for j, mv in enumerate(np.r_[moving, False]):
        if mv and start is None:
            start = j
        elif not mv and start is not None:
            runs.append((start, j - 1))
            start = None
    j0, j1 = max(runs, key=lambda r: r[1] - r[0])
    return idx[j0] - every, idx[j1]


def rest_angle(cap, frames):
    """edge_angle over several frames at rest, combined on the mod-90 circle
    weighted by how many lines each found. Returns (angle, spread, frames)."""
    got = []
    for k in frames:
        try:
            a, s, n = edge_angle(read(cap, k))
        except SystemExit:
            continue
        if n >= 1:
            got.append((a, n))
    # prefer frames with several edges; a small robot in a wide shot may
    # only ever show one or two
    if any(g[1] >= 3 for g in got):
        got = [g for g in got if g[1] >= 3]
    if not got:
        raise SystemExit("no frame at rest with clear edges")
    a = np.array([g[0] for g in got])
    w = np.array([g[1] for g in got], float)
    mean = (np.degrees(np.angle((w * np.exp(4j * np.radians(a))).sum())) / 4) % 90
    spread = float(np.sqrt(np.average(((a - mean + 45) % 90 - 45) ** 2, weights=w)))
    return float(mean), spread, len(got)


def sheet(cap, a, b, path, step=20, cols=9):
    tiles = []
    for k in range(a, b + step, step):
        f = read(cap, k)
        c = centre_of(f) or (f.shape[1] / 2, f.shape[0] / 2)
        r = int(0.22 * f.shape[1])
        pad = cv2.copyMakeBorder(f, r, r, r, r, cv2.BORDER_CONSTANT)
        x, y = int(c[0]), int(c[1])
        tile = cv2.resize(pad[y:y + 2 * r, x:x + 2 * r], (160, 160))
        cv2.putText(tile, str(k), (3, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
        tiles.append(tile)
    while len(tiles) % cols:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]
    cv2.imwrite(str(path), np.vstack(rows))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--sheet", help="write the strip of frames for counting")
    ap.add_argument("--quarter-turns", type=int,
                    help="quarter-turns counted on the sheet, counter-clockwise positive")
    ap.add_argument("--out")
    a = ap.parse_args()

    cap = cv2.VideoCapture(a.video)
    first, last = motion(cap)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"{n} frames; the robot moves between about frame {first} and {last}")
    if a.sheet:
        sheet(cap, first, last + 40, a.sheet)
        print(f"wrote {a.sheet}: count the quarter-turns, then run again with "
              "--quarter-turns N")
    # at rest: well before and well after the motion
    # rest windows scaled to the spin's length in frames, so the same
    # stretch of real time is used at 30 fps and in slow motion
    L = max(last - first, 20)
    before = [k for k in np.linspace(first - 0.4 * L, first - 0.05 * L, 9).astype(int) if k >= 0]
    after = [k for k in np.linspace(last + 0.15 * L, last + 0.8 * L, 9).astype(int) if k < n]
    a0, s0, n0 = rest_angle(cap, before or [0])
    a1, s1, n1 = rest_angle(cap, after or [n - 1])
    # counter-clockwise from above is the image's angle decreasing
    frac = (-(a1 - a0) + 45.0) % 90.0 - 45.0
    print(f"edges at rest: before {a0:.1f} deg (spread {s0:.1f}, {n0} frames), "
          f"after {a1:.1f} deg (spread {s1:.1f}, {n1} frames)")
    print(f"past the nearest quarter-turn: {frac:+.1f} deg")
    if max(s0, s1) > 3.0:
        print("  WARNING: the edges at rest disagree by more than 3 degrees "
              "between frames; the fraction is not to be trusted (robot too small "
              "in the shot, or not at rest in the frames used)")
    if a.quarter_turns is None:
        return 0
    total = 90.0 * a.quarter_turns + frac
    print(f"total rotation {total:.1f} deg ({total / 360:.2f} turns)")
    if a.out:
        Path(a.out).write_text(json.dumps({
            "video": str(a.video), "rotation_deg": total,
            "quarter_turns_counted": a.quarter_turns, "fraction_deg": frac,
            "edge_deg_before_after": [a0, a1], "edge_spread_deg": [s0, s1],
            "method": "tools/spin_shape.py: quarter-turns counted by eye on the "
                      "frame strip, the rest from the boards' edges at rest"}, indent=1))
        print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
