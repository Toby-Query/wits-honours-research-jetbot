"""Scenarios, and running a whole design through every check.

A design is a Python module with a `build()` function that returns a Circuit
and (optionally) a `SCENARIOS` list. `evaluate` builds a fresh circuit for
each scenario, so one scenario cannot leave state behind for the next.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import erc
from .constraints import Report, ScenarioReport
from .solver import Simulation


@dataclass
class Scenario:
    """A situation to put the design in.

    setup       fn(circuit), once before the run: set parameters and inputs
    stimulus    fn(t, circuit), every step: what changes over time
    require     extra Requirements that only make sense in this scenario
    metrics     {label: fn(result) -> value}, printed in the report
    """
    name: str
    duration: float
    dt: float = 1e-3
    setup: Optional[Callable] = None
    stimulus: Optional[Callable] = None
    require: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    record: Optional[list] = None
    every: int = 1
    doc: str = ""
    build_args: dict = field(default_factory=dict)

    def run(self, build):
        circuit = build(**self.build_args)
        if self.setup:
            self.setup(circuit)
        sim = Simulation(circuit, dt=self.dt)
        res = sim.run(self.duration, stimulus=self.stimulus,
                      record=self.record, every=self.every, name=self.name)
        return circuit, res


def evaluate(build, scenarios=(), name=None):
    """Rule-check a design and run it through its scenarios. Returns a Report."""
    circuit = build()
    rep = Report(name or circuit.name)
    rep.findings = erc.check(circuit)
    rep.assumptions = circuit.assumptions()
    for sc in scenarios:
        sr = ScenarioReport(sc.name, sc.doc)
        try:
            c, res = sc.run(build)
        except Exception as e:
            sr.error = f"{type(e).__name__}: {e}"
            rep.scenarios.append(sr)
            continue
        sr.limits = res.limits
        for r in list(c.requirements) + list(sc.require):
            ok, detail = r.evaluate(res)
            sr.requirements.append((r, ok, detail))
        for label, fn in sc.metrics.items():
            try:
                v = fn(res)
                sr.metrics[label] = f"{v:.4g}" if isinstance(v, float) else str(v)
            except Exception as e:
                sr.metrics[label] = f"(error: {e})"
        rep.scenarios.append(sr)
    return rep


def load(target):
    """Import a design from a file path or a dotted module name."""
    p = Path(target)
    if p.suffix == ".py" and p.exists():
        spec = importlib.util.spec_from_file_location(p.stem, p)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[p.stem] = mod
        spec.loader.exec_module(mod)
    else:
        mod = importlib.import_module(target)
    if not hasattr(mod, "build"):
        raise AttributeError(f"{target} has no build() function")
    return mod
