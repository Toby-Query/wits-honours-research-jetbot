"""Draw the figures the documents refer to.

    .venv/bin/python tools/make_figures.py

Everything is recomputed from hardware.py, so a figure can never disagree
with the code that it illustrates.
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from elecsim import Simulation  # noqa: E402
from jetbot_twin import electrical  # noqa: E402
from jetbot_twin.hardware import DEFAULT  # noqa: E402
from jetbot_twin.twin import JetBotTwin  # noqa: E402

OUT = ROOT / "docs" / "img"
INK, MUTED, GRID = "#1a1a1a", "#6b6b6b", "#e6e6e6"
C1, C2, C3 = "#2a78d6", "#eb6834", "#1baf7a"
NAMES = {"drv8870": "2 x DRV8870 (drive / coast)", "tb6612": "TB6612FNG (drive / brake)"}


def style(ax):
    ax.grid(True, color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=9)


def waveform():
    """Motor current through one PWM period, both ways of driving it."""
    fig, axes = plt.subplots(1, 2, figsize=(9.0, 3.4), sharey=True)
    for ax, board, colour in zip(axes, ("drv8870", "tb6612"), (C1, C2)):
        hw = DEFAULT.with_(board=board)
        c = electrical.build(hw, fidelity="switching", standalone=False)
        name = "motor_left"
        c[name].inputs["omega"] = c["motor_right"].inputs["omega"] = 8.0
        electrical.write_motor(c, "left", 0.3)
        electrical.write_motor(c, "right", 0.3)
        period = 1.0 / c["pca9685"].freq
        sim = Simulation(c, dt=period / 400)
        sim.run(10 * period, record=[f"{name}.i"])
        res = sim.run(3 * period, record=[f"{name}.i"])
        t = (res.t - res.t[0]) * 1e3
        i = np.abs(res[f"{name}.i"])
        ax.plot(t, i, color=colour, lw=2)
        ax.axhline(i.mean(), color=MUTED, lw=1, ls="--")
        ax.annotate(f"average {i.mean():.2f} A", (t[-1], i.mean()),
                    xytext=(-4, 5), textcoords="offset points", ha="right",
                    fontsize=9, color=INK)
        style(ax)
        ax.set_title(NAMES[board], fontsize=10, color=INK, loc="left")
        ax.set_xlabel("time, ms", fontsize=10, color=INK)
    axes[0].set_ylabel("winding current, A", fontsize=10, color=INK)
    fig.suptitle("The same command (0.3) and wheel speed (76 rpm), two driver chips",
                 fontsize=11, color=INK, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(OUT / "pwm_waveform.png", dpi=150)
    plt.close(fig)


def speed_curve():
    """Steady speed against command: on the floor, and wheels up."""
    cmds = np.round(np.arange(0.05, 1.0001, 0.05), 2)
    fig, axes = plt.subplots(1, 2, figsize=(9.0, 3.6))
    for board, colour, ls in (("drv8870", C1, "-"), ("tb6612", C2, "--")):
        floor, free = [], []
        for cmd in cmds:
            tw = JetBotTwin(board=board, dt=2e-3)
            floor.append(tw.run([(2.5, cmd, cmd)]).final("v"))
            tw = JetBotTwin(board=board, dt=2e-3)
            free.append(tw.run([(1.5, cmd, cmd)], lifted=True).final("wl") * 9.549)
        axes[0].plot(cmds, free, color=colour, lw=2, ls=ls, marker="o", ms=4,
                     label=NAMES[board])
        axes[1].plot(cmds, floor, color=colour, lw=2, ls=ls, marker="o", ms=4,
                     label=NAMES[board])
    axes[0].set_ylabel("wheel speed, rpm", fontsize=10, color=INK)
    axes[0].set_title("Wheels off the ground", fontsize=10, color=INK, loc="left")
    axes[1].set_ylabel("robot speed, m/s", fontsize=10, color=INK)
    axes[1].set_title("On the floor", fontsize=10, color=INK, loc="left")
    for ax in axes:
        style(ax)
        ax.set_xlabel("command passed to set_motors()", fontsize=10, color=INK)
        ax.legend(frameon=False, fontsize=9, loc="upper left")
    fig.suptitle("Predicted, before calibration: which chip is on the board "
                 "changes what a command means", fontsize=11, color=INK,
                 x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(OUT / "speed_vs_command.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    waveform()
    speed_curve()
    print("wrote", *sorted(p.name for p in OUT.glob("*.png")))
