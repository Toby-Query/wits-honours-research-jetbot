"""Defining a part, a rating, a requirement and a design rule of your own.

    python -m elecsim check examples/02_your_own_part.py -v

A part is a class: declare its pins and numbers, then say how it behaves by
adding its equations in `stamp`. Here: an LED with a forward voltage that
depends on current, and a thermistor-style PTC resettable fuse.
"""
from elecsim import (Circuit, Component, Finding, Limit, Param, Port, Scenario,
                     Thermal, datasheet, junction_fit, junction_iv, rule, within)
from elecsim.lib import Resistor, VoltageSource


class Led(Component):
    """An LED: a diode with a high knee, and a brightness you can constrain."""
    a = Port(doc="anode")
    k = Port(doc="cathode")
    vf = Param(2.0, unit="V", doc="forward voltage at i_test")
    i_test = Param(0.020, unit="A")
    i_max = Param(0.030, unit="A", doc="absolute maximum forward current")
    n = Param(2.0, doc="ideality factor")
    linear = False                       # its equations depend on the solution

    # Ratings are declared with the part, not with the design that uses it.
    limits = (Limit("i", max="i_max", why="LED forward current"),)

    def _iv(self, v):
        # junction_iv is the framework's exponential: the Shockley equation,
        # continued as a straight line where exp() would overflow. Use it
        # rather than writing math.exp yourself and the solver will cope
        # with whatever first guess it is handed.
        i_s = junction_fit(self.vf, self.i_test, self.n)
        return junction_iv(v, i_s, self.n)

    def stamp(self, s):
        v = s.vd(self.a, self.k)
        i, g = self._iv(v)
        s.Inl(self.a, self.k, i, g)      # nonlinear branch: value and slope

    def measure(self, s):
        v = s.vd(self.a, self.k)
        i, _ = self._iv(v)
        return {"v": v, "i": i, "heat": v * i,
                "brightness": i / self.i_test}       # any signal you like


class PolyFuse(Component):
    """A resettable fuse: its resistance jumps when it gets hot.

    State that persists between steps lives on the instance; `update` is
    called once per accepted step to advance it.
    """
    a = Port()
    b = Port()
    r_cold = Param(0.5, unit="ohm")
    r_hot = Param(500.0, unit="ohm")
    i_hold = Param(0.05, unit="A", doc="carries this indefinitely")
    t_trip = Param(0.2, unit="s", doc="time to trip at twice i_hold")

    def __init__(self, name, **kw):
        super().__init__(name, **kw)
        self.heat = 0.0
        self.tripped = False

    def stamp(self, s):
        s.G(self.a, self.b, 1.0 / (self.r_hot if self.tripped else self.r_cold))

    def update(self, s, dt):
        i = abs(s.vd(self.a, self.b)) / (self.r_hot if self.tripped else self.r_cold)
        over = (i / self.i_hold) ** 2 - 1.0
        self.heat = max(0.0, self.heat + over * dt / (3 * self.t_trip))
        if not self.tripped and self.heat >= 1.0:
            self.tripped = True
        elif self.tripped and i < 0.02 * self.i_hold:
            self.tripped, self.heat = False, 0.0

    def measure(self, s):
        v = s.vd(self.a, self.b)
        i = v / (self.r_hot if self.tripped else self.r_cold)
        return {"v": v, "i": i, "heat": v * i, "tripped": float(self.tripped)}


# A design rule of your own: runs on every design, before any simulation.
@rule
def every_led_has_a_series_resistor(circuit):
    for c in circuit:
        if isinstance(c, Led):
            others = [p.comp for p in c.a.net.pins if p.comp is not c] if c.a.net else []
            if not any(isinstance(o, (Resistor, PolyFuse)) for o in others):
                yield Finding("error", "LED without a resistor",
                              f"{c.name} is fed straight from "
                              f"{c.a.net.name if c.a.net else 'nothing'}", c.name)


def build(r_series=150.0):
    c = Circuit("indicator")
    src = c.add(VoltageSource("usb", v=datasheet(5.0, "USB"), r=0.2))
    fuse = c.add(PolyFuse("f1"))
    r = c.add(Resistor("r1", r=r_series, p_max=0.125,
                       thermal=Thermal(rth=400, cth=0.02, t_max=125)))
    led = c.add(Led("d1"))
    c.connect("VBUS", src.p, fuse.a)
    c.connect("FUSED", fuse.b, r.a)
    c.connect("LED_A", r.b, led.a)
    c.ground(src.n, led.k)
    c.require("the LED is visibly on", lambda r_: r_.final("d1.brightness") > 0.5)
    return c


def short_the_resistor(c):
    c["r1"].fault = "short"


SCENARIOS = [
    Scenario("normal", duration=1.0,
             require=[within("d1.i", 0.0, 0.025, "LED kept under 25 mA")]),
    Scenario("too small a resistor", duration=1.0, build_args={"r_series": 68.0},
             doc="68 ohm instead of 150: the LED's own rating should object."),
    Scenario("resistor shorted", duration=2.0, setup=short_the_resistor,
             doc="The fuse trips -- but look at what the LED saw first.",
             metrics={"fuse tripped at": lambda r: f"{r.t[r['f1.tripped'] > 0][0]:.2f} s"
                      if r.max("f1.tripped") else "never"}),
]
