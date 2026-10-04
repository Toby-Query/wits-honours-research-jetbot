"""The smallest useful design: a cell, a switch and a lamp.

    python -m elecsim check examples/01_torch.py
"""
from elecsim import Circuit, Scenario, assumed, datasheet, final_at_least
from elecsim.lib import Cell, Resistor, Switch


def build():
    c = Circuit("torch")
    cell = c.add(Cell("cell", capacity_ah=datasheet(2.6, "cell datasheet"),
                      r0=assumed(0.06, "typical for this size; not measured")))
    sw = c.add(Switch("sw", r_on=0.03, i_max=1.0))
    lamp = c.add(Resistor("lamp", r=6.0, p_max=3.0))

    c.connect("BAT", cell.p, sw.a)
    c.connect("LAMP", sw.b, lamp.a)
    c.ground(cell.n, lamp.b)

    c.require("the lamp gets at least 2 W",
              lambda r: (r.min("lamp.heat") >= 2.0, f"min {r.min('lamp.heat'):.2f} W"))
    return c


SCENARIOS = [
    Scenario("ten minutes on", duration=600, dt=1.0,
             require=[final_at_least("cell.soc", 0.9)],
             metrics={"charge used": lambda r: f"{(1 - r.final('cell.soc')) * 100:.1f} %"}),
]
