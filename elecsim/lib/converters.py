"""Switching regulators, as averaged behavioural models."""
from __future__ import annotations

from ..core import Component, Limit, Param, Port


class BuckConverter(Component):
    """A synchronous buck regulator, averaged over its switching period.

    The output is a regulated voltage; the input draws whatever power the
    output delivers plus the converter's own losses:

        P_loss = Vin Iq  +  Iout^2 (Rds + DCR)  +  1/2 Vin Iout t_sw f_sw

    so efficiency falls out of the part's numbers rather than being typed in.
    It leaves regulation the way the real part does: below `uvlo` it is off,
    when Vin is too close to Vout it is in dropout and the output follows the
    input, and above `i_limit` the output current is clamped and the voltage
    collapses.
    """
    vin = Port("power_in")
    gnd = Port("ground")
    out = Port("power_out")

    v_set = Param(unit="V", min=0)
    i_limit = Param(unit="A", min=0, doc="output current limit")
    i_rated = Param(None, unit="A", doc="rated continuous output current")
    rds_on = Param(0.1, unit="ohm", min=0, doc="switch resistance, duty-averaged")
    dcr = Param(0.03, unit="ohm", min=0, doc="inductor resistance")
    iq = Param(1e-3, unit="A", min=0, doc="quiescent current")
    f_sw = Param(340e3, unit="Hz")
    t_sw = Param(40e-9, unit="s", doc="rise + fall time of the switch node")
    d_max = Param(0.95, doc="maximum duty cycle")
    uvlo_on = Param(4.3, unit="V")
    uvlo_off = Param(4.0, unit="V")
    v_in_max = Param(None, unit="V")
    branches = 1
    linear = False

    def __init__(self, name, **kw):
        super().__init__(name, **kw)
        self.mode = "reg"            # off | reg | dropout | limit
        if self.i_rated is not None:
            self.limit("i_out", max="i_rated", why="rated output current")
        if self.v_in_max is not None:
            self.limit("v_in", max="v_in_max", why="maximum input voltage")

    @property
    def r_path(self):
        return self.rds_on + self.dcr

    def relax(self):
        if self.mode == "off":
            return False
        self.mode = "off"
        self._hold = 5                 # stay down a few steps before retrying
        return True

    def _loss(self, vin, io):
        return (vin * self.iq + io * io * self.r_path
                + 0.5 * vin * abs(io) * self.t_sw * self.f_sw)

    def pre_step(self, t, dt):
        vin = self.sig.get("v_in")
        if vin is None:                           # before the first solve
            return
        io, vo = self.sig["i_out"], self.sig["v_out"]
        m = self.mode
        if m == "off":
            self._hold = max(0, getattr(self, "_hold", 0) - 1)
            if vin >= self.uvlo_on and not self._hold:
                m = "reg"
        elif vin < self.uvlo_off:
            m = "off"
        elif m == "reg":
            if io > self.i_limit:
                m = "limit"
            elif self.d_max * vin - io * self.r_path < self.v_set:
                m = "dropout"
        elif m == "dropout":
            if self.d_max * vin - io * self.r_path > self.v_set * 1.005:
                m = "reg"
            elif io > self.i_limit:
                m = "limit"
        elif m == "limit":
            if vo >= self.v_set:
                m = "reg"
        self.mode = m

    def stamp(self, s):
        br = s.row(self)
        A, b = s.A, s.b
        vi, vo, g = self.vin.node, self.out.node, self.gnd.node
        vin = s.vd(self.vin, self.gnd)
        io = s.x[br]
        # the unknown is the current flowing OUT of `out` into the load
        if vo >= 0:
            A[vo, br] -= 1.0
        if g >= 0:
            A[g, br] += 1.0
        m = self.mode
        if m == "off":
            A[br, br] += 1.0                      # i_out = 0
            return
        if m == "limit":
            A[br, br] += 1.0
            b[br] += self.i_limit                 # i_out = i_limit
        elif m == "dropout":
            # v_out = d_max v_in - r i_out
            if vo >= 0:
                A[br, vo] += 1.0
            if g >= 0:
                A[br, g] -= 1.0 - self.d_max
            if vi >= 0:
                A[br, vi] -= self.d_max
            A[br, br] += self.r_path
        else:
            # v_out - v_gnd = v_set
            if vo >= 0:
                A[br, vo] += 1.0
            if g >= 0:
                A[br, g] -= 1.0
            b[br] += self.v_set
        # input side: i_in(vin, io) = (v_out i_out + loss) / vin
        vout = s.vd(self.out, self.gnd)
        vin_c = max(vin, 0.5)
        pin = vout * io + self._loss(vin_c, io)
        i_in = pin / vin_c
        d_vin = (-pin / vin_c ** 2 + (self.iq + 0.5 * abs(io) * self.t_sw
                                      * self.f_sw) / vin_c) if vin > 0.5 else 0.0
        d_io = (vout + 2 * io * self.r_path
                + 0.5 * vin_c * self.t_sw * self.f_sw * (1 if io >= 0 else -1)) / vin_c
        d_vo = io / vin_c
        const = i_in - d_vin * vin - d_io * io - d_vo * vout
        for row, sgn in ((vi, 1.0), (g, -1.0)):
            if row < 0:
                continue
            if vi >= 0:
                A[row, vi] += sgn * d_vin
            if g >= 0:
                A[row, g] -= sgn * (d_vin + d_vo)
            if vo >= 0:
                A[row, vo] += sgn * d_vo
            A[row, br] += sgn * d_io
            b[row] -= sgn * const

    def measure(self, s):
        vin = s.vd(self.vin, self.gnd)
        vout = s.vd(self.out, self.gnd)
        io = s.i(self)
        if self.mode == "off":
            return {"v_in": vin, "v_out": vout, "i_out": 0.0, "i_in": 0.0,
                    "heat": 0.0, "eff": 0.0, "regulating": 0.0}
        loss = self._loss(max(vin, 0.5), io)
        pout = vout * io
        return {"v_in": vin, "v_out": vout, "i_out": io,
                "i_in": (pout + loss) / max(vin, 0.5), "heat": loss,
                "eff": pout / (pout + loss) if pout + loss > 0 else 0.0,
                "regulating": float(self.mode == "reg")}


class LinearRegulator(Component):
    """An LDO: output held at v_set, input current equals output current."""
    vin = Port("power_in")
    gnd = Port("ground")
    out = Port("power_out")
    v_set = Param(unit="V")
    dropout = Param(0.3, unit="V")
    iq = Param(1e-3, unit="A")
    i_max = Param(None, unit="A")
    branches = 1
    linear = False

    def __init__(self, name, **kw):
        super().__init__(name, **kw)
        self._drop = False
        if self.i_max is not None:
            self.limit("i_out", max="i_max", why="regulator output current")

    def pre_step(self, t, dt):
        vin = self.sig.get("v_in")
        if vin is not None:
            self._drop = vin - self.dropout < self.v_set

    def stamp(self, s):
        br = s.row(self)
        A = s.A
        vi, vo, g = self.vin.node, self.out.node, self.gnd.node
        if vo >= 0:
            A[vo, br] -= 1.0
            A[br, vo] += 1.0
        if vi >= 0:
            A[vi, br] += 1.0
        if self._drop:                    # v_out = v_in - dropout
            if vi >= 0:
                A[br, vi] -= 1.0
            s.b[br] += -self.dropout
        else:
            if g >= 0:
                A[br, g] -= 1.0
            s.b[br] += self.v_set
        s.I(self.vin, self.gnd, self.iq)

    def measure(self, s):
        vin, vout = s.vd(self.vin, self.gnd), s.vd(self.out, self.gnd)
        io = s.i(self)
        return {"v_in": vin, "v_out": vout, "i_out": io, "i_in": io + self.iq,
                "heat": (vin - vout) * io + vin * self.iq}
