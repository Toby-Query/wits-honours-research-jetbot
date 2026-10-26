"""Each turn or spin in one top-down video, against the twin.

    .venv/bin/python tools/turns_video.py VIDEO LOG.csv --quarters 1 \\
        [--calibration results/calibrated_robot2_caster.json] [--sheet S.jpg] [--out F.json]

For experiments made of several turns with rests between them (robot 2's
`short_turns_24` and `spins_10`; plans in results/robot2/). The robot rests
between turns, so each turn's angle is a whole number of quarter-turns plus
the change in the angle of its boards' straight edges from the rest before to
the rest after (tools/spin_shape.py, -45 to +45 deg). --quarters is that whole
number for every turn (1 for a short turn, 11 for a 2 s spin); --sheet writes a
strip of frames through each turn so the count can be checked by eye.

The turns are found in the video by matching the start of each burst of
motion to the logged command times, then each is compared with the twin
replayed on the run's own logged timing and rest voltage.
"""
import argparse
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from jetbot_twin.compare import as_executed, replay  # noqa: E402
from jetbot_twin.experiments import EXPERIMENTS  # noqa: E402
from jetbot_twin.hardware import DEFAULT  # noqa: E402
from jetbot_twin.twin import Log  # noqa: E402
from spin_shape import W, centre_of, read, rest_angle, robot_mask  # noqa: E402


def turns_from_log(log):
    sent = [e.split(":") for e in log.meta["commands_sent_at"].split()]
    ev = [(float(t), float(l), float(r)) for t, l, r in sent]
    out = []
    for (t0, l, r), (t1, _, _) in zip(ev[:-1], ev[1:]):
        if l or r:
            out.append((t0, t1))
    return out


def motion_signal(cap, fps, every=2):
    """(times, how much the robot's outline changed since the last sample)."""
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    prev, ts, d = None, [], []
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    for k in range(n):
        ok = cap.grab()
        if not ok:
            break
        if k % every:
            continue
        ok, f = cap.retrieve()
        scale = W / f.shape[1]
        m = robot_mask(cv2.resize(f, (W, int(round(f.shape[0] * scale)))))
        if prev is not None:
            d.append(float((m ^ prev).sum()) / max(1, m.sum()))
            ts.append(k / fps)
        prev = m
    return np.array(ts), np.array(d)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("log")
    ap.add_argument("--quarters", type=int, required=True)
    ap.add_argument("--calibration", default=str(ROOT / "results" / "calibrated_robot2_caster.json"))
    ap.add_argument("--sheet")
    ap.add_argument("--video-start", help="HH:MM:SS the phone started recording (its file "
                    "name, e.g. 20261026_113647 -> 11:36:47); turns repeat every second or "
                    "two, so without it the match can land a whole turn out")
    ap.add_argument("--out")
    a = ap.parse_args()

    log = Log.read_csv(a.log)
    exp = EXPERIMENTS[log.meta["experiment"]]
    turns = turns_from_log(log)
    cap = cv2.VideoCapture(a.video)
    fps = cap.get(cv2.CAP_PROP_FPS)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # Where the log sits on the video's clock: slide the logged pattern of
    # motors on and off along the video's motion signal and keep the offset
    # at which motion lines up best with the motors being on. (Picking out
    # starts of motion and matching them failed: the outline flickers a
    # little at rest, and turns repeat so regularly that a match one turn out
    # scores almost as well.)
    ts, d = motion_signal(cap, fps)
    on_mask = lambda off: np.any([(ts >= t0 + off) & (ts <= t1 + 0.15 + off)
                                  for t0, t1 in turns], axis=0)
    hint = None
    if a.video_start:
        import datetime as dt
        started = dt.datetime.fromtimestamp(float(log.meta["started"]))
        h, m_, s_ = (int(x) for x in a.video_start.split(":"))
        vstart = started.replace(hour=h, minute=m_, second=s_, microsecond=0)
        hint = (started - vstart).total_seconds()          # log t=0 on the video clock
    span = turns[-1][1] + 1.0
    lo_, hi_ = (hint - 1.5, hint + 1.5) if hint is not None else (0.0, ts[-1] - span)
    best = None
    for off in np.arange(lo_, hi_, 0.01):
        m = on_mask(off)
        inside = (ts >= off) & (ts <= off + span)
        if m.sum() == 0 or (inside & ~m).sum() == 0:
            continue
        score = float(d[m].mean() - d[inside & ~m].mean())
        if best is None or score > best[0]:
            best = (score, off)
    if best is None:
        raise SystemExit("could not place the log on the video")
    offset = float(best[1])
    print(f"{n} frames at {fps:.2f} fps; {len(turns)} turns in the log; video clock = "
          f"log clock + {offset:.2f} s (motion while on, less at rest: {best[0]:.2f})")

    cal = {k: v for k, v in json.load(open(a.calibration)).items() if not k.startswith("_")}
    hw = DEFAULT.with_(board="tb6612", **cal)
    twin_log, _ = replay(exp, log, hw)
    th = twin_log["th"]
    tt = twin_log.t

    def twin_heading(t):
        return math.degrees(float(np.interp(t, tt, np.unwrap(th))))

    rows, tiles = [], []
    for k, (t0, t1) in enumerate(turns):
        nxt = turns[k + 1][0] if k + 1 < len(turns) else t1 + 2.0
        prv = turns[k - 1][1] if k else 0.0
        # at rest: before = the last 0.5 s before the command, after = from
        # 0.5 s after the stop until just before the next command
        b = np.linspace((max(prv + 0.6, t0 - 0.7) + offset) * fps, (t0 - 0.25 + offset) * fps, 7)
        e = np.linspace((t1 + 0.6 + offset) * fps, (min(nxt - 0.25, t1 + 1.6) + offset) * fps, 7)
        b = [int(x) for x in b if 0 <= x < n]
        e = [int(x) for x in e if 0 <= x < n]
        a0, s0, n0 = rest_angle(cap, b)
        a1, s1, n1 = rest_angle(cap, e)
        frac = (-(a1 - a0) + 45.0) % 90.0 - 45.0
        robot = 90.0 * a.quarters + frac
        twin = twin_heading(t1 + 0.9) - twin_heading(t0 - 0.05)
        rows.append(dict(turn=k + 1, robot_deg=robot, twin_deg=twin, fraction_deg=frac,
                         edge_spread_deg=[s0, s1], command_s=t1 - t0))
        print(f"  turn {k + 1:2d}: robot {robot:7.1f} deg, twin {twin:7.1f}, "
              f"command {1e3 * (t1 - t0):.0f} ms, edges spread {max(s0, s1):.1f}")
        if a.sheet:
            for tsec in np.linspace(t0 - 0.1, t1 + 0.4, 9):
                f = read(cap, int((tsec + offset) * fps))
                c = centre_of(f) or (f.shape[1] / 2, f.shape[0] / 2)
                r = int(0.16 * f.shape[1])
                pad = cv2.copyMakeBorder(f, r, r, r, r, cv2.BORDER_CONSTANT)
                x, y = int(c[0]), int(c[1])
                tile = cv2.resize(pad[y:y + 2 * r, x:x + 2 * r], (110, 110))
                tiles.append(tile)
            lab = np.full((110, 50, 3), 255, np.uint8)
            cv2.putText(lab, str(k + 1), (6, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
            tiles.insert(len(tiles) - 9, lab)
    if a.sheet:
        rows_img = [np.hstack(tiles[i:i + 10]) for i in range(0, len(tiles), 10)]
        cv2.imwrite(a.sheet, np.vstack(rows_img))
        print(f"wrote {a.sheet}")

    r = np.array([x["robot_deg"] for x in rows])
    t = np.array([x["twin_deg"] for x in rows])
    d = r - t
    h = 2.069 * d.std(ddof=1) / math.sqrt(len(d)) if len(d) > 20 else 2.262 * d.std(ddof=1) / math.sqrt(len(d))
    print(f"\nrobot {r.mean():.1f} deg (sd {r.std(ddof=1):.1f}, range {r.min():.1f}-{r.max():.1f}); "
          f"twin {t.mean():.1f}; robot minus twin {d.mean():+.1f} deg "
          f"({d.mean() / t.mean() * 100:+.1f} %, 95 % {d.mean() - h:+.1f} to {d.mean() + h:+.1f})")
    lo, hi = np.percentile(r, [2.5, 97.5])
    print(f"twin inside the robot's middle 95 % ({lo:.1f} to {hi:.1f})? "
          f"{'yes' if lo <= t.mean() <= hi else 'no'}")
    if a.out:
        Path(a.out).write_text(json.dumps({"video": a.video, "log": a.log,
                                           "quarters": a.quarters, "turns": rows}, indent=1))
        print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
