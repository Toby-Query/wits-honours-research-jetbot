"""How much does the caster's rolling number depend on which five runs fit it?

    .venv/bin/python tools/caster_cv.py

The published refit took mu_caster_roll from batch 1 (runs 1-5) and tested on
runs 6-50. Here every batch takes a turn as the fit set and the other 45 runs
are the test (leave-one-batch-out). Each run's twin distance is computed once
on a grid of mu_caster_roll and interpolated, so the ten fits are cheap.

It also gives each fit a prediction interval, the twin's distance plus or minus
what the five fitting runs scatter by (t with 4 degrees of freedom, times
sqrt(1 + 1/5)), and counts how many held-out runs fall inside it.

Writes results/robot2/caster_cv.json.
"""
import csv
import json
import math
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jetbot_twin.compare import replay  # noqa: E402
from jetbot_twin.experiments import EXPERIMENTS, predict_manual  # noqa: E402
from jetbot_twin.hardware import DEFAULT  # noqa: E402
from jetbot_twin.twin import Log  # noqa: E402

STR = ROOT / "data" / "real" / "straights_robot2"
GRID = np.round(np.arange(0.05, 0.6001, 0.025), 4)
T95 = {2: 4.303, 3: 3.182, 4: 2.776}     # two-sided 95 % t by degrees of freedom


def one(args):
    run, roll = args
    cal = {k: v for k, v in json.load(open(ROOT / "results" / "calibrated_robot2_caster.json")).items()
           if not k.startswith("_")}
    hw = DEFAULT.with_(board="tb6612", **{**cal, "mu_caster_roll": float(roll)})
    exp = EXPERIMENTS["straight_short"]
    log = Log.read_csv(STR / f"straight_{run:02d}.csv")
    tl, _ = replay(exp, log, hw)
    from jetbot_twin.compare import as_executed
    return run, float(roll), predict_manual(as_executed(exp, log), tl)["distance_m"]


def main():
    res = json.load(open(ROOT / "results" / "robot2" / "straights_result.json"))
    scale = res["summary"]["scale"]
    runs = {r["run"]: r for r in res["runs"]}
    batch = {int(r["run"]): r["batch"] for r in csv.DictReader(open(STR / "runs.csv"))}
    nums = sorted(runs)
    jobs = [(n, g) for n in nums for g in GRID]
    with Pool() as pool:
        out = pool.map(one, jobs)
    table = {}
    for n, g, d in out:
        table.setdefault(n, {})[g] = d

    def twin(n, roll):
        return float(np.interp(roll, GRID, [table[n][g] for g in GRID]))

    robot = {n: runs[n]["robot_m"] for n in nums}
    swerve = {n: runs[n]["drift_seen_m"] * scale > 0.25 for n in nums}
    groups = [nums[i:i + 5] for i in range(0, len(nums), 5)]   # batches of five, in order
    fine = np.linspace(GRID[0], GRID[-1], 2001)
    report = {}
    for mode in ("all five", "straight runs only"):
        print(f"-- fit on {mode}")
        folds = []
        for k, fit_all in enumerate(groups):
            fit = [n for n in fit_all if not (mode != "all five" and swerve[n])]
            if len(fit) < 3:
                print(f"   runs {fit_all[0]}-{fit_all[-1]}: fewer than 3 straight runs; skipped")
                continue
            test = [n for n in nums if n not in fit_all]
            sse = [sum((twin(n, r) - robot[n]) ** 2 for n in fit) for r in fine]
            roll = float(fine[int(np.argmin(sse))])
            fit_res = np.array([robot[n] - twin(n, roll) for n in fit])
            s = float(fit_res.std(ddof=1))
            half = T95[len(fit) - 1] * s * math.sqrt(1 + 1 / len(fit))
            res_t = {n: robot[n] - twin(n, roll) for n in test}
            straight = [n for n in test if not swerve[n]]
            tw = np.mean([twin(n, roll) for n in straight])
            folds.append(dict(
                fit_runs=fit, batch_runs=fit_all, mu_caster_roll=roll, fit_sd_m=s, pi_half_m=half,
                bias_straight_pct=100 * np.mean([res_t[n] for n in straight]) / tw,
                bias_all_pct=100 * np.mean(list(res_t.values())) / np.mean([twin(n, roll) for n in test]),
                coverage_straight=float(np.mean([abs(res_t[n]) <= half for n in straight])),
                coverage_all=float(np.mean([abs(res_t[n]) <= half for n in test])),
                n_test=len(test), n_test_straight=len(straight)))
            f = folds[-1]
            print(f"fit on runs {fit[0]:2d}-{fit[-1]:2d}: roll {roll:.3f}; held-out straight runs "
                  f"{f['bias_straight_pct']:+.1f} %, all {f['bias_all_pct']:+.1f} %; interval "
                  f"+/-{half * 100:.1f} cm covers {f['coverage_straight'] * 100:.0f} % of straight, "
                  f"{f['coverage_all'] * 100:.0f} % of all")
        rolls = np.array([f["mu_caster_roll"] for f in folds])
        bs = np.array([f["bias_straight_pct"] for f in folds])
        summary = dict(roll_mean=rolls.mean(), roll_min=rolls.min(), roll_max=rolls.max(),
                       bias_straight_mean=bs.mean(), bias_straight_min=bs.min(), bias_straight_max=bs.max(),
                       coverage_straight_mean=np.mean([f["coverage_straight"] for f in folds]),
                       coverage_all_mean=np.mean([f["coverage_all"] for f in folds]),
                       pi_half_mean_m=np.mean([f["pi_half_m"] for f in folds]))
        print(json.dumps(summary, indent=1, default=float))
        report[mode] = dict(summary=summary, folds=folds)
    (ROOT / "results" / "robot2" / "caster_cv.json").write_text(
        json.dumps(dict(report=report, grid=list(GRID),
                        table={str(n): [table[n][g] for g in GRID] for n in nums}),
                   indent=1, default=float))


if __name__ == "__main__":
    main()
