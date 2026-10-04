"""A stand-in for the real robot, for testing the comparison pipeline.

NOT DATA. This is a second twin with its numbers moved, read through noisy
sensors and a rounding tape measure. It exists so that `compare` and
`calibrate` can be shown to recover known answers before they are trusted
with unknown ones. Nothing produced here says anything about the real
JetBot, and every log it writes is marked `source = synthetic`.
"""
from __future__ import annotations

import numpy as np

from .experiments import EXPERIMENTS, MANUAL_TOL, predict_manual, run_on_twin
from .hardware import DEFAULT
from .twin import Log

# A robot that differs from the catalogue the way a real one plausibly would.
TRUTH = dict(
    motor_rpm_noload=205.0, motor_i_noload=0.21, motor_r=5.1,
    cell_r0=0.085, wheel_radius=0.0333, gear_eff=0.55, mu_caster=0.36,
    jetson_p_idle=3.1, jetson_p_max=8.4, mass_override=0.78, soc0=0.83,
)


def stand_in(**extra):
    return DEFAULT.with_(**{**TRUTH, **extra})


def observe(exp, hw=None, seed=0):
    """Run `exp` on the stand-in and return what a robot would have logged
    and what a person would have written down: (Log, manual dict)."""
    rng = np.random.default_rng(seed + abs(hash(exp.name)) % 10000)
    hw = hw or stand_in()
    full = run_on_twin(exp, hw)
    n = len(full)
    cols = {
        "t": full["t"] + rng.normal(0, 0.002, n).cumsum() * 0.0,
        "cmd_l": full["cmd_l"], "cmd_r": full["cmd_r"],
        "bus_v": np.round((full["bus_v"] + rng.normal(0, 0.006, n)) / 0.004) * 0.004,
        "charge_i": full["charge_i"] + rng.normal(0, 0.001, n),
        "p_5v": full["p_5v"] * (1 + rng.normal(0, 0.02, n)),
    }
    log = Log(cols, {"experiment": exp.name, "source": "synthetic"})
    manual = {}
    for k, v in predict_manual(exp, full).items():
        tol = MANUAL_TOL.get(k, 1.0)
        if isinstance(v, list):
            manual[k] = [float(round(x / tol) * tol) for x in v]
        else:
            manual[k] = float(round((v + rng.normal(0, tol / 2)) / tol) * tol)
    return log, manual
