"""The vocabulary: parameters, ports, components, nets, circuits.

A design is a `Circuit`. A circuit holds `Component`s; each component has
`Port`s (its pins) and `Param`s (its numbers); `Circuit.connect` joins pins
into named nets. Nothing here knows how to solve anything -- that is
`solver.py` -- and nothing here knows about any particular part.

Defining a part is declaring what it has and saying how it behaves:

    class Resistor(Component):
        a = Port()
        b = Port()
        r = Param(unit="ohm", min=0, doc="resistance")

        def stamp(self, s):
            s.G(self.a, self.b, 1.0 / self.r)

        def measure(self, s):
            v = s.vd(self.a, self.b)
            return {"v": v, "i": v / self.r, "heat": v * v / self.r}
"""
from __future__ import annotations

import math

REQUIRED = object()

# Port kinds. The first group takes part in the electrical solution; the
# second carries a logic level (or a duty cycle) and is not solved for.
ELECTRICAL = {"passive", "power_in", "power_out", "ground", "analog_in",
              "analog_out"}
SIGNAL = {"digital_in", "digital_out", "bidir"}
KINDS = ELECTRICAL | SIGNAL | {"nc"}


# --------------------------------------------------------------------------
# numbers that remember where they came from
# --------------------------------------------------------------------------
class Q(float):
    """A float with a provenance. Behaves as a float everywhere."""

    def __new__(cls, value, src="", assumed=False):
        o = super().__new__(cls, value)
        o.src = src
        o.assumed = assumed
        return o


def assumed(value, why=""):
    """A number nobody has checked. Reports list every one of these."""
    return Q(value, why, True)


def datasheet(value, ref=""):
    return Q(value, ref, False)


def measured(value, how=""):
    return Q(value, "measured: " + how if how else "measured", False)


# --------------------------------------------------------------------------
# declarations
# --------------------------------------------------------------------------
class Param:
    """A number (or table, or string) a component is built from."""

    def __init__(self, default=REQUIRED, unit="", doc="", min=None, max=None):
        self.default, self.unit, self.doc = default, unit, doc
        self.min, self.max = min, max
        self.name = None

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        return obj._values[self.name]

    def __set__(self, obj, value):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if self.min is not None and value < self.min:
                raise ValueError(f"{obj.name}.{self.name} = {value} is below "
                                 f"the minimum {self.min} {self.unit}")
            if self.max is not None and value > self.max:
                raise ValueError(f"{obj.name}.{self.name} = {value} is above "
                                 f"the maximum {self.max} {self.unit}")
        obj._values[self.name] = value


class Port:
    """A pin on a component.

    kind        one of KINDS; drives the rule check and decides whether the
                net it lands on is solved (electrical) or carried (signal)
    v_abs_max   absolute maximum voltage on this pin, relative to the
                component's ground port -- checked on every solved step
    v_abs_min   likewise (default: nothing below -0.3 V if v_abs_max is set)
    voh, vih    logic levels, for the level-compatibility rule
    optional    may be left unconnected without the rule check objecting
    """

    def __init__(self, kind="passive", doc="", v_abs_max=None, v_abs_min=None,
                 voh=None, vih=None, optional=False):
        if kind not in KINDS:
            raise ValueError(f"unknown port kind {kind!r}")
        self.kind, self.doc = kind, doc
        self.v_abs_max, self.v_abs_min = v_abs_max, v_abs_min
        self.voh, self.vih = voh, vih
        self.optional = optional or kind == "nc"
        self.name = None

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        return obj._pins[self.name]


class Pin:
    """One port of one component instance. This is what you connect."""

    __slots__ = ("comp", "name", "spec", "net", "node")

    def __init__(self, comp, name, spec):
        self.comp, self.name, self.spec = comp, name, spec
        self.net = None
        self.node = -1          # MNA row; -1 is ground or "not solved"

    @property
    def kind(self):
        return self.spec.kind

    @property
    def level(self):
        """Logic level or duty on a signal net, 0..1."""
        return self.net.level if self.net is not None else 0.0

    def drive(self, level):
        if self.net is not None:
            self.net.level = level

    def __repr__(self):
        return f"{self.comp.name}.{self.name}"


class Thermal:
    """A first-order thermal model: one resistance to ambient, one mass."""

    def __init__(self, rth, cth=None, t_max=None):
        self.rth, self.cth, self.t_max = rth, cth, t_max


class Limit:
    """A rating: a signal that must stay inside a bound.

        Limit("i", max="i_max")                  bound is another Param
        Limit("i", max=3.2, for_s=0.010)         only if exceeded for 10 ms
        Limit("v", min=4.75, severity="warn")

    `absolute` compares |signal|. `for_s` separates a peak rating from a
    continuous one: the bound must be exceeded continuously for that long
    before it counts.
    """

    def __init__(self, signal, max=None, min=None, severity="error",
                 for_s=0.0, absolute=False, why=""):
        if severity not in ("error", "warn", "info"):
            raise ValueError(severity)
        self.signal, self.max, self.min = signal, max, min
        self.severity, self.for_s, self.absolute = severity, for_s, absolute
        self.why = why

    def bound(self, comp, which):
        b = getattr(self, which)
        if isinstance(b, str):
            b = getattr(comp, b)
        return b


class Component:
    """Base class for every part. Subclass it, declare, and stamp.

    Class attributes a subclass may set
        branches     extra unknowns it needs (a voltage source needs one)
        linear       False if its stamp depends on the present solution
        limits       tuple of Limit
        fault_modes  which of "open"/"short" the generic injector may apply

    Methods a subclass may define
        stamp(s)     add its equations to the system           (required)
        measure(s)   return {signal: value} after a solved step
        update(s,dt) advance internal state after a solved step
        pre_step(t, dt)   act on last step's result before the next solve
        init_state(s)     take initial state from the DC operating point
        relax()           give up regulating when a solve fails; see below
    """

    branches = 0
    linear = True
    limits = ()
    fault_modes = ()
    _ports: dict = {}
    _params: dict = {}

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        ports, params = {}, {}
        for klass in reversed(cls.__mro__):
            for k, v in vars(klass).items():
                if isinstance(v, Port):
                    ports[k] = v
                elif isinstance(v, Param):
                    params[k] = v
        cls._ports, cls._params = ports, params

    def __init__(self, name, thermal=None, note="", **values):
        self.name = name
        self.note = note
        self.thermal = thermal
        self.circuit = None
        self._values = {}
        self._pins = {k: Pin(self, k, spec) for k, spec in self._ports.items()}
        self._br = -1                    # index of first branch unknown
        self.inputs = {}                 # things the outside world sets
        self.sig = {}                    # latest measurements
        self.fault = None                # None | "open" | "short"
        self.extra_limits = []
        self.T = None                    # junction/body temperature, if thermal
        self._abs_pins = None
        unknown = set(values) - set(self._params)
        if unknown:
            raise TypeError(f"{type(self).__name__} {name!r} has no parameter"
                            f" {sorted(unknown)}; it has {sorted(self._params)}")
        for k, p in self._params.items():
            if k in values:
                setattr(self, k, values[k])
            elif p.default is REQUIRED:
                raise TypeError(f"{type(self).__name__} {name!r} needs {k!r}"
                                f" ({p.doc or p.unit})")
            else:
                setattr(self, k, p.default)

    # -- declared interface -------------------------------------------------
    @property
    def pins(self):
        return list(self._pins.values())

    def params(self):
        return dict(self._values)

    def limit(self, *a, **kw):
        """Attach a rating to this instance only."""
        self.extra_limits.append(Limit(*a, **kw))
        return self

    def all_limits(self):
        return list(self.limits) + self.extra_limits

    # -- behaviour: override ------------------------------------------------
    def stamp(self, s):
        raise NotImplementedError(f"{type(self).__name__}.stamp")

    def measure(self, s):
        return {}

    def update(self, s, dt):
        pass

    def pre_step(self, t, dt):
        pass

    def init_state(self, s):
        pass

    def relax(self):
        """Called when a solve fails. A part that can only meet its
        equations with a supply it has not got (a regulator, mostly) should
        switch itself off here and return True; the solve is then retried."""
        return False

    # -- called by the solver ------------------------------------------------
    def _stamp(self, s):
        if self.fault is not None:
            self.stamp_fault(s)
        else:
            self.stamp(s)

    def stamp_fault(self, s):
        """Generic open / short between the first two electrical pins.

        Override for a part whose failure looks like something else.
        """
        el = [p for p in self.pins if p.kind in ELECTRICAL]
        for k in range(self.branches):               # park the unknowns
            s.A[self._br + k, self._br + k] += 1.0
        if len(el) >= 2:
            s.G(el[0], el[1], 1e3 if self.fault == "short" else 1e-12)

    def _measure(self, s, dt, ambient):
        if self.fault is not None:
            sig = {"heat": 0.0, "failed": 1.0}
        else:
            sig = self.measure(s) or {}
            sig.setdefault("failed", 0.0)
        if self.thermal is not None:
            th = self.thermal
            p = sig.get("heat", 0.0)
            if self.T is None:
                self.T = ambient
            t_inf = ambient + p * th.rth
            if th.cth and dt:
                self.T = t_inf + (self.T - t_inf) * math.exp(
                    -dt / (th.rth * th.cth))
            else:
                self.T = t_inf
            sig["T"] = self.T
        if self._abs_pins is None:
            ref = next((p for p in self.pins if p.kind == "ground"), None)
            self._abs_pins = [(p, ref) for p in self.pins
                              if p.kind in ELECTRICAL
                              and p.spec.v_abs_max is not None]
        for p, ref in self._abs_pins:
            sig["_v_" + p.name] = s.v(p) - (s.v(ref) if ref else 0.0)
        self.sig = sig
        return sig

    def __repr__(self):
        return f"<{type(self).__name__} {self.name}>"


# --------------------------------------------------------------------------
# nets and circuits
# --------------------------------------------------------------------------
class Net:
    def __init__(self, name):
        self.name = name
        self.pins = []
        self.node = -1
        self.level = 0.0            # logic level / duty, for signal nets

    @property
    def is_signal(self):
        """True if nothing electrical is on it: carried, not solved."""
        return bool(self.pins) and all(
            p.kind in SIGNAL or p.kind == "nc" for p in self.pins)

    def __repr__(self):
        return f"<Net {self.name}: {', '.join(map(repr, self.pins))}>"


class Circuit:
    """A set of components and the nets that join their pins."""

    GND = "GND"

    def __init__(self, name, ambient=25.0, doc=""):
        self.name, self.ambient, self.doc = name, ambient, doc
        self.components = {}
        self.nets = {}
        self.requirements = []
        self.rules = []                 # extra design rules: fn(circuit)
        self.n_nodes = 0
        self.n_unknowns = 0
        self._compiled = False

    # -- building -------------------------------------------------------------
    def add(self, comp):
        if comp.name in self.components:
            raise ValueError(f"two components are called {comp.name!r}")
        comp.circuit = self
        self.components[comp.name] = comp
        self._compiled = False
        return comp

    def net(self, name):
        if name not in self.nets:
            self.nets[name] = Net(name)
        return self.nets[name]

    def connect(self, net_name, *pins):
        """Put these pins on the net called `net_name`."""
        net = self.net(net_name)
        for p in pins:
            if not isinstance(p, Pin):
                raise TypeError(f"connect({net_name!r}, ...) was given {p!r},"
                                " which is not a pin")
            if p.net is not None and p.net is not net:
                raise ValueError(f"{p} is already on net {p.net.name!r}; "
                                 f"cannot also put it on {net_name!r}")
            if p.net is None:
                p.net = net
                net.pins.append(p)
        self._compiled = False
        return net

    def ground(self, *pins):
        return self.connect(self.GND, *pins)

    def disconnect(self, pin):
        if pin.net is not None:
            pin.net.pins.remove(pin)
            pin.net = None
            self._compiled = False

    def require(self, name, check, severity="error", doc=""):
        from .constraints import Requirement
        self.requirements.append(Requirement(name, check, severity, doc))
        return self

    def __getitem__(self, name):
        return self.components[name]

    def __contains__(self, name):
        return name in self.components

    def __iter__(self):
        return iter(self.components.values())

    # -- numbering --------------------------------------------------------------
    def compile(self):
        """Give every solved net a row and every branch unknown a column."""
        k = 0
        for net in self.nets.values():
            if net.name == self.GND or net.is_signal or not net.pins:
                net.node = -1
            else:
                net.node = k
                k += 1
            for p in net.pins:
                p.node = net.node
        # An unconnected electrical pin still needs somewhere to be.
        for c in self:
            for p in c.pins:
                if p.net is None:
                    if p.kind in ELECTRICAL:
                        p.node = k
                        k += 1
                    else:
                        p.node = -1
        self.n_nodes = k
        for c in self:
            c._br = k if c.branches else -1
            k += c.branches
        self.n_unknowns = k
        self._compiled = True
        return self

    def node_names(self):
        names = [None] * self.n_nodes
        for net in self.nets.values():
            if net.node >= 0:
                names[net.node] = net.name
        for c in self:
            for p in c.pins:
                if p.net is None and p.node >= 0:
                    names[p.node] = f"({p})"
        return names

    # -- introspection -----------------------------------------------------------
    def signals(self):
        """Every current measurement, flat: {"comp.signal": value}."""
        out = {}
        for c in self:
            for k, v in c.sig.items():
                if k[0] != "_":
                    out[f"{c.name}.{k}"] = v
        return out

    def assumptions(self):
        """Every parameter marked `assumed(...)`: what the result rests on."""
        out = []
        for c in self:
            for k, v in c._values.items():
                if isinstance(v, Q) and v.assumed:
                    out.append((c.name, k, float(v), v.src))
        return out

    def summary(self):
        return (f"{self.name}: {len(self.components)} components, "
                f"{len(self.nets)} nets")
