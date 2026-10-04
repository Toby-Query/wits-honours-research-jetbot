"""Measurement ICs: what the firmware would actually read.

A twin is compared against a real robot through that robot's own sensors,
so the twin needs those sensors too -- with their resolution and their
limits -- or the comparison is between two different quantities.
"""
from __future__ import annotations

from ..core import Component, Limit, Param, Port


def quantise(x, lsb, lo, hi):
    return min(max(round(x / lsb) * lsb, lo), hi)


class ShuntMonitor(Component):
    """A high-side current/voltage monitor: the INA219.

    Reads the drop across an external shunt (in_p - in_n) and the bus
    voltage at in_n. Draws nothing from the nodes it senses.
    """
    in_p = Port("analog_in", v_abs_max=26.0)
    in_n = Port("analog_in", v_abs_max=26.0)
    vs = Port("power_in", v_abs_max=6.0)
    gnd = Port("ground")
    sda = Port("bidir", voh=3.3, optional=True)
    scl = Port("bidir", voh=3.3, optional=True)

    r_shunt = Param(unit="ohm", min=1e-6, doc="the external shunt it reads across")
    shunt_range = Param(0.32, unit="V", doc="PGA full scale (+/-)")
    shunt_lsb = Param(10e-6, unit="V")
    bus_lsb = Param(4e-3, unit="V")
    bus_range = Param(32.0, unit="V")
    i_supply = Param(1e-3, unit="A")
    address = Param(0x40)

    limits = (Limit("v_shunt", max="shunt_range", absolute=True, severity="warn",
                    why="shunt voltage beyond the ADC range: reading clips"),)

    def stamp(self, s):
        s.I(self.vs, self.gnd, self.i_supply)

    def measure(self, s):
        vsh = s.vd(self.in_p, self.in_n)
        bus = s.vd(self.in_n, self.gnd)
        vq = quantise(vsh, self.shunt_lsb, -self.shunt_range, self.shunt_range)
        bq = quantise(bus, self.bus_lsb, 0.0, self.bus_range)
        return {"v_shunt": vsh, "bus_v": bq, "current": vq / self.r_shunt,
                "power": bq * vq / self.r_shunt,
                "heat": s.vd(self.vs, self.gnd) * self.i_supply}


class Adc(Component):
    """A single-ended ADC channel behind an optional divider (ADS1115-like)."""
    ain = Port("analog_in")
    vdd = Port("power_in")
    gnd = Port("ground")
    full_scale = Param(4.096, unit="V")
    bits = Param(15, doc="resolution for a single-ended reading")
    divider = Param(1.0, doc="what the input was divided by before the pin")
    i_supply = Param(0.2e-3, unit="A")

    def stamp(self, s):
        s.I(self.vdd, self.gnd, self.i_supply)

    def measure(self, s):
        v = s.vd(self.ain, self.gnd)
        lsb = self.full_scale / (2 ** int(self.bits))
        q = quantise(v, lsb, 0.0, self.full_scale)
        return {"v_pin": v, "reading": q * self.divider,
                "clipped": float(v > self.full_scale)}
