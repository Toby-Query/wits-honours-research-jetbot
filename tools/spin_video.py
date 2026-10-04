"""How far the robot turned, from a top-down video of it spinning.

    .venv/bin/python tools/spin_video.py VIDEO [--slow 8] [--out FILE.json]

The robot carries its own compass needle: the chassis is the only green
thing in shot and the lit display at its back is the only bright blue one.
The direction from the middle of the green to the blue is the robot's
heading, with no symmetry to confuse it, in every frame. Unwrapped, that is
the total rotation.

Two other methods were tried first and are not used. Following the long axis
of the green rectangle is ambiguous by a half-turn and flickers. Matching
each frame's picture against the last, or against the first, is thrown by
the robot's own shadow turning with it and by its near-symmetry.

--slow is how many times slower than life the file plays (a 240 fps clip
stored at 30 fps is 8). The phone used here measured 240.3 fps against the
wheels-up sweep; see tools/wheel_tape.py.
"""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np

W = 720                                # wide enough that a robot filling a
                                       # quarter of the frame still has a few
                                       # hundred green pixels
CHASSIS_M = 0.1336                     # the green box is this long


def needle(frame):
    """(heading in radians, image sense; centre x, y; chassis length px) or None."""
    s = cv2.resize(frame, (W, int(round(W * frame.shape[0] / frame.shape[1]))))
    hsv = cv2.cvtColor(s, cv2.COLOR_BGR2HSV)
    h, sat, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    green = ((h > 35) & (h < 85) & (sat > 90) & (v > 70)).astype(np.uint8)
    blue = ((h > 95) & (h < 130) & (sat > 90) & (v > 120)).astype(np.uint8)
    pts = cv2.findNonZero(green)
    if pts is None or len(pts) < 150:
        return None
    (gx, gy), (w, hh), _ = cv2.minAreaRect(pts)
    # the display is the biggest blue thing on the robot; the USB sockets
    # are blue too, but smaller and dimmer
    n, lab, stats, cent = cv2.connectedComponentsWithStats(blue)
    best = None
    for k in range(1, n):
        if stats[k, cv2.CC_STAT_AREA] < 12:
            continue
        if np.hypot(cent[k][0] - gx, cent[k][1] - gy) > 0.9 * max(w, hh):
            continue                               # not on the robot
        if best is None or stats[k, cv2.CC_STAT_AREA] > stats[best, cv2.CC_STAT_AREA]:
            best = k
    if best is None:
        return None
    bx, by = cent[best]
    return np.arctan2(by - gy, bx - gx), gx, gy, max(w, hh)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--slow", type=float, default=8.0)
    ap.add_argument("--out")
    a = ap.parse_args()
    cap = cv2.VideoCapture(a.video)
    fps = cap.get(cv2.CAP_PROP_FPS) * a.slow
    rows = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        r = needle(frame)
        rows.append(r if r else (np.nan,) * 4)
    rows = np.array(rows, float)
    n = len(rows)
    idx = np.arange(n)
    good = np.isfinite(rows[:, 0])
    deg = np.degrees(np.interp(idx, idx[good], np.unwrap(rows[good, 0])))
    cx = np.interp(idx, idx[good], rows[good, 1])
    cy = np.interp(idx, idx[good], rows[good, 2])
    k = int(fps / 8)
    rate = np.gradient(np.convolve(deg, np.ones(k) / k, "same")) * fps          # deg/s
    fast = np.abs(rate) > 0.25 * np.percentile(np.abs(rate), 98)
    runs, start = [], None
    for i in range(n):
        if fast[i] and start is None:
            start = i
        elif not fast[i] and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, n - 1))
    i0, i1 = max(runs, key=lambda r: r[1] - r[0])           # the spin itself
    before = slice(max(0, i0 - int(2.0 * fps)), max(1, i0 - int(0.3 * fps)))
    after = slice(min(n - 2, i1 + int(0.3 * fps)), min(n, i1 + int(2.0 * fps)))
    total = float(np.median(deg[after]) - np.median(deg[before]))
    px_per_m = float(np.nanmedian(rows[:, 3])) / CHASSIS_M
    d = np.hypot(np.median(cx[after]) - np.median(cx[before]),
                 np.median(cy[after]) - np.median(cy[before])) / px_per_m
    mid = slice(i0 + int(0.5 * fps), i1 - int(0.4 * fps))
    # image y points down, so a NEGATIVE image angle is counter-clockwise on the floor
    res = dict(video=a.video, true_fps=float(fps), rotation_deg=abs(total),
               turns=abs(total) / 360.0,
               direction="counter-clockwise" if total < 0 else "clockwise",
               steady_rate_deg_s=float(np.median(np.abs(rate[mid]))),
               moving_for_s=float((i1 - i0) / fps),
               heading_scatter_at_rest_deg=float(np.std(deg[before])),
               frames_without_a_reading=int((~good).sum()),
               ended_this_far_from_start_m=float(d))
    for k_, v in res.items():
        print(f"  {k_:<30} {v:.2f}" if isinstance(v, float) else f"  {k_:<30} {v}")
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1))
        print(f"wrote {a.out}")
    np.save(Path(a.video).with_suffix(".heading.npy"), np.column_stack([idx / fps, deg, rate]))


if __name__ == "__main__":
    main()
