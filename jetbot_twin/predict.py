"""What the twin expects the robot to do, with honest error bars.

The twin rests on numbers nobody has measured yet. Each of those is given a
spread, the experiments are run a few dozen times with the numbers drawn
from their spreads, and the result is a band rather than a line. That band
is the prediction: written down BEFORE the robot is switched on, so that the
comparison afterwards is a test and not a fit.
"""
from __future__ import annotations

import json
import math
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from .experiments import EXPERIMENTS, predict_manual, run_on_twin, holds
from .compare import sags
from .hardware import DEFAULT

# Relative standard deviation given to each unverified number.
SPREAD = {
    "motor_rpm_noload": 0.15, "motor_i_noload": 0.25, "motor_r": 0.20,
    "motor_l": 0.30, "gear_eff": 0.15, "rotor_inertia": 0.30,
    "motor_friction_static_ratio": 0.15, "cell_r0": 0.30, "holder_r": 0.40,
    "wheel_radius": 0.02, "mu_tyre": 0.20, "mu_caster": 0.35, "c_rr": 0.30,
    "mass_override": 0.08,
}
SWEEP_CMDS = [0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0]


def metrics(overrides):
    """One robot, described by `overrides`, through the standard experiments.
    Returns a flat dict of numbers you could go and measure."""
    hw = DEFAULT.with_(**overrides)
    out = {}
    log = run_on_twin(EXPERIMENTS["wheels_up_sweep"], hw)
    turns = predict_manual(EXPERIMENTS["wheels_up_sweep"], log)["turns_l"]
    hs = holds(EXPERIMENTS["wheels_up_sweep"])
    for cmd, (t0, t1), n in zip(SWEEP_CMDS, hs, turns):
        out[f"wheels-up rpm at {cmd:g}"] = abs(log.at("wl", t1 - 0.05)) * 9.549
    moving = [c for c, (t0, t1) in zip(SWEEP_CMDS, hs)
              if abs(log.at("wl", t1 - 0.05)) > 0.5]
    out["lowest command that turns a free wheel"] = min(moving) if moving else math.nan
    exp = EXPERIMENTS["straight"]
    log = run_on_twin(exp, hw)
    t_off = holds(exp)[-1][1]
    out["straight: distance, m"] = log.final("x")
    out["straight: roll-on after cut, m"] = log.final("x") - log.at("x", t_off)
    out["straight: speed at cut, m/s"] = log.at("v", t_off - 0.05)
    out["straight: bus sag, mV"] = sags(exp, log)[0] * 1e3
    out["straight: peak tyre slip, m/s"] = float(np.max(np.abs(log["slip_l"])))
    log = run_on_twin(EXPERIMENTS["spin"], hw)
    out["spin: rotation, deg"] = math.degrees(log.final("th"))
    log = run_on_twin(EXPERIMENTS["stall"], hw)
    out["stall at 0.5: bus sag, mV"] = sags(EXPERIMENTS["stall"], log)[0] * 1e3
    out["stall at 0.5: motor current each, A"] = float(np.max(np.abs(log["i_l"])))
    log = run_on_twin(EXPERIMENTS["square"], hw)
    out["square: end x, m"] = log.final("x")
    out["square: end y, m"] = log.final("y")
    out["square: end heading, deg"] = math.degrees(log.final("th")) % 360
    return out


def _draw(rng, base, spread):
    p = dict(base)
    for name, sig in spread.items():
        nominal = base.get(name, getattr(DEFAULT, name))
        if nominal is None:
            nominal = DEFAULT.mass
        z = float(np.clip(rng.standard_normal(), -2.5, 2.5))
        p[name] = float(nominal) * (1 + sig * z)
    return p


def monte_carlo(board, n=48, seed=1, workers=None, spread=None, base=None):
    rng = np.random.default_rng(seed)
    base = {"board": board, **(base or {})}
    draws = [_draw(rng, base, spread or SPREAD) for _ in range(n)]
    with Pool(workers) as pool:
        rows = pool.map(metrics, [base] + draws)
    nominal, rows = rows[0], rows[1:]
    out = {}
    for k in nominal:
        v = np.array([r[k] for r in rows], float)
        v = v[np.isfinite(v)]
        out[k] = {"nominal": nominal[k],
                  "p05": float(np.percentile(v, 5)) if len(v) else math.nan,
                  "p50": float(np.percentile(v, 50)) if len(v) else math.nan,
                  "p95": float(np.percentile(v, 95)) if len(v) else math.nan}
    return out


def _one_sens(args):
    name, factor, board = args
    nominal = getattr(DEFAULT, name)
    if nominal is None:
        nominal = DEFAULT.mass
    return name, factor, metrics({"board": board, name: float(nominal) * factor})


def sensitivity(board, names=None, rel=0.10, workers=None):
    """Which unverified number moves which prediction, and how much.

    Returns {metric: [(param, elasticity), ...]} most influential first. An
    elasticity of 1 means a 10 % error in the number is a 10 % error in the
    prediction.
    """
    names = names or list(SPREAD)
    jobs = [(n, f, board) for n in names for f in (1 + rel, 1 - rel)]
    with Pool(workers) as pool:
        res = pool.map(_one_sens, jobs)
        base = pool.apply(metrics, ({"board": board},))
    by = {}
    for name, factor, m in res:
        by.setdefault(name, {})[factor] = m
    out = {}
    for k, y0 in base.items():
        rows = []
        for name, d in by.items():
            up, dn = d[1 + rel][k], d[1 - rel][k]
            if y0 and all(map(math.isfinite, (up, dn, y0))):
                rows.append((name, (up - dn) / (2 * rel * y0)))
        rows.sort(key=lambda r: -abs(r[1]))
        out[k] = rows
    return out


def write(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=1))
