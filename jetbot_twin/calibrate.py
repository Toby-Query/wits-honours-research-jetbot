"""Fit the twin's unknown numbers to what the real robot did.

Everything at once does not work: with a voltage trace and a tape measure
you cannot tell a weak motor from a stiff gearbox from a flat battery. So
the fit goes in stages, each one an experiment designed so that only a few
numbers can explain its result:

    pack     jetson_load       a known load step     -> pack resistance
    free     wheels_up_sweep   no floor, no mass     -> motor constant, friction,
                                                       and which driver chip
    stall    stall             no back-EMF           -> winding resistance
    floor    straight, spin    everything together   -> gearbox efficiency,
                                                       caster drag

Each stage holds what earlier stages found. `square` is in none of them: it
is kept back to test the result.

Two things are deliberately NOT fitted. Geometry (wheel radius, track) is
measured with a ruler and typed into hardware.py: a fit will happily shrink
a wheel to a third of its size to soak up an error that belongs somewhere
else. And which driver chip is fitted is read off the board, not inferred;
if it has to be inferred, `staged` fits the free-wheel stage under each
hypothesis and keeps the one that explains the data, rather than deciding
before anything else has been fitted.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .compare import replay, sags
from .experiments import EXPERIMENTS, MANUAL_TOL, predict_manual

STAGES = [
    ("rail", ["r_5v_path"], ["jetson_load"]),
    ("pack", ["cell_r0"], ["jetson_load"]),
    ("free", ["motor_rpm_noload", "motor_i_noload"], ["wheels_up_sweep"]),
    ("stall", ["motor_r"], ["stall"]),
    ("floor", ["gear_eff", "mu_caster"], ["straight", "spin"]),
]
# How far a fit may move each number from where it started, as a ratio.
BOUNDS = {"gear_eff": (0.5, 1.45), "motor_l": (0.3, 3.0), "r_5v_path": (0.05, 10.0),
          "cell_r0": (0.3, 5.0)}
DEFAULT_BOUNDS = (0.4, 2.5)
BUS_NOISE = 0.010          # V: what one INA219 bus reading is good to


@dataclass
class Case:
    exp: object
    real: object = None                 # Log from the robot, or None
    manual: dict = field(default_factory=dict)


def residuals(hw, cases, dt=None, only=None):
    """Everything the twin gets wrong, each in units of its own uncertainty."""
    out = []
    for case in cases:
        twin_log, _ = replay(case.exp, case.real, hw, dt=dt)
        if only != "manual" and case.real is not None and "bus_v" in case.real:
            a, b = sags(case.exp, case.real), sags(case.exp, twin_log)
            out += [(y - x) / BUS_NOISE for x, y in zip(a, b)]
            if case.exp.jetson == "stress":       # the load step itself
                t0 = case.exp.profile[0][0]
                t1 = t0 + case.exp.profile[1][0]
                for log, sign in ((case.real, -1.0), (twin_log, 1.0)):
                    step = (log.mean("bus_v", t0 - 3, t0 - 0.2)
                            - log.mean("bus_v", t0 + 2, t1 - 0.2))
                    out.append(sign * step / (BUS_NOISE / 4))
                out[-2:] = [out[-2] + out[-1]]
        if (only == "rail" and case.real is not None and "v_5v" in case.real
                and np.isfinite(case.real["v_5v"]).any()):
            # the 5 V the Jetson sees, idle and loaded
            t0 = case.exp.profile[0][0]
            t1 = t0 + case.exp.profile[1][0]
            out = []
            for a, b in ((1.0, t0 - 0.5), (t0 + 2.0, t1 - 0.5)):
                out.append((twin_log.mean("v_5v", a, b)
                            - case.real.mean("v_5v", a, b)) / 0.005)
            return np.array(out, float)
        from .compare import as_executed
        pred = predict_manual(as_executed(case.exp, case.real), twin_log)
        for k, v in case.manual.items():
            if k not in pred:
                continue
            tol = MANUAL_TOL.get(k, 1.0)
            if isinstance(v, (list, tuple)):
                out += [(p - r) / tol for p, r in zip(pred[k], v)]
            else:
                out.append((pred[k] - v) / tol)
    out = np.array(out, float)
    return out[np.isfinite(out)]        # a hold too short to average gives none


def fit(hw, names, cases, dt=None, verbose=False, only=None):
    """Least-squares fit of the named Hardware fields. Returns (hw, info)."""
    from scipy.optimize import least_squares
    x0 = np.ones(len(names))
    nominal = [float(getattr(hw, n)) for n in names]

    def make(x):
        return hw.with_(**{n: v * s for n, v, s in zip(names, nominal, x)})

    def f(x):
        r = residuals(make(x), cases, dt, only)
        if verbose:
            print("   ", " ".join(f"{n}={v * s:.4g}" for n, v, s in
                                  zip(names, nominal, x)),
                  f" cost {0.5 * float(r @ r):.2f}")
        return r

    lo = [BOUNDS.get(n, DEFAULT_BOUNDS)[0] for n in names]
    hi = [BOUNDS.get(n, DEFAULT_BOUNDS)[1] for n in names]
    sol = least_squares(f, x0, bounds=(lo, hi), diff_step=0.05, xtol=1e-3,
                        ftol=1e-3, max_nfev=40)
    at_bound = [n for n, x, a, b in zip(names, sol.x, lo, hi)
                if x - a < 1e-3 or b - x < 1e-3]
    info = {"names": names, "before": nominal,
            "after": [v * s for v, s in zip(nominal, sol.x)],
            "cost_before": 0.5 * float(np.sum(f(x0) ** 2)),
            "cost_after": float(sol.cost), "n_residuals": len(sol.fun),
            "evaluations": int(sol.nfev), "at_bound": at_bound}
    return make(sol.x), info


def staged(hw, data, board=None, verbose=True, dt=None):
    """Run every stage that has data. `data` maps experiment name -> Case.

    board   "drv8870" or "tb6612" if you have looked; None to infer it.
    """
    say = print if verbose else (lambda *a: None)
    history = []
    stages = list(STAGES)

    def run_stage(hw, stage, names, exps):
        cases = [data[e] for e in exps if e in data]
        if not cases:
            say(f"{stage}: no data for {', '.join(exps)}; skipped")
            return hw, None
        if stage == "rail":
            # starts at zero, so there is nothing to scale: begin from a guess
            hw = hw.with_(r_5v_path=hw.r_5v_path or 0.2)
        hw, info = fit(hw, names, cases, dt=dt, only=stage)
        info["stage"] = stage
        say(f"{stage}: misfit {info['cost_before']:.1f} -> "
            f"{info['cost_after']:.1f} over {info['n_residuals']} numbers")
        for n, a, b in zip(names, info["before"], info["after"]):
            flag = "   <- stopped at its bound: do not trust" \
                if n in info["at_bound"] else ""
            say(f"    {n:<20} {a:.4g} -> {b:.4g}{flag}")
        return hw, info

    for st in stages[:2]:                                # rail, then pack
        hw, info = run_stage(hw, *st)
        if info:
            history.append(info)

    if board is None and "wheels_up_sweep" in data:
        trial = {}
        for b in ("drv8870", "tb6612"):
            say(f"-- supposing the driver is the {b}")
            trial[b] = run_stage(hw.with_(board=b), *stages[2])
        board = min(trial, key=lambda b: trial[b][1]["cost_after"])
        cost = {b: trial[b][1]["cost_after"] for b in trial}
        other = max(cost, key=cost.get)
        say(f"driver chip: {board} (misfit {cost[board]:.1f} against "
            f"{cost[other]:.1f} for the {other})")
        if cost[other] < 3 * cost[board] + 5:
            say("  the two are close: look at the board before believing this")
        history.append({"stage": "driver chip", "picked": board, "cost": cost})
        hw, info = trial[board]
        history.append(info)
    else:
        if board is not None:
            hw = hw.with_(board=board)
        hw, info = run_stage(hw, *stages[2])
        if info:
            history.append(info)

    for st in stages[3:]:
        hw, info = run_stage(hw, *st)
        if info:
            history.append(info)
    return hw, history
