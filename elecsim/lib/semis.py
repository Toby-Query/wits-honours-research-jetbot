"""Diodes and MOSFET switches."""
from __future__ import annotations

import math

from ..core import Component, Limit, Param, Port

VT = 0.02585            # thermal voltage at 300 K


def diode_fit(vf, i_f, n=1.8):
    """Saturation current that gives forward drop `vf` at current `i_f`."""
    return i_f / (math.exp(vf / (n * VT)) - 1.0)


def diode_iv(v, i_s, n):
    """Shockley current and slope, continued linearly above 40 n Vt.

    The linear continuation is what keeps Newton from overflowing on a bad
    first guess; a real diode is series-resistance-limited up there anyway.
    """
    nvt = n * VT
    x = v / nvt
    if x > 40.0:
        e = math.exp(40.0)
        g = i_s * e / nvt
        return i_s * (e - 1.0) + g * (v - 40.0 * nvt), g
    if x < -40.0:
        return -i_s, 1e-12
    e = math.exp(x)
    return i_s * (e - 1.0), i_s * e / nvt + 1e-12


def limit_junction(v_new, v_old, i_s, n):
    """SPICE's pnjlim: stop the junction voltage leaping up the exponential."""
    nvt = n * VT
    v_crit = nvt * math.log(nvt / (math.sqrt(2.0) * i_s))
    if v_new > v_crit and abs(v_new - v_old) > 2.0 * nvt:
        if v_old > 0:
            arg = 1.0 + (v_new - v_old) / nvt
            return v_old + nvt * math.log(arg) if arg > 0 else v_crit
        return nvt * math.log(max(v_new / nvt, 1e-30))
    return v_new


class Diode(Component):
    """A diode, specified by its forward drop at a stated current."""
    a = Port(doc="anode")
    k = Port(doc="cathode")
    vf = Param(0.7, unit="V", doc="forward drop at i_f")
    i_f = Param(1.0, unit="A", doc="current at which vf is specified")
    n = Param(1.8, doc="ideality")
    i_max = Param(None, unit="A")
    linear = False
    fault_modes = ("open", "short")

    def __init__(self, name, **kw):
        super().__init__(name, **kw)
        self.i_s = diode_fit(self.vf, self.i_f, self.n)
        self._v = 0.0
        if self.i_max is not None:
            self.limit("i", max="i_max", why="diode forward current")

    def refresh(self):
        self.i_s = diode_fit(self.vf, self.i_f, self.n)

    def stamp(self, s):
        v = limit_junction(s.vd(self.a, self.k), self._v, self.i_s, self.n)
        self._v = v
        i, g = diode_iv(v, self.i_s, self.n)
        s.G(self.a, self.k, g)
        s.I(self.a, self.k, i - g * v)

    def measure(self, s):
        v = s.vd(self.a, self.k)
        i, _ = diode_iv(v, self.i_s, self.n)
        return {"v": v, "i": i, "heat": v * i}


class MosfetSwitch(Component):
    """A MOSFET used as a switch: on-resistance, off-leakage, body diode.

    Conducts d -> s when on. The body diode conducts s -> d regardless, which
    is the part people forget. `on` is an input.
    """
    d = Port()
    s = Port()
    rds_on = Param(unit="ohm", min=1e-9)
    vf_body = Param(0.8, unit="V")
    i_max = Param(None, unit="A")
    v_max = Param(None, unit="V")
    on = Param(False)
    linear = False
    fault_modes = ("open", "short")

    def __init__(self, name, **kw):
        super().__init__(name, **kw)
        self.inputs["on"] = bool(self.on)
        self.i_s = diode_fit(self.vf_body, 1.0, 1.8)
        self._v = 0.0
        if self.i_max is not None:
            self.limit("i", max="i_max", absolute=True, why="drain current")
        if self.v_max is not None:
            self.limit("v", max="v_max", absolute=True, why="drain-source voltage")

    def refresh(self):
        self.i_s = diode_fit(self.vf_body, 1.0, 1.8)

    def stamp(self, s):
        if self.inputs["on"]:
            s.G(self.d, self.s, 1.0 / self.rds_on)
            return
        s.G(self.d, self.s, 1e-9)
        v = limit_junction(s.vd(self.s, self.d), self._v, self.i_s, 1.8)
        self._v = v
        i, g = diode_iv(v, self.i_s, 1.8)
        s.G(self.s, self.d, g)
        s.I(self.s, self.d, i - g * v)

    def measure(self, s):
        v = s.vd(self.d, self.s)
        if self.inputs["on"]:
            i = v / self.rds_on
        else:
            i = -diode_iv(-v, self.i_s, 1.8)[0] + v * 1e-9
        return {"v": v, "i": i, "heat": v * i, "on": float(self.inputs["on"])}
