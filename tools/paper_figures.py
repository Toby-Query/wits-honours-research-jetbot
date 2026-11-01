"""Figures for the IEEE paper (paper/figures/).

    .venv/bin/python tools/paper_figures.py

Reads only saved results; runs no simulation.
"""
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "paper" / "figures"
OUT.mkdir(parents=True, exist_ok=True)

BLUE, ORANGE, INK, MUTED = "#2a78d6", "#eb6834", "#222222", "#888888"
plt.rcParams.update({
    "font.family": "serif", "font.serif": ["Times New Roman", "Times", "DejaVu Serif"], "mathtext.fontset": "stix", "font.size": 8, "axes.titlesize": 8,
    "axes.labelsize": 8, "legend.fontsize": 7, "xtick.labelsize": 7,
    "ytick.labelsize": 7, "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": MUTED, "axes.linewidth": 0.6, "lines.linewidth": 1.4,
    "savefig.bbox": "tight", "savefig.pad_inches": 0.02,
})
COL = 3.45  # IEEE column width, inches


def straights():
    r1 = json.load(open(ROOT / "results/robot2/straights_result.json"))
    r2 = json.load(open(ROOT / "results/robot2/straights_result_refit_all50.json"))
    scale = r1["summary"]["scale"]
    fig, axes = plt.subplots(2, 1, figsize=(COL, 3.0), sharex=True)
    titles = ["(a) Round 1: one caster coefficient",
              "(b) Round 2: rolling and scrubbing caster"]
    for ax, res, title in zip(axes, (r1, r2), titles):
        runs = res["runs"]
        swerve = [r["drift_seen_m"] * scale > 0.25 for r in runs]
        x = [r["run"] for r in runs]
        y = [100 * r["residual_m"] for r in runs]
        ax.axhline(0, color=INK, lw=0.7)
        ax.axvspan(0.5, 5.5, color="#dddddd", lw=0, zorder=0)
        ax.scatter([a for a, s in zip(x, swerve) if not s], [b for b, s in zip(y, swerve) if not s],
                   s=14, color=BLUE, marker="o", edgecolor="white", lw=0.5, label="went straight (40)")
        ax.scatter([a for a, s in zip(x, swerve) if s], [b for b, s in zip(y, swerve) if s],
                   s=18, color=ORANGE, marker="^", edgecolor="white", lw=0.5, label="swerved left (10)")
        ax.set_title(title, loc="left")
        ax.set_ylabel("robot $-$ twin (cm)")
        ax.grid(axis="y", color="#eeeeee", lw=0.5)
        ax.set_ylim(-21, 18)
    axes[1].text(3, 16, "fit\nset", ha="center", va="top", fontsize=6.5, color=INK)
    axes[1].legend(loc="upper right", frameon=False, ncol=2, handletextpad=0.2, columnspacing=0.8)
    axes[1].set_xlabel("run number")
    axes[1].set_xlim(0, 51)
    fig.savefig(OUT / "straights.pdf")


def turns():
    r1 = json.load(open(ROOT / "data/real/2026-10-04/short_turns_24_video.json"))
    r2 = json.load(open(ROOT / "data/real/robot2-2026-10-22/short_turns_24_video.json"))
    sets = [("(a) Robot 1", r1["each_turn_deg"], r1["twin_each_deg"]),
            ("(b) Robot 2", [t["robot_deg"] for t in r2["turns"]],
             [t["twin_deg"] for t in r2["turns"]])]
    fig, axes = plt.subplots(1, 2, figsize=(COL, 1.75), sharey=True)
    for ax, (title, robot, twin) in zip(axes, sets):
        n = range(1, len(robot) + 1)
        ax.plot(n, twin, color=BLUE, label="twin")
        ax.scatter(n, robot, s=10, color=INK, zorder=3, label="robot")
        ax.set_title(title, loc="left")
        ax.set_xlabel("turn number")
        ax.grid(axis="y", color="#eeeeee", lw=0.5)
    axes[0].set_ylabel("angle turned (deg)")
    axes[0].legend(loc="lower left", frameon=False)
    fig.tight_layout(w_pad=0.6)
    fig.savefig(OUT / "turns.pdf")

def swerves():
    sw = json.load(open(ROOT / "results/robot2/swerve_test.json"))
    res = json.load(open(ROOT / "results/robot2/straights_result.json"))
    scale = res["summary"]["scale"]
    fig, ax = plt.subplots(figsize=(COL, 2.3))
    styles = {"weak": (BLUE, "-", "left wheel slow"),
              "strong": (ORANGE, "-", "right wheel fast"),
              "late": ("#1baf7a", "--", "left wheel starts late"),
              "early": ("#a07800", ":", "left wheel stops early")}
    for k, (c, ls, lab) in styles.items():
        cv = sw["hypotheses"][k]["curve"]
        ax.plot([100 * p["drift_m"] for p in cv], [p["turn_deg"] for p in cv],
                color=c, ls=ls, lw=1.3, label=lab)
    d = [100 * r["drift_seen_m"] * scale for r in res["runs"]]
    t = [r["heading_deg"] for r in res["runs"]]
    ax.scatter(d, t, s=9, color=INK, zorder=3, label="robot 2, 50 runs")
    ax.set_xlim(-15, 55)
    ax.set_ylim(-15, 75)
    ax.set_xlabel("drift to the left (cm)")
    ax.set_ylabel("turn (deg)")
    ax.grid(color="#eeeeee", lw=0.5)
    ax.legend(loc="upper left", frameon=False, fontsize=6.5)
    fig.savefig(OUT / "swerves.pdf")


if __name__ == "__main__":
    straights()
    turns()
    swerves()
    print("written to", OUT)
