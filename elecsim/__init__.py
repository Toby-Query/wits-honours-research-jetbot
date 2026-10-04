"""elecsim: describe a circuit in Python, constrain it, simulate it.

    from elecsim import Circuit, Simulation
    from elecsim.lib import Cell, Resistor

    c = Circuit("torch")
    cell = c.add(Cell("cell", capacity_ah=2.6, r0=0.06))
    lamp = c.add(Resistor("lamp", r=10, p_max=2.0))
    c.connect("V+", cell.p, lamp.a)
    c.ground(cell.n, lamp.b)

    res = Simulation(c, dt=1.0).run(600)
    print(res.final("cell.soc"), res.max("lamp.heat"))

See docs/FRAMEWORK.md.
"""
from . import analysis, erc, faults, lib
from .constraints import (Finding, Report, Requirement, at_least, at_most,
                          final_at_least, within)
from .core import (Circuit, Component, Limit, Net, Param, Pin, Port, Q,
                   Thermal, assumed, datasheet, measured)
from .design import Scenario, evaluate, load
from .erc import rule
from .lib.semis import diode_fit as junction_fit
from .lib.semis import diode_iv as junction_iv
from .solver import ConvergenceError, Result, Simulation, System

__version__ = "0.1.0"
__all__ = [
    "Circuit", "Component", "Limit", "Net", "Param", "Pin", "Port", "Q",
    "Thermal", "assumed", "datasheet", "measured", "Scenario", "evaluate",
    "load", "rule", "Simulation", "Result", "System", "ConvergenceError",
    "Requirement", "Report", "Finding", "at_least", "at_most", "within",
    "final_at_least", "junction_iv", "junction_fit", "lib", "erc", "faults", "analysis",
]
