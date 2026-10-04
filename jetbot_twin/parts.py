"""Parts that exist only on this robot.

These are ordinary `elecsim` components defined outside the framework, which
is the point: a design brings its own parts when the library does not have
them. `JetsonNano` is a worked example of a multi-pin nonlinear part.
"""
from __future__ import annotations

from elecsim import Component, Limit, Param, Port


class JetsonNano(Component):
    """The Jetson Nano developer kit as the expansion board sees it.

    Fed 5 V through the 40-pin header. Everything on the kit -- module,
    Wi-Fi card, camera, fan -- is behind its own regulators, so the kit is a
    constant-power load whatever the rail does (within reason). It also
    hands 3.3 V back out of the header, which is what powers the expansion
    board's logic; that current is drawn from the 5 V rail too.

    `inputs["p"]` is the power the kit is using, in watts. Set it from a
    scenario, or from the real robot's own power monitor.
    """
    v5 = Port("power_in", v_abs_max=5.5)
    gnd = Port("ground")
    v3v3 = Port("power_out")
    sda = Port("bidir", voh=3.3, v_abs_max=3.6)
    scl = Port("bidir", voh=3.3, v_abs_max=3.6)

    power = Param(unit="W", min=0, doc="initial power draw")
    v_min = Param(4.75, unit="V")
    v_max = Param(5.25, unit="V")
    v_off = Param(4.0, unit="V", doc="below this it has browned out")
    i_header_max = Param(5.0, unit="A", doc="what the header pins can carry")
    eff_3v3 = Param(0.9, doc="efficiency of the carrier's 3.3 V regulator")
    v_knee = Param(3.0, unit="V")
    branches = 1
    linear = False

    limits = (
        Limit("v", min="v_min", for_s=0.002,
              why="5 V rail below the Jetson's tolerance: throttling, then reset"),
        Limit("v", max="v_max", why="5 V rail above the Jetson's tolerance"),
        Limit("i", max="i_header_max", why="header pin current"),
    )

    def __init__(self, name, **kw):
        super().__init__(name, **kw)
        self.inputs["p"] = float(self.power)
        self.browned_out = False
        self.on = True                    # is its own 3.3 V regulator up?

    def pre_step(self, t, dt):
        v = self.sig.get("v")
        if v is None:
            return
        if v < self.v_off:
            self.browned_out = True
            self.on = False
        elif v >= self.v_min:
            self.on = True

    def refresh(self):
        self.inputs["p"] = float(self.power)

    def relax(self):
        if not self.on:
            return False
        self.on = False
        return True

    def stamp(self, s):
        A, b = s.A, s.b
        br = s.row(self)
        n5, ng, n3 = self.v5.node, self.gnd.node, self.v3v3.node
        j = s.x[br]                               # current out of the 3V3 pin
        # 3.3 V output
        if n3 >= 0:
            A[n3, br] -= 1.0
            A[br, n3] += 1.0
        if ng >= 0:
            A[ng, br] += 1.0
            A[br, ng] -= 1.0
        b[br] += 3.3 if self.on else 0.0
        # 5 V input: (P + 3.3 j / eff) / v
        v = s.vd(self.v5, self.gnd)
        P = self.inputs["p"] + (3.3 * j / self.eff_3v3 if self.on else 0.0)
        if v >= self.v_knee:
            i, dv, dj = P / v, -P / (v * v), 3.3 / (self.eff_3v3 * v)
        else:
            g = 1.0 / (self.v_knee ** 2)
            i, dv, dj = P * g * v, P * g, 3.3 / self.eff_3v3 * g * v
        if not self.on:
            dj = 0.0
        const = i - dv * v - dj * j
        for row, sgn in ((n5, 1.0), (ng, -1.0)):
            if row < 0:
                continue
            if n5 >= 0:
                A[row, n5] += sgn * dv
            if ng >= 0:
                A[row, ng] -= sgn * dv
            A[row, br] += sgn * dj
            b[row] -= sgn * const

    def measure(self, s):
        v = s.vd(self.v5, self.gnd)
        j = s.i(self)
        P = self.inputs["p"] + (3.3 * j / self.eff_3v3 if self.on else 0.0)
        i = P / v if v >= self.v_knee else P * v / self.v_knee ** 2
        if v < self.v_off:
            self.browned_out = True
        return {"v": v, "i": i, "p": v * i, "i_3v3": j,
                "browned_out": float(self.browned_out),
                "heat": v * i - (3.3 * j if self.on else 0.0)}
