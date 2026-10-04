"""Design rules: is the circuit wired sensibly? No simulation involved.

A rule is a function that takes a Circuit and yields Findings. Register one
with `@rule`, and it runs on every design:

    @rule
    def no_long_names(circuit):
        for c in circuit:
            if len(c.name) > 20:
                yield Finding("warn", "naming", f"{c.name} is a long name")

or attach one to a single design with `circuit.rules.append(fn)`.
"""
from __future__ import annotations

from .constraints import Finding
from .core import ELECTRICAL, SIGNAL

RULES = []


def rule(fn):
    RULES.append(fn)
    return fn


def check(circuit):
    """Run every registered rule and every rule attached to the circuit."""
    out = []
    for fn in RULES + list(circuit.rules):
        out.extend(fn(circuit) or [])
    return out


# --------------------------------------------------------------------------
@rule
def unconnected_pins(circuit):
    for c in circuit:
        loose = [p.name for p in c.pins if p.net is None and not p.spec.optional]
        if loose:
            yield Finding("error", "unconnected pin",
                          f"{c.name}: {', '.join(loose)} not connected", c.name)


@rule
def single_pin_nets(circuit):
    for net in circuit.nets.values():
        if len(net.pins) == 1 and net.name != circuit.GND:
            yield Finding("warn", "net has one end",
                          f"net {net.name!r} only reaches {net.pins[0]}",
                          net.name)


@rule
def has_ground(circuit):
    g = circuit.nets.get(circuit.GND)
    if g is None or not g.pins:
        yield Finding("error", "no ground",
                      f"nothing is connected to {circuit.GND!r}; the solver "
                      "needs a reference")


@rule
def everything_reaches_ground(circuit):
    """Every solved net must be joined to ground through some component."""
    g = circuit.nets.get(circuit.GND)
    if g is None:
        return
    seen, todo = {g.name}, [g]
    while todo:
        net = todo.pop()
        for p in net.pins:
            for q in p.comp.pins:
                if (q.net is not None and q.net.name not in seen
                        and q.kind in ELECTRICAL):
                    seen.add(q.net.name)
                    todo.append(q.net)
    for net in circuit.nets.values():
        if net.pins and not net.is_signal and net.name not in seen:
            yield Finding("error", "floating",
                          f"net {net.name!r} has no path to ground", net.name)


@rule
def supply_conflicts(circuit):
    for net in circuit.nets.values():
        outs = [p for p in net.pins if p.kind == "power_out"]
        if len(outs) > 1:
            yield Finding("error", "supply conflict",
                          f"net {net.name!r} is driven by {len(outs)} supply "
                          f"outputs: {', '.join(map(repr, outs))}", net.name)
        ins = [p for p in net.pins if p.kind == "power_in"]
        if ins and not any(p.kind in ("power_out", "passive") for p in net.pins):
            yield Finding("error", "unpowered",
                          f"net {net.name!r} feeds {', '.join(map(repr, ins))} "
                          "but nothing supplies it", net.name)


@rule
def signal_drivers(circuit):
    for net in circuit.nets.values():
        ins = [p for p in net.pins if p.kind == "digital_in"]
        outs = [p for p in net.pins if p.kind == "digital_out"]
        bid = [p for p in net.pins if p.kind == "bidir"]
        if ins and not outs and not bid and net.is_signal:
            yield Finding("error", "undriven input",
                          f"{', '.join(map(repr, ins))} on net {net.name!r} "
                          "has nothing driving it", net.name)
        if len(outs) > 1:
            yield Finding("error", "output conflict",
                          f"net {net.name!r} has {len(outs)} push-pull "
                          f"outputs: {', '.join(map(repr, outs))}", net.name)


@rule
def logic_levels(circuit):
    """A driver must reach the receiver's threshold and not exceed its maximum."""
    for net in circuit.nets.values():
        drivers = [p for p in net.pins if p.kind in ("digital_out", "bidir")
                   and p.spec.voh is not None]
        for d in drivers:
            for r in net.pins:
                if r is d or r.kind not in SIGNAL:
                    continue
                if r.spec.vih is not None and d.spec.voh < r.spec.vih:
                    yield Finding(
                        "error", "logic level",
                        f"{d} drives {d.spec.voh:g} V but {r} needs "
                        f"{r.spec.vih:g} V to read a high", net.name)
                if (r.spec.v_abs_max is not None
                        and d.spec.voh > r.spec.v_abs_max):
                    yield Finding(
                        "error", "overvoltage on input",
                        f"{d} drives {d.spec.voh:g} V into {r}, rated "
                        f"{r.spec.v_abs_max:g} V", net.name)
