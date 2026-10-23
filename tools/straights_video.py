"""Distance, drift and turn of each straight run in a batch video, from above.

    .venv/bin/python tools/straights_video.py VIDEO LOG1.csv ... LOG5.csv
        [--camera-height 1.9] [--out FILE.json]

For the repeated straights on robot 2 (results/robot2/PREREGISTRATION.md):
one top-down video holds a whole batch of runs, with someone putting the
robot back on its mark in between. tools/path_video.py expects one run per
video, so this is separate from it and leaves it as it was.

- **The ruler** is the tile joints (0.5 m), every one in view that runs
  cleanly across the picture, found in a frame with nobody in it. They fix
  the map from picture to floor, perspective included.
- **The robot** is found by its green chassis: the only green thing in shot,
  so legs, hands and shadows are not mistaken for it. Only the green is
  used, so a dark thing beside it cannot merge with it.
- **The runs** are the stretches where it drives itself: steady, in one
  direction, for about the length of a run. Being carried back to the mark
  is faster, shorter and the other way. The runs found are matched in order
  to the batch's logs, and the gaps between them are checked against the
  gaps between the logs, so a wrong match shows.
- **Each run** is measured from the robot at rest just before it starts and
  just after it stops. "Ahead" is the way it first moves. Distance is how
  far ahead it ended, drift how far to its left (negative: right), and the
  turn (counter-clockwise positive) is the change in its direction of travel between the first and
  last 0.25 m.

The camera sees the top of the robot, not its wheels on the floor. Mapped
onto the floor, anything above it moves too far, by camera height / (camera
height - its height). With --camera-height the distances and drifts are
scaled back by that, exactly (it is the same factor everywhere in the
picture for a pinhole camera looking down). Without it they are reported as
seen, and the factor is left to the analysis.
"""
import argparse
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from path_video import find_grid, homography, joints_mask, pick_regular, refit, to_floor  # noqa: E402

TOP_HEIGHT = 0.087      # m: robot 2's effective height seen from above, its
                        # parts weighted by area (tools/straights_analysis.py)
HALF = 0.5              # the robot is looked for at half size
BODY_OPEN = 31          # px: parts of the robot thinner than this are left out
ROBOT_PX = (7000, 22000)  # px at full size: the robot's body outline, from batch 1


def grid(frame, tile):
    """Homography from picture to floor using every regularly spaced joint."""
    ups, acrosses, (w, h) = find_grid(frame)
    bw = joints_mask(frame)
    best = None
    for nu in (4, 3, 2):
        if len(ups) < nu:
            continue
        for na in (4, 3):
            if len(acrosses) < na:
                continue
            try:
                u = pick_regular(ups, nu, w)
                a = pick_regular(acrosses, na, h)
            except TypeError:
                continue
            lu = [refit(bw, p, True) for p in u]
            la = [refit(bw, p, False) for p in a]
            if any(l is None for l in lu + la):
                continue
            best = (lu, la, u, a)
            break
        if best:
            break
    if best is None:
        raise SystemExit("could not find the tile joints")
    lu, la, u, a = best
    return homography(lu, la, tile), u, a


def track(cap, n):
    """Robot centre (picture px) and the long axis of its green chassis in
    every frame. Only the green is used: the robot may start right beside
    something dark (a leg, a bag, a shadow) that the dark parts would merge
    with, and nothing else in shot is green."""
    out = np.full((n, 3), np.nan)
    near = None
    for i in range(n):
        ok, f = cap.read()
        if not ok:
            break
        small = cv2.resize(f, None, fx=HALF, fy=HALF)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        # strongly coloured green, bright or in shade (the floor's shade is
        # grey, so it is not picked up however dark the green gets)
        green = ((hsv[..., 0] > 30) & (hsv[..., 0] < 90) & (hsv[..., 1] > 90)
                 & (hsv[..., 2] > 28)).astype(np.uint8)
        joined = cv2.morphologyEx(green, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        num, lab, st, ce = cv2.connectedComponentsWithStats(joined)
        if num < 2:
            continue
        area = st[:, cv2.CC_STAT_AREA].astype(float)
        area[0] = 0
        score = area if near is None else \
            area / (1.0 + (np.hypot(ce[:, 0] - near[0], ce[:, 1] - near[1]) / 80.0) ** 2)
        k = int(np.argmax(score))
        if area[k] < 40:
            continue
        sel = (lab == k) & (green > 0)
        mo = cv2.moments(sel.astype(np.uint8), True)
        if mo["m00"] < 20:
            continue
        cx, cy = mo["m10"] / mo["m00"], mo["m01"] / mo["m00"]
        ang = 0.5 * math.atan2(2 * mo["mu11"], mo["mu20"] - mo["mu02"])
        near = (cx, cy)
        out[i] = (cx / HALF, cy / HALF, ang)
    return out


def mean_frame(cap, frames):
    acc = None
    for k in frames:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(k))
        ok, f = cap.read()
        if ok:
            acc = f.astype(np.float32) if acc is None else acc + f
    return (acc / len(frames)).astype(np.uint8)


def whole_robot(rest, empty, hint, radius=125):
    """Centre of the whole robot (picture px) in `rest`, a still frame, found
    as what differs from `empty`, a still frame of the same view with the
    robot somewhere else. Only pieces near `hint` (where its green is) count,
    so anything else that changed between the two frames is left out."""
    d = cv2.absdiff(rest, empty).max(axis=2)
    fg = (d > 35).astype(np.uint8)
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((11, 11), np.uint8))
    near = np.zeros_like(fg)
    cv2.circle(near, (int(hint[0]), int(hint[1])), radius, 1, -1)
    fg &= near
    n, lab, st, ce = cv2.connectedComponentsWithStats(fg)
    if n < 2:
        return None
    # the piece under the green, or else the nearest to it (not the biggest:
    # a leg walking past is bigger than the robot)
    under = lab[int(hint[1]), int(hint[0])]
    if under:
        k = int(under)
    else:
        k = 1 + int(np.argmin(np.hypot(ce[1:, 0] - hint[0], ce[1:, 1] - hint[1])))
    # the body only: thin parts (antenna, ribbon loop, cables) show at one
    # end of a run and not the other, depending on what is behind them, and
    # would pull the centre about
    body = cv2.morphologyEx((lab == k).astype(np.uint8), cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (BODY_OPEN, BODY_OPEN)))
    n2, lab2, st2, _ = cv2.connectedComponentsWithStats(body)
    if n2 < 2:
        return None
    k2 = 1 + int(np.argmax(st2[1:, cv2.CC_STAT_AREA]))
    ys, xs = np.nonzero(lab2 == k2)
    return float(xs.mean()), float(ys.mean()), int(len(xs))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("logs", nargs="+", help="the batch's run logs, in order")
    ap.add_argument("--tile", type=float, default=0.5)
    ap.add_argument("--camera-height", type=float,
                    help="m, lens above the floor; corrects for seeing the robot's top")
    ap.add_argument("--out")
    a = ap.parse_args()

    # motors-on times from the logs (robot clock)
    on = []
    for p in a.logs:
        meta = {}
        for line in open(p):
            if not line.startswith("#"):
                break
            if "=" in line:
                k, v = line[1:].split("=", 1)
                meta[k.strip()] = v.strip()
        sent = [e.split(":") for e in meta["commands_sent_at"].split()]
        t_on = next(float(t) for t, l, r in sent if float(l) or float(r))
        t_off = next(float(t) for t, l, r in sent if float(t) > t_on and not (float(l) or float(r)))
        on.append((float(meta["started"]) + t_on, t_off - t_on))

    cap = cv2.VideoCapture(a.video)
    fps = cap.get(cv2.CAP_PROP_FPS)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cache = Path(a.video).with_suffix(".track.npy")
    if cache.exists():
        tr = np.load(cache)
    else:
        tr = track(cap, n)
        np.save(cache, tr)
    t = np.arange(len(tr)) / fps

    # the ruler: a frame with the robot at rest and nobody about, taken as
    # one where the robot's mask has not moved for a while (first run, 1 s
    # before it starts is found below; start with the middle frame)
    good = np.isfinite(tr[:, 0])
    px = tr[:, 0].copy()
    py = tr[:, 1].copy()
    px[~good] = np.interp(t[~good], t[good], px[good])
    py[~good] = np.interp(t[~good], t[good], py[good])

    def find_runs(H):
        fx, fy = to_floor(H, np.column_stack([px, py])).T
        sm = lambda v: np.convolve(v, np.ones(5) / 5, "same")
        vx, vy = np.gradient(sm(fx)) * fps, np.gradient(sm(fy)) * fps
        speed = np.hypot(vx, vy)
        moving = speed > 0.12
        runs, s = [], None
        for i, mv in enumerate(np.r_[moving, False]):
            if mv and s is None:
                s = i
            elif not mv and s is not None:
                if i - s > 0.4 * fps:
                    runs.append((s, i))
                s = None
        return fx, fy, speed, runs

    cap.set(cv2.CAP_PROP_POS_FRAMES, n // 2)
    ok, ref = cap.read()
    H, ups, acs = grid(ref, a.tile)
    fx, fy, speed, bursts = find_runs(H)

    # The runs are picked by timing: the logs give the gaps between the
    # batch's runs on the robot's clock, and the bursts of motion that start
    # with exactly those gaps are the runs. (Being carried back to the mark
    # covers much the same distance in much the same time, so distance and
    # duration alone cannot tell the two apart.)
    log_starts = np.array([o[0] for o in on])
    starts = np.array([t[s_] for s_, e_ in bursts])
    best = None
    for b in starts:
        for j in range(len(log_starts)):
            off = b - log_starts[j]
            want = log_starts + off
            pick, err = [], []
            for w_ in want:
                i = int(np.argmin(np.abs(starts - w_)))
                pick.append(i)
                err.append(abs(starts[i] - w_))
            got = sum(e_ < 0.4 for e_ in err)
            if best is None or (got, -sum(err)) > (best[0], -best[1]):
                best = (got, sum(err), pick, off, err)
    got, _, pick, offset, err = best
    print(f"{n} frames at {fps:.2f} fps; joints up at x = {[round(u) for u in ups]}, "
          f"across at y = {[round(v) for v in acs]}; {len(bursts)} bursts of motion, "
          f"{got} of {len(a.logs)} logs matched by timing")
    if got != len(a.logs) or len(set(pick)) != len(pick):
        for (s_, e_), st_ in zip(bursts, starts):
            print(f"   burst at {st_:.1f}-{t[e_ - 1]:.1f} s")
        raise SystemExit("the runs found do not match the logs one for one")
    runs = []
    for i in pick:
        s_, e_ = bursts[i]
        d = math.hypot(fx[e_ - 1] - fx[s_], fy[e_ - 1] - fy[s_])
        runs.append((s_, e_, d, math.atan2(fy[e_ - 1] - fy[s_], fx[e_ - 1] - fx[s_])))
        if not 0.5 < d < 2.0:
            raise SystemExit(f"the burst at {t[s_]:.1f} s, matched to a log, moved {d:.2f} m")
    print(f"matched to the logs: each run starts where its log says, to "
          f"{max(err):.2f} s")

    scale = 1.0
    if a.camera_height:
        scale = (a.camera_height - TOP_HEIGHT) / a.camera_height

    results = []
    for k, ((s, e, d, ang), (t_on, dur_on)) in enumerate(zip(runs, on)):
        # at rest before: the second before it starts; after: the first
        # still stretch after it stops, up to 1 s
        b0, b1 = max(0, s - int(1.0 * fps)), max(1, s - int(0.1 * fps))
        still = np.flatnonzero(speed[e:e + int(3 * fps)] < 0.03)
        a0 = e + (still[0] if len(still) else int(0.3 * fps))
        a1 = min(len(fx), a0 + int(0.8 * fps))
        # stop the "after" window if something else moves the robot
        jump = np.flatnonzero(speed[a0:a1] > 0.1)
        if len(jump):
            a1 = a0 + max(3, jump[0])
        # where it was at rest, first from the green track, then refined to
        # the whole robot's centre: the still frame before against the still
        # frame after (each shows the floor where the robot is in the other)
        # A foot beside the robot, or the robot in deep shade, gives an
        # outline far from the robot's size; then a shorter still moment
        # nearer the run is tried instead.
        def robot_sized(w):
            return w is not None and ROBOT_PX[0] < w[2] < ROBOT_PX[1]
        tries_b = [(b0, b1), (max(0, s - int(0.4 * fps)), max(1, s - 2))]
        tries_a = [(a0, a1), (a0, min(len(fx), a0 + int(0.3 * fps)))]
        w0 = w1 = None
        for (p0, p1) in tries_b:
            for (q0, q1) in tries_a:
                before = mean_frame(cap, np.linspace(p0, p1 - 1, 5))
                after = mean_frame(cap, np.linspace(q0, q1 - 1, 5))
                c0 = whole_robot(before, after, (np.median(px[p0:p1]), np.median(py[p0:p1])))
                c1 = whole_robot(after, before, (np.median(px[q0:q1]), np.median(py[q0:q1])))
                if robot_sized(c0) and robot_sized(c1):
                    w0, w1, b0, b1, a0, a1 = c0, c1, p0, p1, q0, q1
                    break
            if w0 is not None:
                break
        if w0 is None:
            print(f"  {Path(a.logs[k]).name}: could not pick out the robot at rest "
                  f"(outline {c0 and c0[2]}/{c1 and c1[2]} px); left out")
            results.append(dict(log=Path(a.logs[k]).name, video_start_s=float(t[s]),
                                failed="no robot-sized outline at rest"))
            continue
        (sx, sy), (ex, ey) = to_floor(H, [w0[:2], w1[:2]])
        gx0, gy0 = np.median(fx[b0:b1]), np.median(fy[b0:b1])
        gx1, gy1 = np.median(fx[a0:a1]), np.median(fy[a0:a1])
        # ahead: the way it first moves (start to 0.3 m along)
        gone = np.hypot(fx[s:e] - gx0, fy[s:e] - gy0)
        j = s + int(np.argmax(gone > 0.3))
        hd = math.atan2(fy[j] - gy0, fx[j] - gx0)
        c_, s_ = math.cos(hd), math.sin(hd)
        X = ((ex - sx) * c_ + (ey - sy) * s_) * scale
        # floor y from the map runs the way picture y does (down), so left of
        # the direction of travel is the negative cross product here
        Y = -(-(ex - sx) * s_ + (ey - sy) * c_) * scale
        # turn: the change in the direction of travel, from the first 0.25 m
        # to the last. A two-wheeled robot does not slide sideways, so that
        # is how far it turned. (The green outline's own axis is no good for
        # this: only parts of the chassis show from above.)
        along = np.r_[0.0, np.cumsum(np.hypot(np.diff(fx[s:a0]), np.diff(fy[s:a0])))]
        total = along[-1]
        i_a = s + int(np.argmax(along > 0.25))
        i_b = s + int(np.argmax(along > total - 0.25))
        d_end = math.atan2(gy1 - fy[i_b], gx1 - fx[i_b])
        d_start = math.atan2(fy[i_a] - gy0, fx[i_a] - gx0)
        # floor y runs down the picture, so counter-clockwise is minus
        turn = -math.degrees((d_end - d_start + math.pi) % (2 * math.pi) - math.pi)
        scatter = 1e3 * float(np.hypot(np.std(fx[b0:b1]), np.std(fy[b0:b1])))
        results.append(dict(log=Path(a.logs[k]).name, video_start_s=float(t[s]),
                            distance_m=float(X), drift_m=float(Y), heading_deg=turn,
                            rest_scatter_mm=scatter, motors_on_s=dur_on,
                            robot_px_before_after=[w0[2], w1[2]],
                            green_vs_whole_shift_mm=float(1e3 * (math.hypot(gx1 - gx0, gy1 - gy0)
                                                                 - math.hypot(ex - sx, ey - sy))),
                            frames_without_robot=int((~good[b0:a1]).sum())))
        print(f"  {Path(a.logs[k]).name}: {X:.3f} m ahead, {Y * 100:+.1f} cm left, "
              f"turned {turn:+.1f} deg (robot {w0[2]}/{w1[2]} px before/after; the "
              f"green alone said {math.hypot(gx1 - gx0, gy1 - gy0) * scale:.3f} m)")
    if a.out:
        Path(a.out).write_text(json.dumps({
            "video": a.video, "fps": fps, "camera_height_m": a.camera_height,
            "top_height_m": TOP_HEIGHT if a.camera_height else None,
            "height_scale_applied": scale, "clock_offset_s": float(offset),
            "runs": results}, indent=1))
        print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
