"""Break things on purpose and see what the design does about it.

    report = fmea(build, scenario)        # every single fault, one at a time
    print(report.text())

A fault is a function that takes a freshly built circuit and damages it.
The three constructors here cover most of what goes wrong on a bench.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .core import ELECTRICAL
from .solver import Simulation


@dataclass
class Fault:
    name: str
    apply: callable

    def __call__(self, circuit):
        self.apply(circuit)


def open_circuit(comp_name):
    """The component fails open (or is unplugged)."""
    def f(c):
        c[comp_name].fault = "open"
    return Fault(f"{comp_name} open", f)


def short_circuit(comp_name):
    def f(c):
        c[comp_name].fault = "short"
    return Fault(f"{comp_name} short", f)


def drift(comp_name, param, factor):
    """A parameter is off by a factor: an aged cell, a worn contact."""
    def f(c):
        comp = c[comp_name]
        setattr(comp, param, getattr(comp, param) * factor)
    return Fault(f"{comp_name}.{param} x{factor:g}", f)


def set_input(comp_name, key, value):
    def f(c):
        c[comp_name].inputs[key] = value
    return Fault(f"{comp_name}.{key} = {value}", f)


def enumerate_faults(circuit):
    """Every open/short the components say they can suffer."""
    out = []
    for c in circuit:
        for mode in c.fault_modes:
            out.append(open_circuit(c.name) if mode == "open"
                       else short_circuit(c.name))
    return out


@dataclass
class FmeaRow:
    fault: str
    ran: bool
    failed_requirements: list = field(default_factory=list)
    violated_limits: list = field(default_factory=list)
    error: str = ""

    @property
    def effect(self):
        if not self.ran:
            return "simulation failed: " + self.error
        if not self.failed_requirements and not self.violated_limits:
            return "no effect on any requirement or rating"
        bits = []
        if self.failed_requirements:
            bits.append("fails: " + "; ".join(self.failed_requirements))
        if self.violated_limits:
            bits.append("over-stresses: " + ", ".join(self.violated_limits[:4])
                        + (" ..." if len(self.violated_limits) > 4 else ""))
        return " | ".join(bits)


@dataclass
class Fmea:
    scenario: str
    rows: list

    def text(self):
        w = max(len(r.fault) for r in self.rows) if self.rows else 10
        L = [f"Single-fault analysis, scenario: {self.scenario}", ""]
        for r in self.rows:
            L.append(f"  {r.fault:<{w}}  {r.effect}")
        benign = sum(1 for r in self.rows if r.ran and not r.failed_requirements
                     and not r.violated_limits)
        L += ["", f"{len(self.rows)} faults, {benign} tolerated"]
        return "\n".join(L)


def fmea(build, scenario, faults=None):
    """Apply each fault alone, run the scenario, record what breaks."""
    if faults is None:
        faults = enumerate_faults(build(**scenario.build_args))
    rows = []
    for fault in faults:
        c = build(**scenario.build_args)
        if scenario.setup:
            scenario.setup(c)
        fault(c)
        row = FmeaRow(fault.name, True)
        try:
            res = Simulation(c, dt=scenario.dt).run(
                scenario.duration, stimulus=scenario.stimulus,
                every=scenario.every)
        except Exception as e:
            row.ran, row.error = False, f"{type(e).__name__}: {e}"
            rows.append(row)
            continue
        for r in list(c.requirements) + list(scenario.require):
            ok, _ = r.evaluate(res)
            if not ok:
                row.failed_requirements.append(r.name)
        row.violated_limits = [l.label for l in res.limits
                               if l.violated and l.severity == "error"]
        rows.append(row)
    return Fmea(scenario.name, rows)
