"""The repeated straights on robot 2 against the twin, as preregistered.

    .venv/bin/python tools/straights_analysis.py [--camera-height 1.9]

Follows results/robot2/PREREGISTRATION.md, fixed before the runs:

1. spread test: is zero inside the middle 95 % of the residuals
   (robot distance minus twin distance, run by run)?
2. bias: the mean residual, with a 95 % interval over the batch means
   (t, batches - 1 degrees of freedom) and, alongside, over all runs;
3. the spread of residuals within batches and of the batch means;
4. drift and heading, which the twin puts at zero, with intervals.

Plus, from the amendment of 2026-10-23: residual against run number, since
all batches were made in one session.

The twin is results/calibrated_robot2.json, replayed for each run at the
run's own rest voltage and logged command times. Distances come from
tools/straights_video.py (data/real/straights_robot2/batchNN_video.json),
which measures the robot's top; --camera-height scales them to the floor
(see that tool). Without it, the result is shown for a range of heights.
"""
import argparse
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jetbot_twin.compare import as_executed, replay  # noqa: E402
from jetbot_twin.experiments import EXPERIMENTS, predict_manual  # noqa: E402
from jetbot_twin.hardware import DEFAULT  # noqa: E402
from jetbot_twin.twin import Log  # noqa: E402

DATA = ROOT / "data" / "real" / "straights_robot2"
OUT = ROOT / "results" / "robot2"
# The robot's "height" as the camera sees it, for the whole-outline method.
# Calibrated against robot 2's lit green LED (8.5 cm above the floor, by
# ruler), in the 22 runs where the LED shows at both ends: the outline's
# distances match the LED's if the outline is taken as 5.3 cm up (sd 0.6 cm
# over runs; 5.0 for runs that went straight, 5.9 for those that swerved).
# It is lower than the robot's top because at the start mark, near the edge
# of the picture, the camera also sees the side panels and wheels. A first
# estimate from the parts list alone (8.7 cm) was too high.
TOP_HEIGHT = 0.053
TOP_RANGE = (0.050, 0.059)


def t95(dof):
    # two-sided 95 % t quantiles, small table (scipy is not needed for this)
    table = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
             8: 2.306, 9: 2.262, 10: 2.228, 15: 2.131, 20: 2.086, 30: 2.042, 40: 2.021,
             49: 2.010, 60: 2.000}
    keys = sorted(table)
    for k in keys:
        if dof <= k:
            return table[k]
    return 1.96


def interval(x):
    x = np.asarray(x, float)
    m = x.mean()
    h = t95(len(x) - 1) * x.std(ddof=1) / math.sqrt(len(x))
    return m, m - h, m + h


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera-height", type=float, help="m, lens above the floor")
    ap.add_argument("--top-height", type=float, default=TOP_HEIGHT,
                    help="m, the robot's effective height as seen from above")
    ap.add_argument("--calibration", default=str(ROOT / "results" / "calibrated_robot2.json"))
    a = ap.parse_args()

    runs = list(csv.DictReader(open(DATA / "runs.csv")))
    video = {}
    for f in sorted(DATA.glob("batch*_video.json")):
        j = json.load(open(f))
        if j.get("height_scale_applied", 1.0) != 1.0:
            raise SystemExit(f"{f.name} was already corrected for height; rerun "
                             "tools/straights_video.py without --camera-height")
        for r in j["runs"]:
            video[r["log"]] = r
    cal = {k: v for k, v in json.load(open(a.calibration)).items() if not k.startswith("_")}
    hw = DEFAULT.with_(board="tb6612", **cal)
    exp = EXPERIMENTS["straight_short"]

    rows = []
    for r in runs:
        if r["status"] != "ok":
            continue
        v = video.get(r["file"])
        if v is None:
            raise SystemExit(f"no video measurement for {r['file']}")
        if "failed" in v:
            print(f"  {r['file']}: no measurement ({v['failed']})")
            continue
        real = Log.read_csv(DATA / r["file"])
        twin_log, _ = replay(exp, real, hw)
        tw = predict_manual(as_executed(exp, real), twin_log)
        rows.append(dict(run=int(r["run"]), batch=r["batch"], rest_v=float(r["rest_v"]),
                         seen_m=v["distance_m"], drift_seen_m=v["drift_m"],
                         heading_deg=v["heading_deg"], twin_m=tw["distance_m"]))
    n = len(rows)
    print(f"{n} runs in {len(set(r['batch'] for r in rows))} batches; twin from "
          f"{Path(a.calibration).name}")

    def result(H, show=True, top=None):
        top = a.top_height if top is None else top
        k = (H - top) / H if H else 1.0
        robot = np.array([r["seen_m"] for r in rows]) * k
        twin = np.array([r["twin_m"] for r in rows])
        res = robot - twin
        batches = sorted(set(r["batch"] for r in rows))
        bm = np.array([res[[r["batch"] == b for r in rows]].mean() for b in batches])
        within = np.sqrt(np.mean([res[[r["batch"] == b for r in rows]].var(ddof=1)
                                  for b in batches]))
        lo, hi = np.percentile(res, [2.5, 97.5])
        out = dict(camera_height_m=H, scale=k, robot_mean_m=robot.mean(),
                   robot_sd_m=robot.std(ddof=1), twin_mean_m=twin.mean(),
                   residual_p2_5=lo, residual_p97_5=hi, spread_test_pass=bool(lo <= 0 <= hi),
                   bias_batches=interval(bm), bias_runs=interval(res),
                   sd_within_batch=within, sd_batch_means=bm.std(ddof=1))
        if show:
            print(f"\ncamera height {H} m, robot seen at {top * 100:.1f} cm: distances x {k:.4f}" if H else
                  "\nas seen (no height correction)")
            print(f"  robot  {robot.mean():.3f} m mean, {robot.std(ddof=1) * 100:.1f} cm sd "
                  f"(range {robot.min():.3f}-{robot.max():.3f})")
            print(f"  twin   {twin.mean():.3f} m mean (each run at its own voltage)")
            print(f"  1. spread test: middle 95 % of residuals {lo * 100:+.1f} to "
                  f"{hi * 100:+.1f} cm -> {'PASS' if lo <= 0 <= hi else 'FAIL'}")
            m, l, h = out["bias_batches"]
            m2, l2, h2 = out["bias_runs"]
            print(f"  2. bias {m * 100:+.1f} cm ({m / twin.mean() * 100:+.1f} %), 95 % interval "
                  f"{l * 100:+.1f} to {h * 100:+.1f} cm over batch means; "
                  f"{l2 * 100:+.1f} to {h2 * 100:+.1f} over runs")
            print(f"  3. sd within batches {within * 100:.1f} cm; sd of batch means "
                  f"{bm.std(ddof=1) * 100:.1f} cm (alone it would be "
                  f"{within / math.sqrt(5) * 100:.1f} if batches did not differ)")
        return out, res, robot, twin

    if a.camera_height:
        main_out, res, robot, twin = result(a.camera_height)
        print(f"\n  the same with the robot's effective height at the ends of its range:")
        for top in TOP_RANGE:
            o, *_ = result(a.camera_height, show=False, top=top)
            m, l, h = o["bias_batches"]
            print(f"    {top * 100:.0f} cm: robot mean {o['robot_mean_m']:.3f} m, bias "
                  f"{m * 100:+.1f} cm ({l * 100:+.1f} to {h * 100:+.1f}), spread test "
                  f"{'pass' if o['spread_test_pass'] else 'FAIL'}")
    else:
        result(None)
        print("\nhow the answer depends on the camera height (not yet measured):")
        print("  height   robot mean   bias      95 % interval (batches)   spread test")
        for H in (1.2, 1.4, 1.6, 1.8, 2.0, 2.2, 2.5):
            o, *_ = result(H, show=False)
            m, l, h = o["bias_batches"]
            print(f"  {H:.1f} m    {o['robot_mean_m']:.3f} m    {m * 100:+5.1f} cm   "
                  f"{l * 100:+5.1f} to {h * 100:+5.1f} cm         "
                  f"{'pass' if o['spread_test_pass'] else 'FAIL'}")
        main_out, res, robot, twin = result(None, show=False)

    k = main_out["scale"]
    drift = np.array([r["drift_seen_m"] for r in rows]) * k
    head = np.array([r["heading_deg"] for r in rows])
    d = interval(drift)
    hh = interval(head)
    print(f"\n  4. drift {d[0] * 100:+.1f} cm (95 % {d[1] * 100:+.1f} to {d[2] * 100:+.1f}; "
          f"negative is to the right), {np.mean(drift < 0) * 100:.0f} % of runs to the right")
    print(f"     turn  {hh[0]:+.1f} deg (95 % {hh[1]:+.1f} to {hh[2]:+.1f}; negative is "
          f"clockwise), twin: 0 for both")
    run_no = np.array([r["run"] for r in rows])
    slope = np.polyfit(run_no, res, 1)[0]
    vs = np.polyfit([r["rest_v"] for r in rows], res, 1)[0]
    print(f"  run number: residual changes {slope * 1e3 * 50:+.1f} mm over the 50 runs "
          f"(battery {rows[0]['rest_v']:.2f} -> {rows[-1]['rest_v']:.2f} V)")

    OUT.mkdir(parents=True, exist_ok=True)
    table = [dict(r, robot_m=float(rb), residual_m=float(e)) for r, rb, e in zip(rows, robot, res)]
    (OUT / "straights_result.json").write_text(json.dumps(
        {"summary": main_out, "drift_m": d, "heading_deg": hh,
         "residual_per_run_slope_m": float(slope), "runs": table},
        indent=1, default=float))
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 3, figsize=(15, 4.2))
        ax[0].hist(res * 100, bins=12, color="#4a7ab5")
        ax[0].axvline(0, color="k", lw=1)
        ax[0].set_xlabel("robot - twin, cm")
        ax[0].set_title("residuals")
        ax[1].plot(run_no, res * 100, "o", ms=4, color="#4a7ab5")
        ax[1].axhline(0, color="k", lw=1)
        ax[1].set_xlabel("run number")
        ax[1].set_ylabel("robot - twin, cm")
        ax[1].set_title("through the session")
        ax[2].plot(drift * 100, head, "o", ms=4, color="#c0504d")
        ax[2].axhline(0, color="k", lw=0.5)
        ax[2].axvline(0, color="k", lw=0.5)
        ax[2].set_xlabel("drift, cm (left +)")
        ax[2].set_ylabel("turn, deg (anticlockwise +)")
        ax[2].set_title("drift and turn (twin: 0, 0)")
        title = (f"camera height {a.camera_height} m" if a.camera_height
                 else "as seen, no height correction")
        fig.suptitle(f"Robot 2, 50 straights against the twin ({title})")
        fig.tight_layout()
        fig.savefig(OUT / "straights_result.png", dpi=110)
        print(f"\nwrote {OUT / 'straights_result.json'} and .png")
    except ImportError:
        pass


if __name__ == "__main__":
    main()
