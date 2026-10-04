# elecsim: the electrical simulation framework

`elecsim` lets you describe a circuit in Python, say what it must and must not
do, and find out whether it does. It is the part of this repository that has
nothing to do with the JetBot: the JetBot is one design written with it
([jetbot_twin/electrical.py](../jetbot_twin/electrical.py)), and yours would be
another.

It needs Python 3.9+ and numpy. Nothing else.

```bash
python -m elecsim new my_board          # writes my_board.py, a working starter
python -m elecsim check my_board.py     # rule-check it and run its scenarios
python -m elecsim parts                 # what is in the library
python -m elecsim parts BuckConverter   # one part's pins and parameters
```

## The five ideas

| | What it is | Where |
|---|---|---|
| **Component** | A part: pins, numbers, and the equations that relate them | `elecsim.Component`, `elecsim/lib/` |
| **Circuit** | Components with their pins joined into named nets | `elecsim.Circuit` |
| **Constraint** | Something that must hold. Three kinds, below | `Limit`, `Requirement`, `@rule` |
| **Scenario** | A situation to put the circuit in: what changes, for how long | `elecsim.Scenario` |
| **Report** | Every constraint, in every scenario, pass or fail | `elecsim.evaluate` |

A design is a Python file with a `build()` function that returns a `Circuit`,
and optionally a `SCENARIOS` list. That is the whole contract.

```python
from elecsim import Circuit, Scenario, assumed, datasheet
from elecsim.lib import Cell, Resistor, Switch

def build():
    c = Circuit("torch")
    cell = c.add(Cell("cell", capacity_ah=datasheet(2.6, "cell datasheet"),
                      r0=assumed(0.06, "typical; not measured")))
    sw   = c.add(Switch("sw", r_on=0.03, i_max=1.0))
    lamp = c.add(Resistor("lamp", r=6.0, p_max=3.0))

    c.connect("BAT", cell.p, sw.a)      # a net is a name and the pins on it
    c.connect("LAMP", sw.b, lamp.a)
    c.ground(cell.n, lamp.b)

    c.require("the lamp gets at least 2 W", lambda r: r.min("lamp.heat") >= 2.0)
    return c

SCENARIOS = [Scenario("ten minutes on", duration=600, dt=1.0)]
```

## Constraints

There are three kinds because there are three different questions.

**Rules: is it wired sensibly?** Checked without simulating. The built-in ones
catch an unconnected pin, a net with one end, two supplies driving one net, a
logic input nothing drives, a 5 V output into a 3.3 V input, a net with no path
to ground. Add your own with `@rule`; it receives the circuit and yields
`Finding`s.

```python
from elecsim import rule, Finding

@rule
def every_led_has_a_series_resistor(circuit):
    for c in circuit:
        if isinstance(c, Led) and not has_resistor(c):
            yield Finding("error", "LED without a resistor", f"{c.name} ...")
```

**Limits: is any part over a rating?** A limit belongs to a component and is
about that component's datasheet. It is watched on every step of every
simulation.

```python
class Led(Component):
    i_max = Param(0.030, unit="A")
    limits = (Limit("i", max="i_max", why="LED forward current"),)
```

`max` and `min` can be a number or the name of a parameter. `for_s=0.5` means
"only if it stays over for half a second", which is how a continuous rating
differs from a peak one. `absolute=True` compares the magnitude. A component
given a `Thermal(rth, cth, t_max)` gets a temperature and a limit on it for
free, and any pin declared with `v_abs_max` is watched against it.

To add a rating to one instance rather than the whole class:
`c["r5"].limit("heat", max=0.25)`.

**Requirements: does the design do its job?** A requirement belongs to the
circuit, or to one scenario, and is a question asked of a finished run.

```python
c.require("5 V rail stays in tolerance",
          lambda r: (r.min("jetson.v") >= 4.75, f"min {r.min('jetson.v'):.3f} V"))
```

The function gets a `Result` and returns `True`/`False`, or a tuple with a
detail string for the report. `at_least`, `at_most`, `within` and
`final_at_least` build the common ones.

## Writing your own part

Subclass `Component`. Declare pins with `Port` and numbers with `Param`. Write
`stamp`, which adds the part's equations, and `measure`, which says what can be
observed about it.

```python
from elecsim import Component, Param, Port

class Resistor(Component):
    a = Port()
    b = Port()
    r = Param(unit="ohm", min=0, doc="resistance")

    def stamp(self, s):
        s.G(self.a, self.b, 1.0 / self.r)

    def measure(self, s):
        v = s.vd(self.a, self.b)
        return {"v": v, "i": v / self.r, "heat": v * v / self.r}
```

What `stamp` has to work with:

| Call | Adds |
|---|---|
| `s.G(a, b, g)` | a conductance between two pins |
| `s.I(a, b, i)` | a current flowing from `a` to `b` through the part |
| `s.Inl(a, b, i, g)` | a nonlinear branch: its current now, and its slope |
| `s.V(self, p, n, volts, r=0)` | a voltage source with series resistance; needs `branches = 1` |
| `s.v(pin)`, `s.vd(a, b)`, `s.i(self)` | the present solution, for nonlinear parts |
| `s.A`, `s.b`, `s.row(self)` | the matrix itself, for anything else |
| `s.t`, `s.dt`, `s.dc` | time, the step, and whether this is the DC solve |

Whatever `measure` returns becomes a signal, named `component.key`. Signals are
what limits watch, what requirements ask about and what gets recorded. If one
of them is called `heat`, the thermal model uses it.

The other hooks, all optional:

| Hook | Called | For |
|---|---|---|
| `update(s, dt)` | after each solved step | advancing state: charge, a timer, a shaft speed |
| `pre_step(t, dt)` | before each solve | acting on the last result: a comparator, a state machine |
| `init_state(s)` | after the DC solve | taking initial state from the operating point |
| `stamp_fault(s)` | instead of `stamp`, when broken | what failure looks like, if not a plain open or short |
| `relax()` | when a solve has no solution | a regulator switching itself off because its supply has gone; return `True` and the solve is retried |

Two things to know:

- **Set `linear = False`** if `stamp` reads the solution. The solver then
  iterates; otherwise it solves once.
- **For an exponential, use `junction_iv`**, not `math.exp`. It is the diode
  equation continued as a straight line where the exponential would overflow,
  and it is what lets the solver recover from a bad first guess.

Things the outside world sets while a simulation runs live in `self.inputs`: a
switch's `closed`, a load's `p`, a motor's `omega`. A scenario's stimulus
writes to them.

[examples/02_your_own_part.py](../examples/02_your_own_part.py) is a complete
worked example: two new parts, a rating, a requirement, a rule and a fault.
[jetbot_twin/parts.py](../jetbot_twin/parts.py) is a harder one, with three
electrical pins and its own unknown.

## Numbers that remember where they came from

Wrap a value to record how it is known. It is still a float.

```python
r0 = assumed(0.06, "typical; not measured")
v  = datasheet(5.118, "APW7313 with R3 68k / R7 15k")
m  = measured(0.742, "kitchen scale, 2026-10-06")
```

Every report ends with the list of `assumed` values the result rests on. A
simulation is only as good as its least-checked input, and this is the list of
them.

## Scenarios

```python
def stall(t, c):                       # called before every step
    c["drive"].inputs["locked"] = t > 0.5

Scenario("stall", duration=3.0, dt=1e-3, stimulus=stall,
         setup=lambda c: ...,          # once, before the run
         build_args={"soc0": 0.1},     # passed to build()
         require=[...],                # requirements for this scenario only
         metrics={"lowest rail": lambda r: f"{r.min('U3.v_in'):.2f} V"})
```

Each scenario gets a freshly built circuit, so nothing carries over between
them.

To drive a simulation yourself instead:

```python
sim = Simulation(build(), dt=1e-3)     # solves the DC operating point
sim.step()                             # one step; read circuit["x"].sig
res = sim.run(2.0, stimulus=fn)        # a Result
res["cell.v"], res.t, res.min("cell.v"), res.at("cell.v", 1.5)
res.to_csv("run.csv")
```

## Breaking it on purpose

```bash
python -m elecsim fmea my_board.py -s "load step"
```

applies every open and short the components say they can suffer, one at a
time, re-runs the scenario, and tabulates which requirements each one costs
you. From code: `elecsim.faults.fmea(build, scenario, faults)`, with
`open_circuit`, `short_circuit`, `drift` (a parameter off by a factor) and
`set_input` to build the list yourself.

## Sweeps and uncertainty

`elecsim.analysis` works on any function from a dict of parameters to a dict of
numbers, so it applies to a circuit, to something built around one, or to
anything else.

| | |
|---|---|
| `sweep(f, name, values)` | one parameter across a range |
| `sensitivity(f, nominal)` | which parameter moves which result, as an elasticity |
| `monte_carlo(f, nominal, spread)` | sample the uncertain ones; get a distribution |

## The library

`python -m elecsim parts` lists it. In brief:

| | |
|---|---|
| Passive | `Resistor`, `Wire` (length and gauge), `Capacitor` (with ESR), `Inductor`, `Switch`, `Fuse` (i²t) |
| Sources and loads | `VoltageSource` (with current limit), `CurrentSource`, `ConstantPowerLoad`, `ConstantCurrentLoad` |
| Semiconductors | `Diode`, `MosfetSwitch` (with body diode) |
| Battery | `Cell` (OCV curve, R0, one RC pair, state of charge), `series_pack`, `PackProtector` |
| Conversion | `BuckConverter` (losses, dropout, UVLO, current limit), `LinearRegulator` |
| Motion | `DCMotor`, `HBridge` (switching), `PwmMotorDrive` (bridge and motor, averaged) |
| Logic | `PwmController` (PCA9685 registers), `LogicLevel` |
| Sensing | `ShuntMonitor` (INA219), `Adc` |

### Two ways to model a PWM drive

`HBridge` with a `DCMotor` simulates the switching: every edge, every body
diode. It needs steps of microseconds. `PwmMotorDrive` is the same bridge and
motor with the PWM period solved in closed form
([elecsim/pwm.py](../elecsim/pwm.py)), so it takes millisecond steps and still
gets discontinuous conduction and both decay modes right.
[tests/test_pwm_models.py](../tests/test_pwm_models.py) holds the two to
within 3 % of each other across duty, speed and decay mode. Use the first to
look at a waveform and the second for anything longer than a few periods.

## How it solves

Modified nodal analysis. The unknowns are the voltage of every net with
something electrical on it, plus any extra unknowns components ask for (a
voltage source's current, for instance). Each Newton iteration every component
stamps its linearised equations, the system is solved, and it repeats until
the answer stops moving. Time stepping is backward Euler with a fixed step,
halved automatically when a step fails to converge.

Nets that carry only logic pins are not solved. They hold a level between 0
and 1, which is a logic state when switching is being simulated and a duty
cycle when it is being averaged.

## What it is not

- **Not SPICE.** No device-physics transistor models, no AC or noise analysis,
  no adaptive step control. Backward Euler damps ringing that a real circuit
  would show. It is for power paths, budgets, ratings and system behaviour, at
  the level of a block diagram with numbers.
- **Not layout-aware.** A net is ideal: no trace resistance or inductance
  unless you add a `Wire` or a `Resistor` for it.
- **Thermal is first-order.** One resistance to ambient and one mass per part.
  Good for "does it overheat in ten seconds"; not for heatsink design.
- **Small systems.** Dense matrices, pure Python. Tens of components at about
  three times real time with millisecond steps.

## Tests

```bash
pip install -e ".[dev]"
pytest
```
