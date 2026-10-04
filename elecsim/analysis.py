"""Sweeps, sensitivities and Monte Carlo over anything you can call.

These work on a plain function `f(params: dict) -> dict of metrics`, so they
apply equally to a circuit, a vehicle model, or the two together.
"""
from __future__ import annotations

import numpy as np


def sweep(f, name, values, base=None):
    """Vary one parameter. Returns {metric: array} plus the swept values."""
    base = dict(base or {})
    rows = [f({**base, name: v}) for v in values]
    out = {k: np.array([r[k] for r in rows]) for k in rows[0]}
    out[name] = np.asarray(values)
    return out


def sensitivity(f, nominal, rel=0.10, names=None):
    """One-at-a-time: how much does each metric move per parameter?

    Returns {metric: [(param, d_metric/metric per d_param/param), ...]}
    sorted so the parameter that matters most comes first. A value of 0.5
    means a 10 % change in the parameter moves the metric 5 %.
    """
    y0 = f(dict(nominal))
    out = {k: [] for k in y0}
    for name in names or list(nominal):
        x0 = nominal[name]
        if not isinstance(x0, (int, float)) or x0 == 0:
            continue
        up = f({**nominal, name: x0 * (1 + rel)})
        dn = f({**nominal, name: x0 * (1 - rel)})
        for k in y0:
            if y0[k]:
                out[k].append((name, (up[k] - dn[k]) / (2 * rel * y0[k])))
            else:
                out[k].append((name, float("nan")))
    for k in out:
        out[k].sort(key=lambda p: -abs(p[1]) if p[1] == p[1] else 0)
    return out


def monte_carlo(f, nominal, spread, n=200, seed=0):
    """Sample uncertain parameters and collect the metrics.

    spread   {param: relative sigma}, e.g. {"r0": 0.2}; Gaussian, truncated
             at +/- 3 sigma. Parameters not listed stay at nominal.
    Returns {metric: array of n samples}, plus "_samples" with what was drawn.
    """
    rng = np.random.default_rng(seed)
    rows, draws = [], []
    for _ in range(n):
        p = dict(nominal)
        for name, sig in spread.items():
            z = float(np.clip(rng.standard_normal(), -3, 3))
            p[name] = nominal[name] * (1 + sig * z)
        draws.append(p)
        rows.append(f(p))
    out = {k: np.array([r[k] for r in rows]) for k in rows[0]}
    out["_samples"] = draws
    return out


def band(samples, lo=5, hi=95):
    """Percentile band of a Monte Carlo metric: (low, median, high)."""
    return tuple(float(np.percentile(samples, q)) for q in (lo, 50, hi))
