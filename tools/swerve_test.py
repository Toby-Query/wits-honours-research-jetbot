"""What kind of fault makes robot 2 swerve? Three hypotheses, tested in the twin.

    .venv/bin/python tools/swerve_test.py

Ten of robot 2's fifty straights ended 26 to 49 cm left and turned 30 to 60
degrees anticlockwise. A swerve's shape says when the turning happened:

    weak   the left wheel is slower by a fraction e for the whole run: the
           path is an arc, and drift is about half of distance x turn
    strong the right wheel is faster by e for the whole run: an arc again,
           but the robot loses less distance
    split  left slower by e/2 and right faster by e/2: an arc at the
           commanded mean speed
    late   the left wheel starts d seconds after the right: the robot turns
           first, then drives straight, and drift is about distance x turn
    early  the left wheel stops d seconds before the right: it drives
           straight, then turns, and drift is small

Each hypothesis is one number per run. The twin (robot 2, caster refitted)
traces the (drift, turn) each would give over a range of that number; each
swerve is then matched to the nearest point on each curve, in units of the
video's measurement error (5 cm of drift, 5 degrees of turn). The hypothesis
whose curve passes through the swerves explains them; one that misses them
cannot, whatever its number.

Writes results/robot2/swerve_test.json.
"""
import json
import math
import sys
from dataclasses import replace
from multiprocessing import Pool
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jetbot_twin.compare import estimate_soc  # noqa: E402
from jetbot_twin.experiments import EXPERIMENTS, run_on_twin  # noqa: E402
from jetbot_twin.hardware import DEFAULT  # noqa: E402
from jetbot_twin.twin import JetBotTwin  # noqa: E402

DRIFT_TOL, TURN_TOL = 0.05, 5.0
SWERVE_LINE = 0.25


def hardware():
    cal = {k: v for k, v in json.load(open(ROOT / "results" / "calibrated_robot2_caster.json")).items()
           if not k.startswith("_")}
    hw = DEFAULT.with_(board="tb6612", **cal)
    return hw.with_(soc0=estimate_soc(hw, 12.32, hw.jetson_p_idle))   # the session's mid voltage


def simulate(args):
    kind, x = args
    hw = hardware()
    exp = EXPERIMENTS["straight_short"]
    on = exp.profile[1][1]
    twin = JetBotTwin(hw, dt=exp.dt)
    if kind == "weak":
        twin.robot.left_motor.alpha = 1.0 - x
        e = exp
    elif kind == "strong":
        twin.robot.right_motor.alpha = 1.0 + x
        e = exp
    elif kind == "split":
        twin.robot.left_motor.alpha = 1.0 - x / 2
        twin.robot.right_motor.alpha = 1.0 + x / 2
        e = exp
    elif kind == "late":
        e = replace(exp, profile=[(1.0, 0, 0), (x, 0, on), (1.5 - x, on, on), (2.0, 0, 0)]
                    if x > 0 else exp.profile)
    else:  # early
        e = replace(exp, profile=[(1.0, 0, 0), (1.5 - x, on, on), (x, 0, on), (2.0, 0, 0)]
                    if x > 0 else exp.profile)
    log = run_on_twin(e, twin=twin)
    return (kind, x) + as_video(log["x"], log["y"])


def as_video(px, py):
    """Distance, drift and turn as tools/straights_video.py measures them:
    ahead is the direction of travel over the first 0.3 m; the turn is the
    change in direction of travel from the first 0.25 m to the last."""
    gone = np.hypot(px - px[0], py - py[0])
    j = int(np.argmax(gone > 0.3))
    hd = math.atan2(py[j] - py[0], px[j] - px[0])
    c, s = math.cos(hd), math.sin(hd)
    ex, ey = px[-1] - px[0], py[-1] - py[0]
    X, Y = ex * c + ey * s, -ex * s + ey * c
    along = np.r_[0.0, np.cumsum(np.hypot(np.diff(px), np.diff(py)))]
    i_a = int(np.argmax(along > 0.25))
    i_b = int(np.argmax(along > along[-1] - 0.25))
    d_end = math.atan2(py[-1] - py[i_b], px[-1] - px[i_b])
    d_start = math.atan2(py[i_a] - py[0], px[i_a] - px[0])
    turn = math.degrees((d_end - d_start + math.pi) % (2 * math.pi) - math.pi)
    return float(X), float(Y), float(turn)


GRIDS = {"weak": np.round(np.arange(0.0, 0.401, 0.01), 3),
         "strong": np.round(np.arange(0.0, 0.601, 0.01), 3),
         "split": np.round(np.arange(0.0, 0.601, 0.01), 3),
         "late": np.round(np.arange(0.0, 0.801, 0.02), 3),
         "early": np.round(np.arange(0.0, 0.801, 0.02), 3)}


def main():
    jobs = [(k, float(x)) for k, g in GRIDS.items() for x in g]
    with Pool() as pool:
        out = pool.map(simulate, jobs)
    curves = {k: [] for k in GRIDS}
    for k, x, fx, fy, th in out:
        curves[k].append((x, fx, fy, th))

    res = json.load(open(ROOT / "results" / "robot2" / "straights_result.json"))
    scale = res["summary"]["scale"]
    swerves = [dict(run=r["run"], drift_m=r["drift_seen_m"] * scale, turn_deg=r["heading_deg"],
                    robot_m=r["robot_m"])
               for r in res["runs"] if r["drift_seen_m"] * scale > SWERVE_LINE]

    report = {}
    for k, c in curves.items():
        c = sorted(c)
        xs = np.array([p[0] for p in c])
        fine = np.linspace(xs[0], xs[-1], 4001)
        dy = np.interp(fine, xs, [p[2] for p in c])
        th = np.interp(fine, xs, [p[3] for p in c])
        fits = []
        for s in swerves:
            d2 = ((dy - s["drift_m"]) / DRIFT_TOL) ** 2 + ((th - s["turn_deg"]) / TURN_TOL) ** 2
            i = int(np.argmin(d2))
            fits.append(dict(run=s["run"], x=float(fine[i]), miss=float(math.sqrt(d2[i])),
                             twin_drift_m=float(dy[i]), twin_turn_deg=float(th[i])))
        misses = np.array([f["miss"] for f in fits])
        report[k] = dict(curve=[dict(x=p[0], dist_m=p[1], drift_m=p[2], turn_deg=p[3]) for p in c],
                         fits=fits, rms_miss=float(np.sqrt(np.mean(misses ** 2))),
                         n_within_2=int(np.sum(misses <= 2.0)),
                         x_range=[float(min(f["x"] for f in fits)), float(max(f["x"] for f in fits))])
        print(f"{k:5s}: rms miss {report[k]['rms_miss']:.1f} (in measurement errors); "
              f"{report[k]['n_within_2']}/{len(fits)} swerves within 2; "
              f"parameter {report[k]['x_range'][0]:.3f} to {report[k]['x_range'][1]:.3f}")
    ratio = [s["drift_m"] / (1.05 * math.radians(s["turn_deg"])) for s in swerves]
    print("drift / (distance x turn) for the swerves:", " ".join(f"{r:.2f}" for r in ratio))
    # a check the fit did not use: the distance each hypothesis then implies
    for k, rep in report.items():
        xs = [p["x"] for p in rep["curve"]]
        ds = [p["dist_m"] for p in rep["curve"]]
        pred = [float(np.interp(f["x"], xs, ds)) for f in rep["fits"]]
        rob = [s["robot_m"] for s in swerves]
        rep["distance_check"] = dict(twin_mean_m=float(np.mean(pred)), robot_mean_m=float(np.mean(rob)))
        print(f"  {k:5s}: implied distance {np.mean(pred):.3f} m; the swerves went {np.mean(rob):.3f} m")
    (ROOT / "results" / "robot2" / "swerve_test.json").write_text(
        json.dumps(dict(swerves=swerves, hypotheses=report, ratio=ratio,
                        tolerances=dict(drift_m=DRIFT_TOL, turn_deg=TURN_TOL)), indent=1, default=float))


if __name__ == "__main__":
    main()
