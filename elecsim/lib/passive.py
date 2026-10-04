"""Resistors, capacitors, inductors, wires, switches, fuses."""
from __future__ import annotations

from ..core import Component, Limit, Param, Port

RHO_CU = 1.724e-8           # ohm.m at 20 C
AWG_MM2 = {10: 5.26, 12: 3.31, 14: 2.08, 16: 1.31, 18: 0.823, 20: 0.518,
           22: 0.326, 24: 0.205, 26: 0.129, 28: 0.081, 30: 0.0509}


class Resistor(Component):
    """A resistance, with an optional power rating."""
    a = Port()
    b = Port()
    r = Param(unit="ohm", min=1e-9, doc="resistance")
    p_max = Param(None, unit="W", doc="rated dissipation")
    fault_modes = ("open", "short")

    def __init__(self, name, **kw):
        super().__init__(name, **kw)
        if self.p_max is not None:
            self.limit("heat", max="p_max", why="resistor power rating")

    def stamp(self, s):
        s.G(self.a, self.b, 1.0 / self.r)

    def measure(self, s):
        v = s.vd(self.a, self.b)
        i = v / self.r
        return {"v": v, "i": i, "heat": v * i}


class Wire(Resistor):
    """A length of copper. Give it a length and a gauge, not a resistance."""
    r = Param(None, unit="ohm", doc="derived from length and gauge")
    length = Param(unit="m", min=0)
    awg = Param(22, doc="American wire gauge")
    i_max = Param(None, unit="A", doc="ampacity")

    def __init__(self, name, **kw):
        super().__init__(name, **kw)
        self.r = max(RHO_CU * self.length / (AWG_MM2[self.awg] * 1e-6), 1e-6)
        if self.i_max is not None:
            self.limit("i", max="i_max", absolute=True, why="wire ampacity")


class Capacitor(Component):
    """A capacitance with series resistance and an optional voltage rating."""
    a = Port()
    b = Port()
    c = Param(unit="F", min=0)
    esr = Param(0.0, unit="ohm", min=0)
    v_max = Param(None, unit="V", doc="rated voltage")
    ic = Param(None, unit="V", doc="initial voltage; None takes the DC point")
    fault_modes = ("short",)

    def __init__(self, name, **kw):
        super().__init__(name, **kw)
        self.vc = self.ic or 0.0
        if self.v_max is not None:
            self.limit("v", max="v_max", absolute=True,
                       why="capacitor voltage rating")

    def stamp(self, s):
        if s.dc:
            if self.ic is not None:              # hold the stated voltage
                g = 1e3
                s.G(self.a, self.b, g)
                s.I(self.b, self.a, g * self.ic)
            return
        g = 1.0 / (self.esr + s.dt / self.c)
        s.G(self.a, self.b, g)
        s.I(self.b, self.a, g * self.vc)

    def init_state(self, s):
        if self.ic is None:
            self.vc = s.vd(self.a, self.b)

    def update(self, s, dt):
        g = 1.0 / (self.esr + dt / self.c)
        self._i = g * (s.vd(self.a, self.b) - self.vc)
        self.vc += self._i * dt / self.c

    def measure(self, s):
        i = getattr(self, "_i", 0.0)
        return {"v": self.vc, "i": i, "heat": i * i * self.esr,
                "energy": 0.5 * self.c * self.vc ** 2}


class Inductor(Component):
    """An inductance with winding resistance and an optional saturation current."""
    a = Port()
    b = Port()
    l = Param(unit="H", min=0)
    dcr = Param(0.0, unit="ohm", min=0)
    i_sat = Param(None, unit="A", doc="saturation current")
    branches = 1
    fault_modes = ("open",)

    def __init__(self, name, **kw):
        super().__init__(name, **kw)
        self.il = 0.0
        if self.i_sat is not None:
            self.limit("i", max="i_sat", absolute=True, why="inductor saturation")

    def stamp(self, s):
        if s.dc:
            s.V(self, self.a, self.b, 0.0, r=self.dcr)
        else:
            k = self.l / s.dt
            s.V(self, self.a, self.b, -k * self.il, r=self.dcr + k)

    def init_state(self, s):
        self.il = s.i(self)

    def update(self, s, dt):
        self.il = s.i(self)

    def measure(self, s):
        i = s.i(self)
        return {"i": i, "v": s.vd(self.a, self.b), "heat": i * i * self.dcr,
                "energy": 0.5 * self.l * i * i}


class Switch(Component):
    """A contact. `closed` is an input: set comp.inputs["closed"]."""
    a = Port()
    b = Port()
    r_on = Param(0.02, unit="ohm", min=1e-9, doc="contact resistance")
    r_off = Param(1e9, unit="ohm")
    i_max = Param(None, unit="A", doc="rated contact current")
    closed = Param(True, doc="initial position")
    fault_modes = ("open", "short")

    def __init__(self, name, **kw):
        super().__init__(name, **kw)
        self.inputs["closed"] = bool(self.closed)
        if self.i_max is not None:
            self.limit("i", max="i_max", absolute=True,
                       why="switch contact rating")

    def _r(self):
        return self.r_on if self.inputs["closed"] else self.r_off

    def stamp(self, s):
        s.G(self.a, self.b, 1.0 / self._r())

    def measure(self, s):
        v = s.vd(self.a, self.b)
        i = v / self._r()
        return {"v": v, "i": i, "heat": v * i,
                "closed": float(self.inputs["closed"])}


class Fuse(Component):
    """Opens when its element has absorbed its melting i2t.

    Below the rated current the heat leaks away; above it, it accumulates.
    """
    a = Port()
    b = Port()
    i_rating = Param(unit="A", min=0)
    r = Param(0.01, unit="ohm", min=1e-9)
    i2t = Param(None, unit="A2s", doc="melting integral; default (2 I)^2 * 0.1 s")

    def __init__(self, name, **kw):
        super().__init__(name, **kw)
        self.heat_i2t = 0.0
        self.blown = False

    def stamp(self, s):
        s.G(self.a, self.b, 1e-9 if self.blown else 1.0 / self.r)

    def update(self, s, dt):
        if self.blown:
            return
        i = s.vd(self.a, self.b) / self.r
        melt = self.i2t if self.i2t is not None else (2 * self.i_rating) ** 2 * 0.1
        self.heat_i2t = max(0.0, self.heat_i2t
                            + (i * i - self.i_rating ** 2) * dt)
        if self.heat_i2t >= melt:
            self.blown = True

    def measure(self, s):
        v = s.vd(self.a, self.b)
        i = 0.0 if self.blown else v / self.r
        return {"v": v, "i": i, "heat": v * i, "blown": float(self.blown)}
