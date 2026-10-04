"""Wheel speed from a phone video of the wheels-up sweep.

    .venv/bin/python tools/wheel_video.py VIDEO [--offset SECONDS]

The robot has no wheel-speed sensor, so this is the measurement. It uses the
two things a video has:

  the picture   The hub has eight identical holes. Sampling brightness around
                the ring they sit on gives a pattern whose phase is the
                wheel's angle, modulo 45 degrees. Frame to frame that gives
                the speed -- but only below 22.5 degrees per frame (112 rpm
                at 30 fps). Above that the holes alias.
  the sound     The motor's pitch is its rotor's turning rate, and the rotor
                turns 48 times per wheel turn. No frame-rate limit.

The picture calibrates the sound on the slow holds, where both work; the
sound then gives the fast holds; and the picture's aliased reading checks
each of those, since it must agree with the sound modulo 225 rpm.
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
import scipy.io.wavfile as wavfile
import scipy.signal as sg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from jetbot_twin.experiments import EXPERIMENTS, holds  # noqa: E402

HOLES = 8
GEAR = 48.0


def ring_phase(frame):
    """Angle of the hole pattern, modulo 360/HOLES degrees, and how clearly
    the pattern shows (0..1). None if the hub cannot be found."""
    small = cv2.resize(frame, (480, 480))
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    white = ((hsv[..., 2] > 170) & (hsv[..., 1] < 70)).astype(np.uint8)
    white = cv2.morphologyEx(white, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(white)
    if n < 2:
        return None
    k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    area = stats[k, cv2.CC_STAT_AREA]
    if area < 8000:
        return None
    cx, cy = cent[k]
    R = np.sqrt(area / np.pi)                      # the hub, holes closed over
    grey = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(float)
    th = np.linspace(0, 2 * np.pi, 720, endpoint=False)
    best = (0.0, 0.0)
    for frac in (0.50, 0.56, 0.62):                # the ring the holes sit on
        x = np.clip(cx + frac * R * np.cos(th), 0, 479).astype(int)
        y = np.clip(cy + frac * R * np.sin(th), 0, 479).astype(int)
        ring = grey[y, x]
        F = np.fft.rfft(ring - ring.mean())
        strength = np.abs(F[HOLES]) / (np.abs(F[1:40]).sum() + 1e-9)
        if strength > best[1]:
            best = (np.angle(F[HOLES]), strength)
    return np.degrees(best[0]) / HOLES, best[1], (cx, cy, R)


def picture(path):
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS)
    t, ang, strength = [], [], []
    k = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        r = ring_phase(frame)
        t.append(k / fps)
        ang.append(r[0] if r else np.nan)
        strength.append(r[1] if r else 0.0)
        k += 1
    return np.array(t), np.array(ang), np.array(strength), fps


def sound(path):
    wav = Path(tempfile.mkdtemp()) / "a.wav"
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@44100", "-c", "1",
                    str(path), str(wav)], check=True, capture_output=True)
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fs, x = wavfile.read(wav)
    x = x.astype(float)
    return fs, x / np.abs(x).max()


def find_offset(fs, x, exp):
    """Seconds into the video at which the experiment's clock starts."""
    n = int(0.02 * fs)
    env = np.sqrt(np.convolve(x ** 2, np.ones(n) / n, "same"))[::n]
    te = np.arange(len(env)) * 0.02
    loud = (env > 4 * np.percentile(env, 10)).astype(float)
    best = (0.0, -1.0)
    for off in np.arange(0.0, max(0.1, te[-1] - exp.duration + 3), 0.02):
        want = np.zeros_like(loud)
        for a, b in holds(exp):
            want[(te >= off + a) & (te < off + b)] = 1.0
        score = float((want * loud).sum() - (want * (1 - loud)).sum()
                      - ((1 - want) * loud).sum())
        if score > best[1]:
            best = (off, score)
    return best[0]


def spectrum(fs, x, t0, t1):
    seg = x[int(t0 * fs):int(t1 * fs)]
    f, p = sg.welch(seg, fs, nperseg=16384, noverlap=12288)
    return f, p


def comb_score(f, p, f0, n=6):
    """How much of the spectrum sits on the harmonics of f0."""
    df = f[1] - f[0]
    floor = np.median(p[(f > 80) & (f < 4000)])
    s = 0.0
    for h in range(1, n + 1):
        k = int(round(h * f0 / df))
        if k + 3 >= len(p):
            break
        s += np.log(p[k - 2:k + 3].max() / floor)
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--experiment", default="wheels_up_sweep")
    ap.add_argument("--out")
    a = ap.parse_args()
    exp = EXPERIMENTS[a.experiment]
    fs, x = sound(a.video)
    off = find_offset(fs, x, exp)
    t, ang, strength, fps = picture(a.video)
    limit = 60.0 * fps / (2 * HOLES)               # rpm the picture can follow
    alias = 60.0 * fps / HOLES                     # and what it wraps by
    print(f"video {fps:.2f} fps; the experiment's clock starts {off:.2f} s in")
    print(f"the picture follows the wheel up to {limit:.0f} rpm and wraps every {alias:.0f} rpm\n")

    cmds = [abs(seg[2]) for seg in exp.profile if seg[1] or seg[2]]
    rows = []
    for cmd, (h0, h1) in zip(cmds, holds(exp)):
        m = (t >= off + h0 + 0.7) & (t <= off + h1 - 0.1) & np.isfinite(ang)
        d = np.diff(ang[m])
        d = (d + 180.0 / HOLES) % (360.0 / HOLES) - 180.0 / HOLES   # wrap to +/-22.5
        step = float(np.median(d)) if len(d) else np.nan            # degrees per frame
        spread = float(np.std(d)) if len(d) else np.nan
        pic = step * fps / 6.0                                      # rpm, aliased, signed
        f, p = spectrum(fs, x, off + h0 + 0.7, off + h1 - 0.1)
        rows.append(dict(cmd=cmd, pic=pic, spread=spread * fps / 6.0,
                         clarity=float(np.median(strength[m])) if m.any() else 0.0,
                         f=f, p=p))

    # --- the picture gives each hold's speed modulo `alias`. Speed rises with
    #     command, so take, for each hold in turn, the lowest reading that is
    #     not below the one before. THAT IS AN ASSUMPTION, and the only one:
    #     the alternative at any hold is a jump of a whole 225 rpm.
    sign = np.sign(np.nanmedian([r["pic"] for r in rows[:3]])) or 1.0
    prev = 0.0
    for r in rows:
        base = (r["pic"] * sign) % alias
        c = base
        while c < prev - 0.04 * alias:
            c += alias
        r["rpm_pic"], r["wraps"] = c, int(round((c - base) / alias))
        prev = c

    # --- the sound: look for the rotor's tone near where the picture says,
    #     and separately with no hint at all
    def tone(row, lo, hi):
        f0s = np.arange(lo, hi, 0.25)
        sc = [comb_score(row["f"], row["p"], f0) for f0 in f0s]
        k = int(np.argmax(sc))
        return f0s[k], sc[k]

    print(" cmd   picture   wraps   sound near it   agree?   scatter")
    out = []
    for r in rows:
        c = r["rpm_pic"]
        rotor = c * GEAR / 60.0
        f0, sc = tone(r, 0.88 * rotor, 1.12 * rotor) if rotor > 25 else (np.nan, 0.0)
        rpm_sound = f0 * 60.0 / GEAR
        agree = bool(np.isfinite(rpm_sound) and abs(rpm_sound - c) < 0.05 * c + 4)
        out.append(dict(cmd=r["cmd"], rpm=float(c), wraps=r["wraps"],
                        sound_rpm=float(rpm_sound), scatter_rpm=float(r["spread"]),
                        sound_agrees=agree))
        print(f"{r['cmd']:5.2f}  {c:6.1f} rpm   {r['wraps']}     {rpm_sound:7.1f} rpm     "
              f"{'yes' if agree else 'no '}     +/-{r['spread']:.0f} rpm")
    if a.out:
        Path(a.out).write_text(json.dumps(
            {"video": str(a.video), "offset_s": off, "fps": fps, "holds": out}, indent=1))
        print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
