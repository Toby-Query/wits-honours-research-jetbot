"""Does the circuit model matter to the motion? Robot 2, three twins.

    .venv/bin/python tools/ablation.py coupled      # the twin as it is
    .venv/bin/python tools/ablation.py no_sag       # supply with no resistance
    .venv/bin/python tools/ablation.py ideal        # ... and fixed at full charge

The three twins differ only in what the motors are fed:

- coupled  the board's power path as built from the schematic and measured:
           cells with internal resistance and state of charge, holder,
           protector, switch, shunt. The motors see the pack sag under load
           and the pack's charge level (from each log's rest voltage).
- no_sag   every resistance in the power path set to almost nothing; the
           charge level is still followed. A supply that follows the battery
           but never droops.
- ideal    as no_sag, and always at full charge: the motors see a constant
           voltage. This is what a robot simulator without an electrical
           model assumes (a motor is a torque or speed source).

Each twin goes through the SAME calibration as robot 2's, so none starts at a
disadvantage:

    1. motor constant and friction from the 60 fps wheels-up sweep, holds 0.1-0.6
       (wheel speed only: the ideal twin has no voltage to compare)
    2. caster scrubbing from spins 2 and 3; caster rolling from straights 1-5
       (alternating until they settle, as tools/refit_caster.py)

and then the same held-out tests: straights 6-50, the ten spins, the 24 short
turns, and the electrical readings (bus sag in every straight, the Jetson's
5 V under CPU load). Winding resistance and gearbox efficiency are held at
robot 2's values in all three.

Writes results/ablation/<variant>.json.
"""
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares, minimize_scalar

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jetbot_twin.compare import as_executed, estimate_soc, sags  # noqa: E402
from jetbot_twin.experiments import EXPERIMENTS, predict_manual, run_on_twin  # noqa: E402
from jetbot_twin.hardware import DEFAULT  # noqa: E402
from jetbot_twin.twin import Log  # noqa: E402

R2 = ROOT / "data" / "real" / "robot2-2026-10-22"
STR = ROOT / "data" / "real" / "straights_robot2"
OUT = ROOT / "results" / "ablation"

TINY = 1e-4
STIFF = dict(cell_r0=TINY, cell_r1=TINY, holder_r=TINY, switch_r=TINY,
             prot_r_on=TINY, r_sense=TINY, r_5v_path=0.0)
VARIANTS = {"coupled": ({}, True), "no_sag": (STIFF, True), "ideal": (STIFF, False)}


def replay(exp, real, hw, track_soc):
    """compare.replay, with the option of ignoring the pack's charge."""
    p = real["p_5v"] if "p_5v" in real else None
    p_fn = None
    if p is not None and np.isfinite(p).any():
        tt, pp = real.t[np.isfinite(p)], p[np.isfinite(p)]

        def p_fn(t):
            return float(np.interp(t, tt, pp))
    exp = as_executed(exp, real)
    if track_soc:
        first = real.t <= max(0.5, min(1.0, exp.profile[0][0] * 0.8))
        v_rest = float(np.nanmean(real["bus_v"][first]))
        p_rest = float(np.nanmean(p[first])) if p_fn else hw.jetson_p_idle
        hw = hw.with_(soc0=estimate_soc(hw, v_rest, p_rest))
    else:
        hw = hw.with_(soc0=1.0)
    return run_on_twin(exp, hw, p_5v=p_fn), exp


def segments(real):
    """(t0, t1) of every commanded segment, on the robot's own clock."""
    ev = [tuple(float(x) for x in e.split(":")) for e in real.meta["commands_sent_at"].split()]
    return [(t0, t1) for (t0, l, r), (t1, _, _) in zip(ev[:-1], ev[1:]) if l or r]


def heading_changes(log, segs):
    th = np.unwrap(log["th"])
    return [math.degrees(float(np.interp(t1 + 0.9, log.t, th) - np.interp(t0 - 0.05, log.t, th)))
            for t0, t1 in segs]


def jetson_5v(jl, hw, track):
    """The Jetson's 5 V, robot and twin, while its CPUs are loaded. The load
    runs between the second and third logged commands (10 s to 30 s)."""
    tl, _ = replay(EXPERIMENTS["jetson_load"], jl, hw, track)
    sent = [float(e.split(":")[0]) for e in jl.meta["commands_sent_at"].split()]
    t0, t1 = sent[1] + 2.0, sent[2] - 0.5
    return dict(robot_loaded=jl.mean("v_5v", t0, t1), twin_loaded=tl.mean("v_5v", t0, t1),
                robot_idle=jl.mean("v_5v", 0.5, sent[1] - 0.5),
                twin_idle=tl.mean("v_5v", 0.5, sent[1] - 0.5))


def main(variant):
    over, track = VARIANTS[variant]
    base = {k: v for k, v in json.load(open(ROOT / "results" / "calibrated_robot2.json")).items()
            if not k.startswith("_")}
    hw0 = DEFAULT.with_(board="tb6612", **{**base, **over})
    say = lambda *a: print(f"[{variant}]", *a, flush=True)  # noqa: E731

    # ---- 1. motors, from the wheels-up sweep (wheel speed only) -------------
    sweep = Log.read_csv(R2 / "wheels_up_sweep.csv")
    rpm_real = [h["rpm"] for h in json.load(open(R2 / "wheel_speed_60fps.json"))["holds"]][:8]
    sweep_exp = EXPERIMENTS["wheels_up_sweep"]

    def motor_res(x):
        h = hw0.with_(motor_rpm_noload=x[0], motor_i_noload=x[1])
        log, ex = replay(sweep_exp, sweep, h, track)
        rpm = predict_manual(ex, log)["rpm"][:8]
        return [(a - b) / b for a, b in zip(rpm, rpm_real)]

    sol = least_squares(motor_res, [base["motor_rpm_noload"], base["motor_i_noload"]],
                        bounds=([150, 0.02], [400, 0.5]), x_scale=[50, 0.05],
                        diff_step=0.02, xtol=1e-3, ftol=1e-4, max_nfev=30)
    rpm0, i0 = map(float, sol.x)
    sweep_rms = float(np.sqrt(np.mean(np.square(sol.fun))))
    say(f"motors: rpm_noload {rpm0:.1f}, i_noload {i0:.4f}, sweep rms {sweep_rms * 100:.1f} %")
    hw1 = hw0.with_(motor_rpm_noload=rpm0, motor_i_noload=i0)

    # ---- 2. caster: scrub from spins 2-3, roll from straights 1-5 -----------
    spins = [(Log.read_csv(R2 / f"spin_{n}.csv"),
              json.load(open(R2 / f"spin_{n}_video.json"))["rotation_deg"]) for n in (2, 3)]
    measured = {r["run"]: r["robot_m"] for r in
                json.load(open(ROOT / "results" / "robot2" / "straights_result.json"))["runs"]}
    runs = [r for r in csv.DictReader(open(STR / "runs.csv")) if r["status"] == "ok"]
    logs = {int(r["run"]): Log.read_csv(STR / r["file"]) for r in runs}
    sag_real = {int(r["run"]): float(r["sag_mv"]) for r in runs}
    s_exp, sp_exp = EXPERIMENTS["straight_short"], EXPERIMENTS["spin"]

    def spin_err(h):
        e = []
        for log, rot in spins:
            tl, ex = replay(sp_exp, log, h, track)
            e.append(predict_manual(ex, tl)["rotation_deg"] - rot)
        return float(np.mean(np.square(e)))

    def straight_err(h):
        e = []
        for n in range(1, 6):
            tl, ex = replay(s_exp, logs[n], h, track)
            e.append(predict_manual(ex, tl)["distance_m"] - measured[n])
        return float(np.mean(np.square(e)))

    scrub, roll = base["mu_caster"], 0.05
    for it in range(3):
        scrub = minimize_scalar(lambda x: spin_err(hw1.with_(mu_caster=x, mu_caster_roll=roll)),
                                bounds=(0.05, 1.5), method="bounded", options={"xatol": 2e-3}).x
        roll = minimize_scalar(lambda x: straight_err(hw1.with_(mu_caster=scrub, mu_caster_roll=x)),
                               bounds=(0.0, 0.8), method="bounded", options={"xatol": 2e-4}).x
        say(f"round {it + 1}: scrub {scrub:.4f}, roll {roll:.4f}")
    hw = hw1.with_(mu_caster=float(scrub), mu_caster_roll=float(roll))

    # ---- 3. held-out tests ---------------------------------------------------
    straights = []
    for n in sorted(logs):
        tl, ex = replay(s_exp, logs[n], hw, track)
        straights.append(dict(run=n, robot_m=measured[n],
                              twin_m=predict_manual(ex, tl)["distance_m"],
                              sag_real_mv=sags(ex, logs[n])[0] * 1e3, sag_twin_mv=sags(ex, tl)[0] * 1e3,
                              sag_agent_mv=sag_real[n],
                              rest_v=float(np.nanmean(logs[n]["bus_v"][logs[n].t < 0.8]))))
    say("straights done")

    spin_log = Log.read_csv(R2 / "spins_10.csv")
    tl, _ = replay(EXPERIMENTS["spins_10"], spin_log, hw, track)
    spin_twin = heading_changes(tl, segments(spin_log))
    spin_robot = [s["robot_deg"] for s in json.load(open(R2 / "spins_10_video.json"))["spins"]]

    turn_log = Log.read_csv(R2 / "short_turns_24.csv")
    tl, _ = replay(EXPERIMENTS["short_turns_24"], turn_log, hw, track)
    turn_twin = heading_changes(tl, segments(turn_log))
    turn_robot = [t["robot_deg"] for t in json.load(open(R2 / "short_turns_24_video.json"))["turns"]]
    say("spins and turns done")

    jl = Log.read_csv(R2 / "jetson_load.csv")
    v5 = jetson_5v(jl, hw, track)

    out = dict(variant=variant, track_soc=track, stiff=bool(over),
               motor_rpm_noload=rpm0, motor_i_noload=i0, sweep_rms=sweep_rms,
               mu_caster=float(scrub), mu_caster_roll=float(roll),
               straights=straights,
               spins=dict(robot=spin_robot, twin=spin_twin),
               turns=dict(robot=turn_robot, twin=turn_twin),
               jetson_5v=v5)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{variant}.json").write_text(json.dumps(out, indent=1, default=float))
    say(f"wrote {OUT / (variant + '.json')}")


if __name__ == "__main__":
    main(sys.argv[1])
