"""The experiments: each one runs unchanged on the twin and on the robot.

An experiment is a command profile -- a list of (seconds, left, right) -- plus
what the Jetson should be doing meanwhile and whether the wheels are on the
ground. The same JSON goes to `robot/agent.py` on the real JetBot and to
`JetBotTwin.run`, and both return a log with the same columns.

Each experiment exists to pin down particular numbers in hardware.py; the
`settles` field says which. docs/VALIDATION.md is the lab sheet.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


@dataclass
class Experiment:
    name: str
    profile: list                      # [(seconds, left, right), ...]
    doc: str = ""
    lifted: bool = False               # wheels off the ground
    held: bool = False                 # wheels gripped so they cannot turn
    jetson: str = "idle"               # idle | teleop | inference | stress
    settles: list = field(default_factory=list)
    measure: list = field(default_factory=list)    # what to write down by hand
    log_hz: float = 50.0
    dt: float = 2e-3                   # twin step; nothing moves -> can be coarse

    @property
    def duration(self):
        return sum(p[0] for p in self.profile)

    def to_json(self):
        return json.dumps(asdict(self), indent=1)


# The command used for every test on the floor. It has to be clear of the
# dead band (see docs/ELECTRICAL.md): a robot that is barely moving tells you
# about static friction and nothing else. If the wheels-up sweep shows your
# robot running smoothly from a lower command, lower this.
FLOOR = 0.5
# Spinning is done a little slower. A fast spin loads one tyre past its grip
# (the centre of mass is behind the axle, so the robot tries to orbit rather
# than turn on the spot), and the twin has no model of tyres sliding sideways.
SPIN = 0.4
# The square's corners: 0.29 s at this command is a quarter-turn in the
# calibrated twin. (As first written the corners used FLOOR for 0.3 s, which
# is nearer a half-turn: the "square" would have shuttled back and forth.)
TURN = 0.3


def _steps(levels, hold=2.0, rest=1.0, spin=False):
    out = [(rest, 0.0, 0.0)]
    for v in levels:
        out.append((hold, -v if spin else v, v))
        out.append((rest, 0.0, 0.0))
    return out


EXPERIMENTS = {e.name: e for e in [
    Experiment(
        "rest", [(20.0, 0.0, 0.0)],
        "Sit still. The baseline for everything else.", dt=0.02,
        settles=["jetson_p_idle", "open-circuit pack voltage (state of charge)"]),
    Experiment(
        "jetson_load", [(10.0, 0, 0), (20.0, 0, 0), (10.0, 0, 0)],
        "Sit still while the Jetson's CPU is loaded for the middle 20 s. The "
        "Jetson measures its own power, so this is a KNOWN load step: the "
        "voltage it costs gives the pack resistance without involving the "
        "motors at all.",
        jetson="stress", dt=0.02,
        settles=["cell_r0 + holder_r (the pack's series resistance)",
                 "jetson_p_max", "cell_r1, cell_c1 (from the recovery)"]),
    Experiment(
        "wheels_up_sweep", _steps([0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0]),
        "On a stand, wheels in the air. Step through the commands. Film one "
        "wheel (a strip of tape on the tyre) and count turns in each 2 s.",
        lifted=True,
        settles=["motor_rpm_noload / motor_v_rated (the motor's ke)",
                 "the dead band: which chip is fitted, and motor_l",
                 "motor friction"],
        measure=["wheel turns in each 2 s hold, per command"]),
    Experiment(
        "stall", [(2.0, 0, 0), (1.5, 0.5, 0.5), (3.0, 0, 0)],
        "Hold both wheels firmly, command 0.5 for 1.5 s. With the speed "
        "pinned at zero there is no back-EMF, so the sag is all resistance.",
        lifted=True, held=True,
        settles=["motor_r (through the stall current the sag implies)"]),
    Experiment(
        "straight", [(1.0, 0, 0), (2.0, FLOOR, FLOOR), (2.0, 0, 0)],
        "On the floor, from a tape line, with about 3 m clear ahead. Mark "
        "where it is when the motors cut (film it) and where it stops.",
        settles=["gear_eff, c_rr, mu_caster (how far it gets, and how far it "
                 "rolls on afterwards)", "mu_tyre (does it spin its wheels on "
                 "launch?)", "left/right mismatch (the drift)"],
        measure=["distance travelled, m", "of that, distance after the motors "
                 "cut, m", "sideways drift, m", "heading change, degrees"]),
    Experiment(
        "spin", [(1.0, 0, 0), (2.0, -SPIN, SPIN), (2.0, 0, 0)],
        "Spin on the spot, counter-clockwise from above. Count the turns "
        "(tape on the floor under the camera, film from above).",
        settles=["mu_caster (the caster is dragged sideways the whole time)",
                 "track", "yaw inertia"],
        measure=["total rotation, degrees",
                 "how far the middle of the axle ended from where it began, m"]),
    Experiment(
        "step_response", [(1.0, 0, 0), (1.0, FLOOR, FLOOR), (1.5, 0, 0),
                          (1.0, -FLOOR, -FLOOR), (1.5, 0, 0)],
        "Forward then back. Ends near where it started.",
        settles=["rotor_inertia, mass (through the rise time of the sag)",
                 "forward/reverse asymmetry"],
        measure=["final offset from the start, m"]),
    Experiment(
        "square", [(1.0, 0, 0)] + [(1.0, FLOOR, FLOOR), (0.6, 0, 0),
                                   (0.29, -TURN, TURN), (0.6, 0, 0)] * 4,
        "An open-loop square, about 0.7 m a side, turning left at each corner. "
        "Nothing settles on this one: it is the test. "
        "Whatever the twin predicts for where the robot ends up, after the "
        "other experiments have calibrated it, is the result to report.",
        measure=["final position x, y, m", "final heading, degrees"]),
    Experiment(
        "square_small", [(1.0, 0, 0)] + [(0.6, FLOOR, FLOOR), (0.6, 0, 0),
                                         (0.29, -TURN, TURN), (0.6, 0, 0)] * 4,
        "The same open-loop square with shorter sides, about 0.4 m, to fit "
        "inside a 2 x 2 block of 50 cm tiles with the phone held still above. "
        "Held out, like the larger one.",
        measure=["final position x, y, m", "final heading, degrees"]),
    Experiment(
        "short_turns", [(1.5, 0, 0)] + [(0.29, -TURN, TURN), (1.2, 0, 0)] * 10,
        "The square's corner on its own, ten times: 0.29 s to the left, then "
        "a rest. Film from above, close. How much the ten differ is how "
        "repeatable a short turn is on this floor.",
        settles=["how repeatable a short turn is",
                 "motor_friction_static_ratio (breakaway friction)"],
        measure=["the angle turned in each of the ten"]),
    Experiment(
        "short_turns_24", [(1.5, 0, 0)] + [(0.29, -TURN, TURN), (1.2, 0, 0)] * 24,
        "The same corner, twenty-four times, after the agent's timing was "
        "tightened: enough to say whether the twin's bias is real.",
        settles=["whether the twin is biased on a short turn"],
        measure=["the angle turned in each"]),
]}

JETSON_POWER = {"idle": "jetson_p_idle", "teleop": "jetson_p_teleop",
                "inference": "jetson_p_inference", "stress": "jetson_p_max"}


def run_on_twin(exp, hw=None, twin=None, p_5v=None, dt=None, **overrides):
    """Run an experiment on the twin. Returns a Log.

    p_5v   optional fn(t) -> watts: what the Jetson was really drawing, taken
           from a real log, so the twin is not also being asked to guess it.
    """
    from .twin import JetBotTwin, Log
    twin = twin or JetBotTwin(hw, dt=dt or exp.dt, **overrides)
    hw = twin.hw
    twin.lift(exp.lifted)
    twin.dyn.held = exp.held
    rows = []
    every = max(1, int(round(1.0 / (exp.log_hz * twin.dt))))
    k_step = 0
    for k, (seconds, left, right) in enumerate(exp.profile):
        if p_5v is None:
            if exp.jetson == "stress":      # loaded only in the middle segment
                twin.set_jetson_power(hw.jetson_p_max if k == 1 else hw.jetson_p_idle)
            else:
                twin.set_jetson_power(getattr(hw, JETSON_POWER[exp.jetson]))
        twin.robot.set_motors(left, right)
        for _ in range(int(round(seconds / twin.dt))):
            if p_5v is not None:
                twin.set_jetson_power(p_5v(twin.t))
            twin.step()
            k_step += 1
            if k_step % every == 0:
                rows.append(twin.telemetry())
    twin.robot.stop()
    return Log.from_rows(rows, {"experiment": exp.name, "source": "twin",
                                "board": hw.board})


# --------------------------------------------------------------------------
# the hand measurements, and the same quantities read off a twin log
# --------------------------------------------------------------------------
def holds(exp):
    """(t_start, t_end) of every segment in which a motor is commanded."""
    out, t = [], 0.0
    for seconds, left, right in exp.profile:
        if left or right:
            out.append((t, t + seconds))
        t += seconds
    return out


def predict_manual(exp, log):
    """What a tape measure and a stopwatch would have recorded."""
    import math

    import numpy as np
    n = exp.name
    if n == "straight":
        t_off = holds(exp)[-1][1]
        return {"distance_m": log.final("x"),
                "coast_m": log.final("x") - log.at("x", t_off),
                "drift_m": log.final("y"),
                "heading_deg": math.degrees(log.final("th"))}
    if n == "spin":
        return {"rotation_deg": math.degrees(log.final("th")),
                "wander_m": math.hypot(log.final("x"), log.final("y"))}
    if n == "wheels_up_sweep":
        tl, tr = [], []
        for t0, t1 in holds(exp):
            t1 = t1 + 0.9                     # include the run-down
            tl.append((log.at("phi_l", t1) - log.at("phi_l", t0)) / (2 * math.pi))
            tr.append((log.at("phi_r", t1) - log.at("phi_r", t0)) / (2 * math.pi))
        rpm = [abs(log.at("wl", t1 - 0.05)) * 9.549 for t0, t1 in holds(exp)]
        return {"turns_l": tl, "turns_r": tr, "rpm": rpm}
    if n == "step_response":
        return {"offset_m": log.final("x")}
    if n.startswith("short_turns"):
        th = [math.degrees(v) for v in log["th"]]
        out, t = [], exp.profile[0][0]
        for seconds, left, right in exp.profile[1:]:
            if left < 0:
                before = log.at("th", t - 0.05)
                after = log.at("th", min(t + 0.95, log.t[-1]))
                out.append(math.degrees(after - before))
            t += seconds
        return {"each_turn_deg": out}
    if n.startswith("square"):
        return {"final_x_m": log.final("x"), "final_y_m": log.final("y"),
                "final_heading_deg": math.degrees(log.final("th")) % 360.0}
    return {}


# how well a careful person can measure each of those
MANUAL_TOL = {"distance_m": 0.01, "drift_m": 0.01, "heading_deg": 3.0,
              "coast_m": 0.01, "rotation_deg": 10.0, "wander_m": 0.02, "turns_l": 0.25,
              "turns_r": 0.25, "rpm": 8.0, "each_turn_deg": 5.0, "offset_m": 0.01, "final_x_m": 0.02,
              "final_y_m": 0.02, "final_heading_deg": 5.0}
