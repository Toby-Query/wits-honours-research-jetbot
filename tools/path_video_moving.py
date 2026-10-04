"""The robot's path over a tiled floor, from a video in which the PHONE MOVED.

    .venv/bin/python tools/path_video_moving.py VIDEO --start S --end S --tile 0.5

tools/path_video.py needs a still camera. This one does not: each frame is
registered to the one before by matching features on the floor (the robot is
masked out), the registrations are chained back to a reference frame, and the
reference frame is tied to the floor by its tile joints as before.

Chaining accumulates error, so it is checked rather than trusted: at the end
of the run the tile joints are found afresh and mapped through the chain. If
the chain is good they land on whole multiples of the tile size.
"""
import argparse
import importlib.util
import json
import math
from pathlib import Path

import cv2
import numpy as np

spec = importlib.util.spec_from_file_location("pv", Path(__file__).with_name("path_video.py"))
pv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pv)


def robot_px(frame, near=None):
    """Centre of the robot in pixels: the green of its chassis."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    g = ((hsv[..., 0] > 35) & (hsv[..., 0] < 85) & (hsv[..., 1] > 70) & (hsv[..., 2] > 60)).astype(np.uint8)
    g = cv2.morphologyEx(g, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(g)
    best = None
    for k in range(1, n):
        if stats[k, cv2.CC_STAT_AREA] < 500:
            continue
        d = 0 if near is None else np.hypot(*(cent[k] - near))
        score = stats[k, cv2.CC_STAT_AREA] - 3 * d
        if best is None or score > best[0]:
            best = (score, cent[k])
    return None if best is None else best[1]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--start", type=float, required=True, help="a moment just before the run, s")
    ap.add_argument("--end", type=float, required=True, help="a moment just after it, s")
    ap.add_argument("--tile", type=float, default=0.5)
    ap.add_argument("--up", help="x of the joints running up the reference frame, e.g. 184,730,1284")
    ap.add_argument("--across", help="y of the joints running across it")
    ap.add_argument("--out")
    a = ap.parse_args()
    cap = cv2.VideoCapture(a.video)
    fps = cap.get(cv2.CAP_PROP_FPS)
    i0, i1 = int(a.start * fps), int(a.end * fps)
    cap.set(cv2.CAP_PROP_POS_FRAMES, i0)
    ok, ref = cap.read()
    ups, acrosses, (w, h) = pv.find_grid(ref)
    # The edge of a rug or a skirting board looks like a joint to the line
    # finder. If it picks one, name the real joints by hand.
    up_pos = [float(v) for v in a.up.split(",")] if a.up else \
        pv.pick_regular(ups, min(3, len(ups)), w)
    ac_pos = [float(v) for v in a.across.split(",")] if a.across else \
        pv.pick_regular(acrosses, min(3, len(acrosses)), h)
    bw = pv.joints_mask(ref)
    H_ref = pv.homography([pv.refit(bw, p, True) for p in up_pos],
                          [pv.refit(bw, p, False) for p in ac_pos], a.tile)
    print(f"reference frame at {a.start:.1f} s: joints up the picture at "
          f"{[round(p) for p in up_pos]}, across at {[round(p) for p in ac_pos]}")
    sift = cv2.SIFT_create(2500)
    bf = cv2.BFMatcher()
    S = 0.5

    def feats(frame, rp):
        g = cv2.cvtColor(cv2.resize(frame, None, fx=S, fy=S), cv2.COLOR_BGR2GRAY)
        g = cv2.createCLAHE(3.0, (8, 8)).apply(g)
        mask = np.full(g.shape, 255, np.uint8)
        if rp is not None:
            cv2.circle(mask, (int(rp[0] * S), int(rp[1] * S)), int(170 * S), 0, -1)
        return sift.detectAndCompute(g, mask)

    rp = robot_px(ref)
    kp0, d0 = feats(ref, rp)
    chain = np.eye(3)                       # this frame's pixels -> reference pixels
    rows, inl = [], []
    for i in range(i0, i1):
        ok, frame = cap.read() if i > i0 else (True, ref)
        if not ok:
            break
        if i > i0:
            rp_new = robot_px(frame, rp)
            rp = rp_new if rp_new is not None else rp
            kp1, d1 = feats(frame, rp)
            m = bf.knnMatch(d1, d0, k=2)
            good = [x for x, y in m if x.distance < 0.75 * y.distance]
            if len(good) >= 12:
                src = np.float32([kp1[g.queryIdx].pt for g in good]) / S
                dst = np.float32([kp0[g.trainIdx].pt for g in good]) / S
                Hs, msk = cv2.findHomography(src, dst, cv2.RANSAC, 2.5)
                if Hs is not None and msk.sum() >= 10:
                    chain = chain @ Hs
                    inl.append(int(msk.sum()))
                    kp0, d0 = kp1, d1
        p = cv2.perspectiveTransform(np.float32([[rp]]), (H_ref @ chain).astype(np.float64))[0, 0]
        rows.append([i / fps, p[0], -p[1]])
        last = frame
    rows = np.array(rows)
    print(f"{len(rows)} frames chained; matches kept per step: median {int(np.median(inl))}, "
          f"fewest {min(inl)}")
    # --- the check: joints found in the LAST frame, mapped through the chain
    bw = pv.joints_mask(last)
    segs = cv2.HoughLinesP(bw * 255, 1, np.pi / 720, 180, minLineLength=int(0.3 * w), maxLineGap=60)
    off = []
    if segs is not None:
        Hl = (H_ref @ chain).astype(np.float64)
        for x1, y1, x2, y2 in np.asarray(segs).reshape(-1, 4):
            q = cv2.perspectiveTransform(np.float32([[[x1, y1]], [[x2, y2]]]), Hl).reshape(2, 2)
            d = q[1] - q[0]
            axis = 0 if abs(d[1]) > abs(d[0]) else 1          # the coordinate that is constant
            c = (q[0][axis] + q[1][axis]) / 2
            off.append(abs(c / a.tile - round(c / a.tile)) * a.tile)
    chk = float(np.median(off)) if off else float("nan")
    print(f"check: {len(off)} joint segments in the last frame land a median "
          f"{chk * 100:.1f} cm from a whole tile (a quarter-tile, {a.tile * 25:.0f} cm, would be chance)")
    np.save(Path(a.video).with_suffix(".moving.npy"), rows)
    if a.out:
        Path(a.out).write_text(json.dumps({"video": a.video, "chain_check_cm": chk * 100,
                                           "frames": len(rows)}, indent=1))


if __name__ == "__main__":
    main()
