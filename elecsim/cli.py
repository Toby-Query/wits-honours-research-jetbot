"""Command line.

    python -m elecsim check  DESIGN [-s NAME] [-v] [--json FILE]
    python -m elecsim fmea   DESIGN -s NAME
    python -m elecsim parts  [NAME]
    python -m elecsim show   DESIGN
    python -m elecsim new    NAME

DESIGN is a path to a .py file or a dotted module name; it must define
build() and may define SCENARIOS.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import lib
from .core import Component
from .design import evaluate, load
from .faults import fmea

TEMPLATE = '''"""{name}: describe what this circuit is for."""
from elecsim import Circuit, Scenario, assumed, datasheet, within
from elecsim.lib import Cell, ConstantPowerLoad, LinearRegulator, Resistor


def build():
    c = Circuit("{name}")

    cell = c.add(Cell("cell", capacity_ah=datasheet(2.6, "cell datasheet"),
                      r0=assumed(0.06, "typical; measure it")))
    wire = c.add(Resistor("wire", r=0.05))
    reg = c.add(LinearRegulator("reg", v_set=3.3, i_max=0.5))
    load = c.add(ConstantPowerLoad("load", power=0.5, v_min=3.0))

    c.connect("BAT", cell.p, wire.a)
    c.connect("VIN", wire.b, reg.vin)
    c.connect("3V3", reg.out, load.p)
    c.ground(cell.n, reg.gnd, load.n)

    c.require("the load always has its 3.3 V",
              lambda r: r.min("load.v") > 3.2)
    return c


def heavy(t, c):
    c["load"].inputs["p"] = 1.2 if t > 0.5 else 0.5


SCENARIOS = [
    Scenario("steady", duration=1.0),
    Scenario("load step", duration=1.0, stimulus=heavy,
             require=[within("reg.v_out", 3.25, 3.35)]),
]
'''


def _all_parts():
    out = {}
    for n in dir(lib):
        o = getattr(lib, n)
        if isinstance(o, type) and issubclass(o, Component) and o is not Component:
            out[n] = o
    return out


def cmd_parts(args):
    parts = _all_parts()
    if not args.name:
        for n, o in sorted(parts.items()):
            doc = ((o.__doc__ or "").strip().splitlines() or [""])[0]
            print(f"  {n:<20} {doc}")
        print("\n  python -m elecsim parts NAME   for one part's pins and parameters")
        return 0
    o = parts.get(args.name)
    if o is None:
        print(f"no part called {args.name}; try: {', '.join(sorted(parts))}")
        return 1
    print(f"{args.name}\n{'=' * len(args.name)}\n{(o.__doc__ or '').strip()}\n")
    print("pins")
    for k, p in o._ports.items():
        print(f"  {k:<10} {p.kind:<12}" + (" (optional)" if p.optional else "")
              + (f"  {p.doc}" if p.doc else ""))
    print("\nparameters")
    for k, p in o._params.items():
        from .core import REQUIRED
        d = "required" if p.default is REQUIRED else f"default {p.default!r}"
        print(f"  {k:<14} {p.unit:<10} {d:<22} {p.doc}")
    return 0


def cmd_check(args):
    mod = load(args.design)
    scenarios = list(getattr(mod, "SCENARIOS", []))
    if args.scenario:
        scenarios = [s for s in scenarios if s.name in args.scenario]
        if not scenarios:
            print("no such scenario; this design has: "
                  + ", ".join(s.name for s in getattr(mod, "SCENARIOS", [])))
            return 2
    rep = evaluate(mod.build, scenarios)
    print(rep.text(verbose=args.verbose))
    if args.json:
        rep.to_json(args.json)
        print(f"\nwrote {args.json}")
    return min(rep.failures, 100)


def cmd_fmea(args):
    mod = load(args.design)
    scs = {s.name: s for s in getattr(mod, "SCENARIOS", [])}
    if args.scenario not in scs:
        print("pick a scenario with -s; this design has: " + ", ".join(scs))
        return 2
    print(fmea(mod.build, scs[args.scenario]).text())
    return 0


def cmd_show(args):
    mod = load(args.design)
    c = mod.build()
    print(c.summary(), "\n")
    for comp in c:
        print(f"{comp.name:<14} {type(comp).__name__}"
              + (f"   -- {comp.note}" if comp.note else ""))
        for p in comp.pins:
            print(f"    {p.name:<8} -> {p.net.name if p.net else '(unconnected)'}")
    return 0


def cmd_new(args):
    path = Path(args.name if args.name.endswith(".py") else args.name + ".py")
    if path.exists():
        print(f"{path} already exists")
        return 1
    path.write_text(TEMPLATE.format(name=path.stem))
    print(f"wrote {path}\n  python -m elecsim check {path}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="elecsim", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check", help="rule-check and simulate a design")
    p.add_argument("design")
    p.add_argument("-s", "--scenario", action="append")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--json")
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("fmea", help="apply every single fault in turn")
    p.add_argument("design")
    p.add_argument("-s", "--scenario", required=True)
    p.set_defaults(fn=cmd_fmea)
    p = sub.add_parser("parts", help="list the component library")
    p.add_argument("name", nargs="?")
    p.set_defaults(fn=cmd_parts)
    p = sub.add_parser("show", help="print a design's components and nets")
    p.add_argument("design")
    p.set_defaults(fn=cmd_show)
    p = sub.add_parser("new", help="write a starter design file")
    p.add_argument("name")
    p.set_defaults(fn=cmd_new)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
