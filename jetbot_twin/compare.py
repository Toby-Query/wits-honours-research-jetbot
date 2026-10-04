"""Lay the twin over the real robot and say how far apart they are.

The real robot can report its bus voltage, what the Jetson is drawing, and
whatever you measured by hand. It cannot report motor current or wheel
speed -- it has no sensor for either. So the comparison is made on what both
sides have, and the twin's hidden quantities are judged by whether they
reproduce the visible ones.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .experiments import MANUAL_TOL, holds, predict_manual, run_on_twin
from .twin import Log


def estimate_soc(hw, v_rest, p_5v):
    """The state of charge at which the twin, at rest, shows this voltage."""
    from elecsim import Simulation

    from . import electrical

    def v_at(soc):
        c = electrical.build(hw.with_(soc0=soc), standalone=False)
        c["jetson"].inputs["p"] = p_5v
        Simulation(c)
        return float(c["ina219"].sig["bus_v"])
    lo, hi = 0.0, 1.0
    if v_rest >= v_at(1.0):
        return 1.0
    if v_rest <= v_at(0.0):
        return 0.0
    for _ in range(24):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if v_at(mid) < v_rest else (lo, mid)
    return 0.5 * (lo + hi)


def replay(exp, real: Log, hw, dt=None):
    """Run the twin through what the robot was put through.

    The twin is given the same commands, the Jetson power the robot actually
    measured, and the state of charge that matches the robot's voltage in
    the first second. Everything after that is prediction.
    """
    p = real["p_5v"] if "p_5v" in real else None
    have_p = p is not None and np.isfinite(p).any()
    if have_p:
        tt = real.t[np.isfinite(p)]
        pp = p[np.isfinite(p)]

        def p_fn(t):
            return float(np.interp(t, tt, pp))
    else:
        p_fn = None
    first = real.t <= max(0.5, min(1.0, exp.profile[0][0] * 0.8))
    v_rest = float(np.nanmean(real["bus_v"][first]))
    p_rest = float(np.nanmean(p[first])) if have_p else hw.jetson_p_idle
    hw = hw.with_(soc0=estimate_soc(hw, v_rest, p_rest))
    return run_on_twin(exp, hw, p_5v=p_fn, dt=dt), hw


@dataclass
class Comparison:
    experiment: str
    bus_rmse: float = math.nan            # V
    bus_bias: float = math.nan            # V, twin minus robot
    sag_real: list = field(default_factory=list)   # V, per driven segment
    sag_twin: list = field(default_factory=list)
    manual: dict = field(default_factory=dict)     # key -> (real, twin, tol)
    soc: float = math.nan

    def rows(self):
        out = [("bus voltage RMSE", f"{self.bus_rmse * 1e3:.0f} mV"),
               ("bus voltage bias (twin - robot)", f"{self.bus_bias * 1e3:+.0f} mV")]
        for k, (a, b) in enumerate(zip(self.sag_real, self.sag_twin)):
            out.append((f"sag in segment {k + 1}",
                        f"robot {a * 1e3:.0f} mV, twin {b * 1e3:.0f} mV"))
        for key, (r, t, tol) in self.manual.items():
            if isinstance(r, (list, tuple)):
                out.append((key, "robot " + " ".join(f"{x:.2f}" for x in r)))
                out.append(("", "twin  " + " ".join(f"{x:.2f}" for x in t)))
            else:
                err = t - r
                out.append((key, f"robot {r:.3g}, twin {t:.3g} "
                                 f"({err:+.3g}, {abs(err) / tol:.1f}x the "
                                 "measurement tolerance)"))
        return out

    def text(self):
        w = max(len(a) for a, _ in self.rows())
        return "\n".join([f"{self.experiment}"] +
                         [f"  {a:<{w}}  {b}" for a, b in self.rows()])


def sags(exp, log):
    """Voltage lost in each driven segment: rest level minus loaded level."""
    out = []
    for t0, t1 in holds(exp):
        base = log.mean("bus_v", max(0.0, t0 - 0.6), t0 - 0.05)
        load = log.mean("bus_v", t0 + 0.5 * (t1 - t0), t1 - 0.02)
        out.append(base - load)
    return out


def compare(exp, real: Log, twin_log: Log, manual=None, soc=math.nan):
    c = Comparison(exp.name, soc=soc)
    if "bus_v" in real and np.isfinite(real["bus_v"]).any():
        tw = np.interp(real.t, twin_log.t, twin_log["bus_v"])
        d = tw - real["bus_v"]
        d = d[np.isfinite(d)]
        c.bus_rmse = float(np.sqrt(np.mean(d ** 2)))
        c.bus_bias = float(np.mean(d))
        c.sag_real, c.sag_twin = sags(exp, real), sags(exp, twin_log)
    pred = predict_manual(exp, twin_log)
    for k, v in (manual or {}).items():
        if k in pred:
            c.manual[k] = (v, pred[k], MANUAL_TOL.get(k, 1.0))
    return c


def plot(exp, real: Log, twin_log: Log, path, title=None):
    """Bus voltage and Jetson power, robot against twin, plus what only the
    twin can show underneath."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ink, muted, grid = "#1a1a1a", "#6b6b6b", "#e6e6e6"
    c_real, c_twin, c_2, c_3 = "#1a1a1a", "#2a78d6", "#eb6834", "#1baf7a"
    fig, ax = plt.subplots(3, 1, figsize=(8.4, 7.2), sharex=True,
                           gridspec_kw={"height_ratios": [1.3, 1, 1]})
    for a in ax:
        a.grid(True, color=grid, lw=0.8)
        a.set_axisbelow(True)
        for s in ("top", "right"):
            a.spines[s].set_visible(False)
        for s in ("left", "bottom"):
            a.spines[s].set_color(muted)
        a.tick_params(colors=muted, labelsize=9)
        for t0, t1 in holds(exp):
            a.axvspan(t0, t1, color="#000000", alpha=0.045, lw=0)
    if real is not None and "bus_v" in real:
        ax[0].plot(real.t, real["bus_v"], color=c_real, lw=1.0, label="robot (INA219)")
    ax[0].plot(twin_log.t, twin_log["bus_v"], color=c_twin, lw=1.6, label="twin")
    ax[0].set_ylabel("bus voltage, V", color=ink, fontsize=10)
    ax[0].legend(frameon=False, fontsize=9, loc="lower left")
    ax[1].plot(twin_log.t, twin_log["i_l"], color=c_twin, lw=1.4, label="left motor")
    ax[1].plot(twin_log.t, twin_log["i_r"], color=c_2, lw=1.4, ls="--",
               label="right motor")
    ax[1].plot(twin_log.t, twin_log["i_pack"], color=muted, lw=1.2, label="pack")
    ax[1].set_ylabel("current, A (twin only)", color=ink, fontsize=10)
    ax[1].legend(frameon=False, fontsize=9, ncol=3, loc="upper right")
    ax[2].plot(twin_log.t, twin_log["rim_l"], color=c_twin, lw=1.4, label="left tyre")
    ax[2].plot(twin_log.t, twin_log["rim_r"], color=c_2, lw=1.4, ls="--",
               label="right tyre")
    ax[2].plot(twin_log.t, twin_log["v"], color=c_3, lw=1.8, label="robot")
    ax[2].set_ylabel("speed, m/s (twin only)", color=ink, fontsize=10)
    ax[2].set_xlabel("time, s", color=ink, fontsize=10)
    ax[2].legend(frameon=False, fontsize=9, ncol=3, loc="upper right")
    fig.suptitle(title or f"{exp.name}: shaded where the motors are commanded",
                 fontsize=11, color=ink, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path
