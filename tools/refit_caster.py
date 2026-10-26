"""Robot 2's caster, refitted as rolling along the heading and scrubbing across.

    .venv/bin/python tools/refit_caster.py

Follows results/robot2/REFIT_PLAN.md, written before this was run:

- mu_caster (scrubbing, across the heading): fitted to spins 2 and 3 only;
- mu_caster_roll (rolling, along it): fitted to batch 1 only, runs 1 to 5;
- the two in turn until they settle; everything else as in
  results/calibrated_robot2.json.

Runs 6 to 50 are not used here. They are the test:

    .venv/bin/python tools/straights_analysis.py --camera-height 1.01 \\
        --calibration results/calibrated_robot2_caster.json --runs 6-50 --tag refit

Writes results/calibrated_robot2_caster.json.
"""
import json
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import minimize_scalar

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jetbot_twin.compare import as_executed, replay  # noqa: E402
from jetbot_twin.experiments import EXPERIMENTS, predict_manual  # noqa: E402
from jetbot_twin.hardware import DEFAULT  # noqa: E402
from jetbot_twin.twin import Log  # noqa: E402

R2 = ROOT / "data" / "real" / "robot2-2026-10-22"
STRAIGHTS = ROOT / "data" / "real" / "straights_robot2"
FIT_RUNS = range(1, 6)
OUT = ROOT / "results" / "calibrated_robot2_caster.json"


def main():
    base = json.load(open(ROOT / "results" / "calibrated_robot2.json"))
    cal = {k: v for k, v in base.items() if not k.startswith("_")}

    spins = []
    for n in (2, 3):
        rot = json.load(open(R2 / f"spin_{n}_video.json"))["rotation_deg"]
        spins.append((Log.read_csv(R2 / f"spin_{n}.csv"), rot))
    # batch 1's distances, as measured (height-corrected) in the first round
    measured = {r["run"]: r["robot_m"]
                for r in json.load(open(ROOT / "results" / "robot2" / "straights_result.json"))["runs"]}
    straights = [(Log.read_csv(STRAIGHTS / f"straight_{n:02d}.csv"), measured[n]) for n in FIT_RUNS]

    spin_exp = EXPERIMENTS["spin"]
    str_exp = EXPERIMENTS["straight_short"]

    def hw(scrub, roll):
        return DEFAULT.with_(board="tb6612", **{**cal, "mu_caster": scrub, "mu_caster_roll": roll})

    def spin_err(scrub, roll):
        h = hw(scrub, roll)
        e = [predict_manual(as_executed(spin_exp, log), replay(spin_exp, log, h)[0])["rotation_deg"] - rot
             for log, rot in spins]
        return float(np.mean(np.square(e)))

    def straight_err(scrub, roll):
        h = hw(scrub, roll)
        e = [predict_manual(as_executed(str_exp, log), replay(str_exp, log, h)[0])["distance_m"] - d
             for log, d in straights]
        return float(np.mean(np.square(e)))

    scrub, roll = cal["mu_caster"], 0.05
    print(f"start: scrub {scrub:.4f}, roll {roll:.4f}")
    for it in range(4):
        scrub = minimize_scalar(lambda x: spin_err(x, roll), bounds=(0.05, 1.5),
                                method="bounded", options={"xatol": 1e-3}).x
        roll = minimize_scalar(lambda x: straight_err(scrub, x), bounds=(0.0, 0.6),
                               method="bounded", options={"xatol": 1e-4}).x
        print(f"round {it + 1}: scrub {scrub:.4f} (spins rms {spin_err(scrub, roll) ** 0.5:.1f} deg), "
              f"roll {roll:.4f} (batch 1 rms {straight_err(scrub, roll) ** 0.5 * 100:.1f} cm)")

    out = dict(base)
    out.update(mu_caster=float(scrub), mu_caster_roll=float(roll))
    out["_notes"] = (
        "robot 2, caster refitted as rolling (mu_caster_roll, along the heading) and "
        "scrubbing (mu_caster, across it), per results/robot2/REFIT_PLAN.md: scrub from "
        "spins 2 and 3, roll from straight runs 1-5 only. Runs 6-50 are the test. "
        "Everything else as results/calibrated_robot2.json. " + base.get("_notes", ""))
    OUT.write_text(json.dumps(out, indent=1))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
