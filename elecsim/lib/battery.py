"""Cells and packs."""
from __future__ import annotations

import math

import numpy as np

from ..core import Component, Limit, Param, Port

# Open-circuit voltage against state of charge. Typical curves; the cell you
# actually have will differ by tens of millivolts in the middle and more at
# the ends, which is what a discharge test on the real pack is for.
OCV = {
    # cobalt/NMC 18650 (ICR/INR/NCR), 4.2 V full
    "li-ion": [(0.00, 3.00), (0.02, 3.20), (0.05, 3.35), (0.10, 3.47),
               (0.20, 3.58), (0.30, 3.65), (0.40, 3.71), (0.50, 3.78),
               (0.60, 3.86), (0.70, 3.94), (0.80, 4.03), (0.90, 4.11),
               (1.00, 4.20)],
    # LiPo pouch
    "lipo": [(0.00, 3.27), (0.05, 3.61), (0.10, 3.69), (0.20, 3.73),
             (0.30, 3.77), (0.40, 3.79), (0.50, 3.82), (0.60, 3.87),
             (0.70, 3.94), (0.80, 4.02), (0.90, 4.11), (1.00, 4.20)],
    # LiFePO4
    "lifepo4": [(0.00, 2.50), (0.05, 3.00), (0.10, 3.18), (0.20, 3.22),
                (0.50, 3.28), (0.80, 3.32), (0.90, 3.34), (0.97, 3.40),
                (1.00, 3.60)],
}


class Cell(Component):
    """One cell: an OCV(SOC) source behind R0, with one RC pair for the slow
    part of the sag. Tracks its own state of charge.

        v = OCV(soc) - i R0 - v1,     dv1/dt = i / C1 - v1 / (R1 C1)
    """
    p = Port("power_out")
    n = Port("passive")
    capacity_ah = Param(unit="Ah", min=1e-6)
    soc = Param(1.0, min=0.0, max=1.0, doc="initial state of charge")
    r0 = Param(unit="ohm", min=1e-6, doc="instantaneous internal resistance")
    r1 = Param(0.0, unit="ohm", min=0, doc="slow polarisation resistance")
    c1 = Param(1000.0, unit="F", min=1e-9, doc="polarisation capacitance")
    chemistry = Param("li-ion", doc="a key of OCV, or a [(soc, volts), ...] table")
    v_min = Param(2.5, unit="V", doc="do not discharge below")
    v_max = Param(4.25, unit="V", doc="do not charge above")
    i_max = Param(None, unit="A", doc="maximum continuous discharge current")
    i_charge_max = Param(None, unit="A")

    limits = (Limit("v", min="v_min", why="cell over-discharged"),
              Limit("v", max="v_max", why="cell over-charged"))

    def __init__(self, name, **kw):
        super().__init__(name, **kw)
        table = OCV[self.chemistry] if isinstance(self.chemistry, str) \
            else self.chemistry
        self._s = np.array([a for a, _ in table])
        self._v = np.array([b for _, b in table])
        self.state_soc = self._soc_set = float(self.soc)
        self.v1 = 0.0
        self._i = None
        if self.i_max is not None:
            self.limit("i", max="i_max", why="cell discharge rating")
        if self.i_charge_max is not None:
            self.limit("i", min=-float(self.i_charge_max),
                       why="cell charge rating")

    def refresh(self):
        if self.soc != self._soc_set:          # the initial charge was edited
            self.state_soc = self._soc_set = float(self.soc)

    def ocv(self, soc=None):
        return float(np.interp(self.state_soc if soc is None else soc,
                               self._s, self._v))

    def stamp(self, s):
        g = 1.0 / self.r0
        s.G(self.p, self.n, g)
        s.I(self.n, self.p, g * (self.ocv() - self.v1))

    def _i_out(self, s):
        return (self.ocv() - self.v1 - s.vd(self.p, self.n)) / self.r0

    def update(self, s, dt):
        i = self._i_out(s)
        self._i = i
        self.state_soc = min(1.0, max(0.0, self.state_soc
                                      - i * dt / (3600.0 * self.capacity_ah)))
        if self.r1 > 0:
            a = math.exp(-dt / (self.r1 * self.c1))
            self.v1 = i * self.r1 + (self.v1 - i * self.r1) * a

    def measure(self, s):
        # after a step, the current is the one the step was solved with
        i = self._i_out(s) if self._i is None else self._i
        heat = i * i * self.r0 + (self.v1 ** 2 / self.r1 if self.r1 > 0 else 0.0)
        return {"v": s.vd(self.p, self.n), "i": i, "soc": self.state_soc,
                "ocv": self.ocv(), "heat": heat}


def series_pack(circuit, prefix, n, neg, pos, **cell_kw):
    """Add n cells in series from net `neg` up to net `pos`.

    Returns (cells, tap_nets): tap_nets[k] is the net at the top of cell k,
    so tap_nets[-1] == pos. Per-cell overrides: pass a list for any
    parameter and each cell takes its own element.
    """
    cells, taps = [], []
    low = neg
    for k in range(n):
        kw = {a: (b[k] if isinstance(b, (list, tuple)) and a != "chemistry"
                  else b) for a, b in cell_kw.items()}
        c = circuit.add(Cell(f"{prefix}{k + 1}", **kw))
        high = pos if k == n - 1 else f"{prefix}_tap{k + 1}"
        circuit.connect(low, c.n)
        circuit.connect(high, c.p)
        cells.append(c)
        taps.append(high)
        low = high
    return cells, taps


class PackProtector(Component):
    """A series protection switch for a multi-cell pack (S-8254 and kin).

    Sits between the pack and the system and opens on a cell that is too
    low, a cell that is too high, or too much current. As in the real
    circuit, opening the discharge FET still lets charge current in through
    its body diode, and opening the charge FET still lets the pack discharge.

        bat   pack side          out   system side
        vss   pack negative      tap1..tapN  top of each cell (sense only)
    """
    bat = Port()
    out = Port()
    vss = Port("ground")
    for _k in range(1, 5):
        locals()[f"tap{_k}"] = Port("analog_in", optional=True)
    del _k

    cells = Param(3)
    r_on = Param(unit="ohm", min=1e-6, doc="both FET groups in series, on")
    vf = Param(0.7, unit="V", doc="FET body diode drop")
    v_ov = Param(4.35, unit="V", doc="overcharge detect, per cell")
    v_ov_release = Param(4.15, unit="V")
    v_uv = Param(2.40, unit="V", doc="overdischarge detect, per cell")
    v_uv_release = Param(3.00, unit="V")
    i_oc = Param(unit="A", doc="discharge overcurrent detect")
    t_uv = Param(1.0, unit="s", doc="overdischarge detection delay")
    t_ov = Param(1.0, unit="s")
    t_oc = Param(0.010, unit="s", doc="overcurrent detection delay")
    t_retry = Param(1.0, unit="s", doc="time open after an overcurrent trip")
    linear = False

    def __init__(self, name, **kw):
        super().__init__(name, **kw)
        from .semis import diode_fit
        self.i_s = diode_fit(self.vf, 1.0, 1.8)
        self.state = "on"             # on | uv | ov | oc
        self._timer = {"uv": 0.0, "ov": 0.0, "oc": 0.0, "retry": 0.0}
        self._v = 0.0
        self.trips = 0

    def refresh(self):
        from .semis import diode_fit
        self.i_s = diode_fit(self.vf, 1.0, 1.8)

    def _cell_voltages(self, s):
        taps = [getattr(self, f"tap{k}") for k in range(1, int(self.cells) + 1)]
        v = [s.v(p) for p in taps]
        base = s.v(self.vss)
        out, low = [], base
        for x in v:
            out.append(x - low)
            low = x
        return out

    def stamp(self, s):
        from .semis import diode_iv, limit_junction
        if self.state == "on":
            s.G(self.bat, self.out, 1.0 / self.r_on)
            return
        s.G(self.bat, self.out, 1e-9)
        if self.state == "oc":
            return                               # both paths held open
        # uv: discharge FET open, charging still possible (out -> bat)
        # ov: charge FET open, discharge still possible (bat -> out)
        a, k = (self.out, self.bat) if self.state == "uv" else (self.bat, self.out)
        v = limit_junction(s.vd(a, k), self._v, self.i_s, 1.8)
        self._v = v
        i, g = diode_iv(v, self.i_s, 1.8)
        s.G(a, k, g)
        s.I(a, k, i - g * v)

    def update(self, s, dt):
        vc = self._cell_voltages(s)
        i = self._current(s)
        self._vc, self._i = vc, i
        T = self._timer
        if self.state == "on":
            T["uv"] = T["uv"] + dt if min(vc) < self.v_uv else 0.0
            T["ov"] = T["ov"] + dt if max(vc) > self.v_ov else 0.0
            T["oc"] = T["oc"] + dt if i > self.i_oc else 0.0
            for why in ("oc", "uv", "ov"):
                if T[why] >= getattr(self, "t_" + why):
                    self.state, T[why], T["retry"] = why, 0.0, 0.0
                    self.trips += 1
                    break
        elif self.state == "uv":
            if min(vc) > self.v_uv_release:
                self.state = "on"
        elif self.state == "ov":
            if max(vc) < self.v_ov_release:
                self.state = "on"
        elif self.state == "oc":
            T["retry"] += dt
            if T["retry"] >= self.t_retry:
                self.state = "on"

    def _current(self, s):
        from .semis import diode_iv
        v = s.vd(self.bat, self.out)
        if self.state == "on":
            return v / self.r_on
        if self.state == "uv":
            return -diode_iv(-v, self.i_s, 1.8)[0]
        if self.state == "ov":
            return diode_iv(v, self.i_s, 1.8)[0]
        return 0.0

    def measure(self, s):
        v = s.vd(self.bat, self.out)
        i = self._current(s)
        vc = self._cell_voltages(s)
        return {"v": v, "i": i, "heat": abs(v * i), "on": float(self.state == "on"),
                "tripped": float(self.state != "on"), "trips": float(self.trips),
                "cell_min": min(vc), "cell_max": max(vc)}
