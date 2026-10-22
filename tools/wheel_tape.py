"""Wheel speed from a slow-motion video of a wheel with a strip of tape on it.

    .venv/bin/python tools/wheel_tape.py VIDEO [--out FILE.json]

One dark strip on the white hub is a once-per-turn marker, so the wheel's
angle is read directly in every frame and unwrapped; at slow-motion frame
rates the wheel moves a few degrees per frame and nothing aliases. This is
the measurement to prefer over tools/wheel_video.py, which has to work from
the hub's eight identical holes.

A slow-motion file does not say how fast it was really shot (it plays at 30
fps). The experiment does: its holds start exactly 3 s apart on the robot's
clock, so the true frame rate is read off the spacing of the starts.
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from jetbot_twin.experiments import EXPERIMENTS, holds  # noqa: E402


W = 480


def shrink(frame):
    return cv2.resize(frame, (W, int(round(W * frame.shape[0] / frame.shape[1]))))


def hub(small):
    """(cx, cy, R) of the white hub, from the bounding box of the largest
    bright, colourless region; None if there is not one."""
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    white = ((hsv[..., 2] > 150) & (hsv[..., 1] < 80)).astype(np.uint8)
    n, lab, stats, cent = cv2.connectedComponentsWithStats(white)
    if n < 2:
        return None
    k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    if stats[k, cv2.CC_STAT_AREA] < 4000:
        return None
    x0, y0, w, h = stats[k, :4]
    return x0 + w / 2.0, y0 + h / 2.0, max(w, h) / 2.0


def tape_angle(small, cx, cy, R):
    """Angle of the dark strip on the hub's rim, radians."""
    grey = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(float)
    th = np.linspace(0, 2 * np.pi, 360, endpoint=False)
    dark = np.zeros(360)
    for frac in (0.74, 0.80, 0.86):                  # the solid rim, outside the holes
        x = np.clip(cx + frac * R * np.cos(th), 0, small.shape[1] - 1).astype(int)
        y = np.clip(cy + frac * R * np.sin(th), 0, small.shape[0] - 1).astype(int)
        ring = grey[y, x]
        dark += np.clip(np.median(ring) - ring - 25, 0, None)
    if dark.sum() <= 0:
        return None
    return float(np.arctan2((dark * np.sin(th)).sum(), (dark * np.cos(th)).sum()))


def angles(path):
    """The tape's angle in every frame, unwrapped.

    Two passes. The tape hides part of the rim, so a single frame's estimate
    of where the hub is moves as the tape goes round; that would put a
    once-per-turn wobble into the angle. The first pass finds the hub in a
    sample of frames and takes a running median, which is where the hub
    really is (the phone is on a stand); the second reads the tape."""
    cap = cv2.VideoCapture(str(path))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    step = 12
    idx, found = [], []
    k = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if k % step == 0:
            ok, frame = cap.retrieve()
            h = hub(shrink(frame)) if ok else None
            if h:
                idx.append(k)
                found.append(h)
        k += 1
    found = np.array(found)
    win = 41
    pad = np.pad(found, ((win // 2, win // 2), (0, 0)), mode="edge")
    smooth = np.array([np.median(pad[i:i + win], axis=0) for i in range(len(found))])
    # the largest box seen nearby is the whole hub; smaller ones had the tape
    # cutting into an edge
    smooth[:, 2] = np.array([np.percentile(pad[i:i + win, 2], 90) for i in range(len(found))])
    centre = [np.interp(np.arange(k), idx, smooth[:, j]) for j in range(3)]
    cap = cv2.VideoCapture(str(path))
    out, last = [], 0.0
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        a = tape_angle(shrink(frame), centre[0][i], centre[1][i], centre[2][i])
        last = a if a is not None else last
        out.append(last)
        i += 1
    return np.unwrap(np.array(out))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--experiment", default="wheels_up_sweep")
    ap.add_argument("--out")
    a = ap.parse_args()
    exp = EXPERIMENTS[a.experiment]
    cache = Path(a.video).with_suffix(".angles.npy")
    if cache.exists():
        th = np.load(cache)
    else:
        th = angles(a.video)
        np.save(cache, th)
    n = len(th)
    # speed in turns per frame, smoothed over 25 frames
    k = 25
    rate = np.abs(np.convolve(np.gradient(th), np.ones(k) / k, "same")) / (2 * np.pi)
    moving = rate > max(2e-4, 0.25 * np.percentile(rate, 60))
    if not moving.any():
        raise SystemExit("the wheel never appears to turn: is the tape visible on the hub's rim?")
    # The holds are the long runs of motion. How long "long" is depends on
    # the frame rate (a 2 s hold is 120 frames at 60 fps, 480 in slow
    # motion), so each usual phone rate is tried and the one that finds as
    # many holds as the experiment has, evenly spaced, is kept.
    want = holds(exp)
    period = want[1][0] - want[0][0]

    def find_runs(fps_guess):
        runs, start = [], None
        for i in range(n + 1):
            mv = moving[i] if i < n else False
            if mv and start is None:
                start = i
            elif not mv and start is not None:
                runs.append((start, i))
                start = None
        merged = []
        for s_, e_ in runs:                        # join brief stalls
            if merged and s_ - merged[-1][1] < 0.6 * fps_guess:
                merged[-1] = (merged[-1][0], e_)
            else:
                merged.append((s_, e_))
        return [(s_, e_) for s_, e_ in merged if e_ - s_ > 0.8 * fps_guess]

    def original_rule():
        # what was used for robot 1's slow-motion sweeps: runs over 200
        # frames, joined across stalls under 150 frames if that is needed
        runs, start = [], None
        for i in range(n + 1):
            mv = moving[i] if i < n else False
            if mv and start is None:
                start = i
            elif not mv and start is not None:
                if i - start > 200:
                    runs.append((start, i))
                start = None
        if len(runs) != len(want) and runs:
            merged = [runs[0]]
            for s_, e_ in runs[1:]:
                if s_ - merged[-1][1] < 150:
                    merged[-1] = (merged[-1][0], e_)
                else:
                    merged.append((s_, e_))
            runs = merged
        return runs

    best = None
    runs = original_rule()
    if len(runs) == len(want):
        best = (0.0, "slow-motion", runs)
    for fps_guess in (() if best else (240, 120, 60, 30)):
        runs = find_runs(fps_guess)
        if len(runs) != len(want):
            continue
        gaps = np.diff([s_ for s_, e_ in runs])
        spread = np.std(gaps) / np.median(gaps)
        if best is None or spread < best[0]:
            best = (spread, fps_guess, runs)
    if best is None:
        raise SystemExit(f"could not split the video into {len(want)} holds")
    runs = best[2]
    print(f"{n} frames; found {len(runs)} holds of motion ({best[1]} rule)")
    starts = np.array([s for s, e in runs], float)
    period = want[1][0] - want[0][0]
    fps = float(np.median(np.diff(starts)) / period) if len(starts) > 2 else float("nan")
    jitter = float(np.std(np.diff(starts)) / fps) if len(starts) > 2 else float("nan")
    print(f"true frame rate {fps:.1f} fps (hold starts are {period:g} s apart; "
          f"their spacing varies by {jitter * 1e3:.0f} ms)\n")
    cmds = [abs(seg[2]) for seg in exp.profile if seg[1] or seg[2]]
    hold_len = want[0][1] - want[0][0]
    out = []
    print(" cmd    speed      steadiness   spin-up   stops in   turns in all   misread")
    for cmd, (s, e) in zip(cmds, runs):
        on_end = s + int(hold_len * fps)                       # when the command ended
        a0, a1 = s + int(0.8 * fps), on_end - int(0.05 * fps)  # the settled part
        # The median step from frame to frame, each step wrapped to a half
        # turn. A frame where the tape is misread (blur, a hole) gives one
        # wild step; in a straight-line fit through the unwrapped angle it
        # can add or drop a whole turn, here it is simply outvoted.
        step = np.angle(np.exp(1j * np.diff(th[a0:a1])))
        typical = np.median(step)
        rpm = abs(typical) / (2 * np.pi) * fps * 60.0
        misread = np.abs(step - typical) > max(0.5 * abs(typical), np.radians(2))
        clean = np.where(misread, typical, step)
        inst = np.abs(clean) / (2 * np.pi) * fps * 60.0
        inst = np.convolve(inst, np.ones(int(fps / 10)) / int(fps / 10), "valid")
        wobble = float(np.std(inst))
        bad_frac = float(np.mean(misread))
        seg = rate[s:on_end] * fps * 60.0
        up = np.flatnonzero(seg > 0.63 * rpm)
        t_up = float(up[0] / fps) if len(up) else float("nan")
        t_stop = max(0.0, (e - on_end) / fps)
        turns = abs(th[min(e + 50, n - 1)] - th[max(s - 50, 0)]) / (2 * np.pi)
        out.append(dict(cmd=cmd, rpm=float(rpm), wobble_rpm=wobble, spin_up_s=t_up,
                        stop_s=float(t_stop), turns=float(turns),
                        misread_frac=bad_frac))
        print(f"{cmd:5.2f}  {rpm:6.1f} rpm   +/-{wobble:4.1f} rpm   {t_up * 1e3:5.0f} ms   "
              f"{t_stop * 1e3:5.0f} ms   {turns:7.2f}      {bad_frac * 100:4.1f} %")
    if a.out:
        Path(a.out).write_text(json.dumps(
            {"video": str(a.video), "true_fps": fps, "holds": out}, indent=1))
        print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
