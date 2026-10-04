"""The twin: the board's circuit and the robot's dynamics, stepped together.

    twin = JetBotTwin()
    twin.robot.set_motors(0.3, 0.3)       # the same call as on the real robot
    twin.sleep(2.0)                       # ... but time is yours to advance
    print(twin.telemetry())

Each millisecond: the wheel speeds go into the circuit as back-EMF, the
circuit is solved for the motor currents (and everything else: pack sag,
the 5 V rail, what the INA219 would report), the currents become torques,
and the dynamics move the robot.
"""
from __future__ import annotations

import math

import numpy as np

from elecsim import Simulation

from . import electrical
from .dynamics import DiffDrive
from .hardware import DEFAULT, Hardware


class Motor:
    """Stands in for `jetbot.Motor`: assign `.value` in [-1, 1]."""

    def __init__(self, twin, side, alpha=1.0, beta=0.0):
        self._twin, self._side = twin, side
        self.alpha, self.beta = alpha, beta
        self._value = 0.0
        self.speed = 0

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, v):
        self._value = float(v)
        self.speed = electrical.write_motor(self._twin.circuit, self._side,
                                            self._value, self.alpha, self.beta)


class Robot:
    """Stands in for `jetbot.Robot`, method for method."""

    def __init__(self, twin):
        self.left_motor = Motor(twin, "left")
        self.right_motor = Motor(twin, "right")

    def set_motors(self, left_speed, right_speed):
        self.left_motor.value = left_speed
        self.right_motor.value = right_speed

    def forward(self, speed=1.0, duration=None):
        self.set_motors(speed, speed)

    def backward(self, speed=1.0):
        self.set_motors(-speed, -speed)

    def left(self, speed=1.0):
        self.set_motors(-speed, speed)

    def right(self, speed=1.0):
        self.set_motors(speed, -speed)

    def stop(self):
        self.set_motors(0, 0)


# Columns every log has, real or simulated. The first block is what the real
# robot can measure about itself; the second is what only the twin knows.
OBSERVABLE = ["t", "cmd_l", "cmd_r", "bus_v", "charge_i", "p_5v"]
TRUTH = ["x", "y", "th", "v", "w", "wl", "wr", "phi_l", "phi_r", "i_l", "i_r", "i_pack",
         "v_12", "v_5", "soc", "slip_l", "slip_r", "t_drv", "t_buck"]


class JetBotTwin:
    def __init__(self, hw: Hardware = None, dt=1e-3, jetson_p=None, **overrides):
        hw = hw or DEFAULT
        if overrides:
            hw = hw.with_(**overrides)
        self.hw = hw
        self.dt = dt
        self.circuit = electrical.build(hw, fidelity="average", standalone=False)
        self.jetson = self.circuit["jetson"]
        if jetson_p is not None:
            self.jetson.inputs["p"] = jetson_p
        self.sim = Simulation(self.circuit, dt=dt)
        self.dyn = DiffDrive(hw)
        self.robot = Robot(self)
        self._dl = self.circuit["drive_left"]
        self._dr = self.circuit["drive_right"]
        self.t = 0.0

    # -- knobs ---------------------------------------------------------------
    def set_jetson_power(self, watts):
        self.jetson.inputs["p"] = watts

    def plug_charger(self, plugged=True):
        self.circuit["charger"].inputs["enabled"] = plugged

    def lift(self, lifted=True):
        """Wheels off the ground: the standard bench test."""
        self.dyn.lifted = lifted

    # -- time ------------------------------------------------------------------
    def step(self):
        s = self.dyn.s
        self._dl.inputs["omega"] = s.wl
        self._dr.inputs["omega"] = s.wr
        self.sim.step()
        self.dyn.step(self._dl.sig.get("torque", 0.0),
                      self._dr.sig.get("torque", 0.0), self.dt)
        self.t += self.dt

    def sleep(self, seconds, log=None, log_hz=50.0):
        """Advance the twin; optionally append telemetry rows to `log`."""
        n = int(round(seconds / self.dt))
        every = max(1, int(round(1.0 / (log_hz * self.dt))))
        for k in range(n):
            self.step()
            if log is not None and (k + 1) % every == 0:
                log.append(self.telemetry())

    # -- reading ------------------------------------------------------------------
    def telemetry(self):
        c, s = self.circuit, self.dyn.s
        f = self.dyn.forces
        return {
            "t": self.t,
            "cmd_l": self.robot.left_motor.value,
            "cmd_r": self.robot.right_motor.value,
            # what the robot's own sensors would report
            "bus_v": float(c["ina219"].sig["bus_v"]),
            "charge_i": float(c["ina219"].sig["current"]),
            "p_5v": float(self.jetson.sig["p"]),
            # what only the twin can see
            "x": s.x, "y": s.y, "th": s.th, "v": s.v, "w": s.w,
            "wl": s.wl, "wr": s.wr, "phi_l": s.phi_l, "phi_r": s.phi_r,
            "rim_l": s.wl * self.hw.wheel_radius,       # tyre surface speed, m/s
            "rim_r": s.wr * self.hw.wheel_radius,
            "i_l": float(self._dl.sig.get("i", 0.0)),
            "i_r": float(self._dr.sig.get("i", 0.0)),
            "i_pack": float(c["protector"].sig["i"]),
            "v_12": float(c["U3"].sig["v_in"]),
            "v_5": float(self.jetson.sig["v"]),
            "soc": float(c["cell1"].sig["soc"]),
            "slip_l": f.get("slip_l", 0.0), "slip_r": f.get("slip_r", 0.0),
            "t_drv": float(self._dl.sig.get("T", self.hw.ambient)),
            "t_buck": float(c["U3"].sig.get("T", self.hw.ambient)),
        }

    def run(self, profile, log_hz=50.0, jetson_p=None, lifted=False):
        """Run a command profile: [(seconds, left, right), ...]. Returns a Log."""
        if jetson_p is not None:
            self.set_jetson_power(jetson_p)
        self.lift(lifted)
        rows = []
        for seconds, left, right in profile:
            self.robot.set_motors(left, right)
            self.sleep(seconds, rows, log_hz)
        self.robot.stop()
        return Log.from_rows(rows)


class Log:
    """A table of telemetry: column name -> array. Real and simulated logs
    are the same type, which is what lets one be laid over the other."""

    def __init__(self, cols, meta=None):
        self.cols = {k: np.asarray(v, float) for k, v in cols.items()}
        self.meta = meta or {}

    @classmethod
    def from_rows(cls, rows, meta=None):
        keys = list(rows[0]) if rows else []
        return cls({k: [r[k] for r in rows] for k in keys}, meta)

    @classmethod
    def read_csv(cls, path):
        meta, header, rows = {}, None, []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("#"):
                    if "=" in line:
                        k, v = line[1:].split("=", 1)
                        meta[k.strip()] = v.strip()
                    continue
                if header is None:
                    header = line.split(",")
                    continue
                rows.append([float(x) if x not in ("", "nan") else math.nan
                             for x in line.split(",")])
        arr = np.array(rows) if rows else np.zeros((0, len(header or [])))
        return cls({k: arr[:, j] for j, k in enumerate(header or [])}, meta)

    def write_csv(self, path, keys=None):
        keys = list(keys or self.cols)
        with open(path, "w") as f:
            for k, v in self.meta.items():
                f.write(f"# {k} = {v}\n")
            f.write(",".join(keys) + "\n")
            n = len(self)
            for i in range(n):
                f.write(",".join(f"{self.cols[k][i]:.6g}" for k in keys) + "\n")

    def __getitem__(self, k):
        return self.cols[k]

    def __contains__(self, k):
        return k in self.cols

    def __len__(self):
        return len(next(iter(self.cols.values()))) if self.cols else 0

    @property
    def t(self):
        return self.cols["t"]

    def final(self, k):
        return float(self.cols[k][-1])

    def at(self, k, t):
        return float(np.interp(t, self.t, self.cols[k]))

    def mean(self, k, t0=None, t1=None):
        m = np.ones(len(self), bool)
        if t0 is not None:
            m &= self.t >= t0
        if t1 is not None:
            m &= self.t <= t1
        return float(np.mean(self.cols[k][m]))
