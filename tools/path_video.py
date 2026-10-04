"""The robot's whole path over a tiled floor, from a top-down video.

    .venv/bin/python tools/path_video.py VIDEO --tile 0.5 [--out FILE.json]

The tile joints are the ruler. Two joints running up the picture and three
running across it meet in six points whose real positions are known (the
tiles are square), which fixes the map from picture to floor: perspective
included, so the phone need not be level. The joints are found again in every
few frames, so a hand-held phone drifting a little does not move the floor.

The robot is found as a shape that is not floor: the picture of the empty
floor is the median of frames taken while the robot was moving, and whatever
differs from it is the robot. Its centre is its position. Its heading is its
long axis (the antennas make it long), which is only known to a half-turn,
so the corners are measured from the direction of travel along each side
instead. In a wide shot the colour markers that tools/spin_video.py uses are
too small to find.

What this cannot correct: the chassis top and the display are 5-10 cm above
the floor, so away from the point under the phone they appear displaced
outward, by height / camera height of their distance from that point. With
the phone 1.5-2 m up that is a few centimetres at the edge of the picture.
"""
import argparse
import json
import math
from pathlib import Path

import cv2
import numpy as np


def joints_mask(frame):
    g = cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    bh = cv2.morphologyEx(g, cv2.MORPH_BLACKHAT, np.ones((15, 15), np.uint8))
    return (bh > 12).astype(np.uint8)


def find_grid(frame):
    """Reference lines: (two up-the-picture joints, three across), each as
    (point, direction)."""
    bw = joints_mask(frame) * 255
    h, w = bw.shape
    segs = cv2.HoughLinesP(bw, 1, np.pi / 720, 200, minLineLength=int(0.35 * w), maxLineGap=60)
    segs = np.asarray(segs).reshape(-1, 4)
    up, across = [], []
    for x1, y1, x2, y2 in segs:
        a = math.degrees(math.atan2(y2 - y1, x2 - x1)) % 180
        length = math.hypot(x2 - x1, y2 - y1)
        if abs(a - 90) < 12:
            up.append(((x1 + x2) / 2, length, (x1, y1, x2, y2)))
        elif a < 12 or a > 168:
            across.append(((y1 + y2) / 2, length, (x1, y1, x2, y2)))

    def clusters(items, gap=60):
        items = sorted(items)
        out, cur = [], [items[0]]
        for it in items[1:]:
            if it[0] - cur[-1][0] < gap:
                cur.append(it)
            else:
                out.append(cur)
                cur = [it]
        out.append(cur)
        return [(float(np.mean([i[0] for i in c])), float(sum(i[1] for i in c))) for c in out]

    return clusters(up), clusters(across), (w, h)


def pick_regular(cands, count, span):
    """The `count` candidates most nearly evenly spaced: tile joints are; the
    edge of a rug or of a patch of glare is not."""
    pos = sorted(p for p, _ in cands)
    best = None
    from itertools import combinations
    for combo in combinations(pos, count):
        d = np.diff(combo)
        if d.min() < 0.2 * span:
            continue
        score = (np.std(d) / np.mean(d)) if count > 2 else -d[0]     # two: the widest pair inside
        if best is None or score < best[0]:
            best = (score, combo)
    return list(best[1])


def refit(bw, pos, vertical, band=45):
    """Fit one joint near where it was last seen. Returns (point, direction)."""
    h, w = bw.shape
    ys, xs = np.nonzero(bw)
    if vertical:
        m = np.abs(xs - pos) < band
    else:
        m = np.abs(ys - pos) < band
    pts = np.column_stack([xs[m], ys[m]]).astype(np.float32)
    if len(pts) < 200:
        return None
    vx, vy, x0, y0 = cv2.fitLine(pts, cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
    return np.array([x0, y0]), np.array([vx, vy])


def meet(l1, l2):
    (p, d), (q, e) = l1, l2
    t = np.linalg.solve(np.column_stack([d, -e]), q - p)[0]
    return p + t * d


def homography(lines_up, lines_across, tile):
    src, dst = [], []
    for i, lu in enumerate(lines_up):
        for j, la in enumerate(lines_across):
            src.append(meet(lu, la))
            dst.append([i * tile, j * tile])
    H, _ = cv2.findHomography(np.array(src, np.float32), np.array(dst, np.float32))
    return H


def to_floor(H, pts):
    p = cv2.perspectiveTransform(np.array(pts, np.float32).reshape(-1, 1, 2), H).reshape(-1, 2)
    return p


SCALE = 0.5                            # the robot is looked for at half size


def floor_picture(cap, first, last, count=25):
    """The floor with the robot removed: the median of frames from while it
    was moving, in which no one spot is covered for long."""
    frames = []
    for i in np.linspace(first, last, count).astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, f = cap.read()
        if ok:
            frames.append(cv2.resize(f, None, fx=SCALE, fy=SCALE))
    return np.median(np.array(frames), axis=0).astype(np.uint8)


def blob(frame, floor, near=None):
    """The robot as a shape against the floor: (centre x, y, axis angle mod pi)
    in full-size pixels, or None. `near` is where it was last, so a foot or a
    shadow moving elsewhere is not mistaken for it."""
    small = cv2.resize(frame, None, fx=SCALE, fy=SCALE)
    diff = cv2.absdiff(small, floor).max(axis=2)
    fg = (diff > 38).astype(np.uint8)
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))     # joints are thin
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(fg)
    best = None
    for k in range(1, n):
        area = stats[k, cv2.CC_STAT_AREA]
        if area < 900 or area > 30000:
            continue
        c = cent[k] / SCALE
        d = 0.0 if near is None else float(np.hypot(*(c - near)))
        if near is not None and d > 260:
            continue
        score = area - 4 * d
        if best is None or score > best[0]:
            best = (score, k)
    if best is None:
        return None
    k = best[1]
    m = cv2.moments((lab == k).astype(np.uint8), True)
    ang = 0.5 * math.atan2(2 * m["mu11"], m["mu20"] - m["mu02"])
    return cent[k][0] / SCALE, cent[k][1] / SCALE, ang


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--tile", type=float, default=0.5)
    ap.add_argument("--slow", type=float, default=1.0)
    ap.add_argument("--centre-to-axle", type=float, default=0.022,
                    help="the axle is this far ahead of the chassis centre, m")
    ap.add_argument("--run-from", type=float, default=0.72,
                    help="fraction of the clip after which the robot is moving; "
                         "the floor is pictured from those frames")
    ap.add_argument("--out")
    a = ap.parse_args()
    cap = cv2.VideoCapture(a.video)
    fps = cap.get(cv2.CAP_PROP_FPS) * a.slow
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.set(cv2.CAP_PROP_POS_FRAMES, n // 2)
    ok, ref = cap.read()
    ups, acrosses, (w, h) = find_grid(ref)
    up_pos = pick_regular([c for c in ups if 0.25 * w < c[0] < w], 2, w)
    ac_pos = pick_regular(acrosses, 3, h)
    print(f"{n} frames at {fps:.2f} fps; joints up the picture at x = "
          f"{[round(p) for p in up_pos]}, across at y = {[round(p) for p in ac_pos]}")
    floor = floor_picture(cap, int(a.run_from * n), n - 5)
    bw = joints_mask(ref)
    up_ref, ac_ref = list(up_pos), list(ac_pos)
    ref_u = [refit(bw, p, True) for p in up_ref]
    ref_a = [refit(bw, p, False) for p in ac_ref]
    drift, good_fits = (0.0, 0.0), 0
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    rows, H, near = [], homography(ref_u, ref_a, a.tile), None
    raw = Path(a.video).with_suffix(".raw.npy")
    for i in range(n if not raw.exists() else 0):
        ok, frame = cap.read()
        if not ok:
            break
        if i % 3 == 0 or H is None:
            bw = joints_mask(frame)
            if near is not None:                      # the robot is not a joint
                x, y = int(near[0]), int(near[1])
                bw[max(0, y - 130):y + 130, max(0, x - 130):x + 130] = 0
            lu = [refit(bw, p + drift[0], True) for p in up_ref]
            la = [refit(bw, p + drift[1], False) for p in ac_ref]
            # A refit is believed only if the line is still where a joint
            # could be: nearly the direction it had, and not far from where
            # the phone's drift so far would put it. Otherwise a shadow's
            # edge gets followed across the floor.
            ok_u = [l is not None and abs(l[1][0] / l[1][1] - ref_u[k][1][0] / ref_u[k][1][1]) < 0.03
                    for k, l in enumerate(lu)]
            ok_a = [l is not None and abs(l[1][1] / l[1][0] - ref_a[k][1][1] / ref_a[k][1][0]) < 0.03
                    for k, l in enumerate(la)]
            if all(ok_u) and all(ok_a):
                du = np.median([l[0][0] + (h / 2 - l[0][1]) * l[1][0] / l[1][1] - p
                                for l, p in zip(lu, up_ref)])
                da = np.median([l[0][1] + (w / 2 - l[0][0]) * l[1][1] / l[1][0] - p
                                for l, p in zip(la, ac_ref)])
                if abs(du - drift[0]) < 25 and abs(da - drift[1]) < 25:
                    drift = (0.7 * drift[0] + 0.3 * du, 0.7 * drift[1] + 0.3 * da)
                    H = homography(lu, la, a.tile)
                    good_fits += 1
        r = blob(frame, floor, near)
        if r is None or H is None:
            rows.append([i / fps, np.nan, np.nan, np.nan, np.nan])
            continue
        near = np.array(r[:2])
        # a second point a little way along the shape's long axis, so that
        # its direction goes through the same perspective as its position
        tip = (r[0] + 40 * math.cos(r[2]), r[1] + 40 * math.sin(r[2]))
        c, b = to_floor(H, [r[:2], tip])
        rows.append([i / fps, c[0], c[1], b[0], b[1]])
    if raw.exists():
        rows = np.load(raw)
    else:
        rows = np.array(rows)
        np.save(raw, rows)
    n = len(rows)
    good = np.isfinite(rows[:, 1])
    t = rows[:, 0]
    for k in range(1, 5):
        rows[~good, k] = np.interp(t[~good], t[good], rows[good, k])
    # picture y runs down, so flip one axis to get a right-handed floor
    cx, cy, bx, by = rows[:, 1], -rows[:, 2], rows[:, 3], -rows[:, 4]
    # the shape's long axis is the robot's, known to within a half-turn
    head = np.unwrap(2 * np.arctan2(by - cy, bx - cx)) / 2
    # when does it move? speed of the chassis centre
    sm = lambda v: np.convolve(v, np.ones(7) / 7, "same")
    speed = np.hypot(np.gradient(sm(cx)), np.gradient(sm(cy))) * fps
    turn = np.abs(np.gradient(sm(head))) * fps
    # The run is the last stretch with real movement in it. Small twitches of
    # the tracked centre while the robot sits still do not count.
    fast = np.flatnonzero(sm(speed) > 0.15)
    gaps = np.flatnonzero(np.diff(fast) > 4 * fps)
    first_fast = fast[gaps[-1] + 1] if len(gaps) else fast[0]
    i0 = max(0, first_fast - int(0.15 * fps))
    i1 = fast[-1]
    rest0 = slice(max(0, i0 - int(1.5 * fps)), max(1, i0 - 3))
    h0 = float(np.median(head[rest0]))
    # "ahead" is the way the robot first moves: from where it stood to where
    # it is 0.3 m later. The shape's long axis only gives that to a half-turn.
    sx, sy = float(np.median(cx[rest0])), float(np.median(cy[rest0]))
    gone = np.hypot(cx[i0:] - sx, cy[i0:] - sy)
    j = i0 + int(np.argmax(gone > 0.3))
    drive = math.atan2(cy[j] - sy, cx[j] - sx)
    offset = drive - h0
    heading = head + offset                                   # floor frame
    ax = cx + a.centre_to_axle * np.cos(heading)              # axle midpoint
    ay = cy + a.centre_to_axle * np.sin(heading)
    x0, y0 = float(np.median(ax[rest0])), float(np.median(ay[rest0]))
    c, s = math.cos(drive), math.sin(drive)
    X = (ax - x0) * c + (ay - y0) * s                         # ahead of the start
    Y = -(ax - x0) * s + (ay - y0) * c                        # to its left
    TH = np.degrees(heading - drive)
    end = slice(min(n - 2, i1 + int(0.4 * fps)), n)
    res = dict(video=a.video, fps=float(fps), run_starts_at_s=float(t[i0]),
               run_lasts_s=float(t[i1] - t[i0]),
               final_x_m=float(np.median(X[end])), final_y_m=float(np.median(Y[end])),
               final_heading_deg=float(np.median(TH[end]) % 360.0),
               total_rotation_deg=float(np.median(TH[end])),
               path_length_m=float(np.sum(np.hypot(np.diff(sm(X)[i0:i1]), np.diff(sm(Y)[i0:i1])))),
               furthest_ahead_m=float(X[i0:i1].max()), furthest_left_m=float(Y[i0:i1].max()),
               frames_without_a_reading=int((~good).sum()),
               position_scatter_at_rest_mm=float(1e3 * np.hypot(np.std(X[rest0]), np.std(Y[rest0]))))
    for k_, v in res.items():
        print(f"  {k_:<30} {v:.3f}" if isinstance(v, float) else f"  {k_:<30} {v}")
    path = np.column_stack([t - t[i0], X, Y, TH])[max(0, i0 - int(fps)):]
    np.save(Path(a.video).with_suffix(".path.npy"), path)
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1))
        print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
