"""Ideal-ish sources and simple loads."""
from __future__ import annotations

from ..core import Component, Limit, Param, Port


class VoltageSource(Component):
    """A voltage, optionally a function of time, behind a series resistance.

    `v` may be a number or fn(t). `i_limit` turns it into a bench supply that
    goes constant-current instead of delivering more.
    """
    p = Port("power_out")
    n = Port("passive")
    v = Param(unit="V")
    r = Param(0.0, unit="ohm", min=0)
    i_limit = Param(None, unit="A", doc="constant-current limit")
    enabled = Param(True)
    branches = 1

    def __init__(self, name, **kw):
        super().__init__(name, **kw)
        self.inputs["enabled"] = bool(self.enabled)
        self._cc = False
        self.linear = self.i_limit is None

    def volts(self, t):
        return self.v(t) if callable(self.v) else self.v

    def pre_step(self, t, dt):
        if self.i_limit is None:
            return
        i_out = self.sig.get("i", 0.0)
        v_term = self.sig.get("v", 0.0)
        if not self._cc and i_out > self.i_limit:
            self._cc = True
        elif self._cc and v_term >= self.volts(t):
            self._cc = False

    def stamp(self, s):
        br = s.row(self)
        if not self.inputs["enabled"]:
            # open: i = 0
            s.A[br, br] += 1.0
            return
        if self._cc:
            # i_out = i_limit; branch unknown is current INTO p, so -i_limit
            if self.p.node >= 0:
                s.A[self.p.node, br] += 1.0
            if self.n.node >= 0:
                s.A[self.n.node, br] -= 1.0
            s.A[br, br] += 1.0
            s.b[br] += -self.i_limit
            return
        s.V(self, self.p, self.n, self.volts(s.t), r=self.r)

    def measure(self, s):
        i_out = -s.i(self)
        v = s.vd(self.p, self.n)
        return {"v": v, "i": i_out, "p": v * i_out,
                "heat": i_out * i_out * self.r, "cc": float(self._cc)}


class CurrentSource(Component):
    """Pushes `i` out of pin p (through the external circuit, back into n)."""
    p = Port()
    n = Port()
    i = Param(unit="A")

    def stamp(self, s):
        cur = self.inputs.get("i", self.i)
        s.I(self.n, self.p, cur(s.t) if callable(cur) else cur)

    def measure(self, s):
        return {"v": s.vd(self.p, self.n), "i": self.inputs.get("i", self.i)}


class ConstantCurrentLoad(Component):
    """Draws a fixed current while it has headroom; resistive below v_min."""
    p = Port("power_in")
    n = Port("ground")
    i = Param(unit="A", min=0)
    v_min = Param(0.5, unit="V", doc="below this it looks like a resistor")
    linear = False

    def __init__(self, name, **kw):
        super().__init__(name, **kw)
        self.inputs["i"] = float(self.i)

    def stamp(self, s):
        v = s.vd(self.p, self.n)
        i = self.inputs["i"]
        if v >= self.v_min:
            s.Inl(self.p, self.n, i, 0.0)
        else:
            s.G(self.p, self.n, i / self.v_min)

    def measure(self, s):
        v = s.vd(self.p, self.n)
        i = self.inputs["i"] * min(1.0, max(v, 0.0) / self.v_min)
        return {"v": v, "i": i, "p": v * i, "heat": v * i}


class ConstantPowerLoad(Component):
    """Draws a fixed power: what a load behind its own regulator looks like.

    The power is an input (`comp.inputs["p"]`), so a scenario can vary it.
    Below `v_knee` the load cannot hold its power and looks resistive, which
    is also what keeps i = P / v from running away as v falls.
    """
    p = Port("power_in")
    n = Port("ground")
    power = Param(unit="W", min=0)
    v_knee = Param(1.0, unit="V", min=1e-3)
    v_min = Param(None, unit="V", doc="minimum operating voltage")
    v_max = Param(None, unit="V", doc="maximum operating voltage")
    linear = False

    def __init__(self, name, **kw):
        super().__init__(name, **kw)
        self.inputs["p"] = float(self.power)
        if self.v_min is not None:
            self.limit("v", min="v_min", why="supply below operating range")
        if self.v_max is not None:
            self.limit("v", max="v_max", why="supply above operating range")

    def _iv(self, v):
        P = self.inputs["p"]
        if v >= self.v_knee:
            return P / v, -P / (v * v)
        g = P / (self.v_knee ** 2)
        return g * v, g

    def stamp(self, s):
        i, g = self._iv(s.vd(self.p, self.n))
        s.Inl(self.p, self.n, i, g)

    def measure(self, s):
        v = s.vd(self.p, self.n)
        i, _ = self._iv(v)
        return {"v": v, "i": i, "p": v * i, "heat": v * i}
