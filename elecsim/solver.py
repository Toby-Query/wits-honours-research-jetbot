"""Modified nodal analysis, Newton iteration, backward-Euler transient.

The unknowns are every solved net's voltage plus whatever extra unknowns
components ask for (`branches`). Each Newton iteration every component stamps
its linearised equations into A x = b; the system is solved; repeat until x
stops moving. A component that is linear stamps the same thing every time
and the loop ends after one solve.

Sign conventions, used by every component in the library:

    KCL rows      sum of currents LEAVING the node through elements = 0
    s.G(a, b, g)  a conductance g between pins a and b
    s.I(a, b, i)  an independent current i flowing from a to b through
                  the element (out of node a, into node b)
    s.V(...)      a branch whose unknown is the current entering pin p and
                  leaving pin n -- the passive sign convention
"""
from __future__ import annotations

import numpy as np

GMIN = 1e-12


class ConvergenceError(RuntimeError):
    pass


class System:
    """The matrix being built, and the solution being read."""

    def __init__(self, circuit):
        if not circuit._compiled:
            circuit.compile()
        self.circuit = circuit
        n = circuit.n_unknowns
        self.n = n
        self.A = np.zeros((n, n))
        self.b = np.zeros(n)
        self.x = np.zeros(n)
        self.t = 0.0
        self.dt = None            # None means DC: capacitors open, inductors short
        self.iteration = 0

    @property
    def dc(self):
        return self.dt is None

    # -- reading ---------------------------------------------------------------
    def v(self, pin):
        n = pin.node
        return self.x[n] if n >= 0 else 0.0

    def vd(self, a, b):
        return self.v(a) - self.v(b)

    def i(self, comp, k=0):
        return self.x[comp._br + k]

    # -- stamping ----------------------------------------------------------------
    def G(self, a, b, g):
        i, j = a.node, b.node
        A = self.A
        if i >= 0:
            A[i, i] += g
        if j >= 0:
            A[j, j] += g
        if i >= 0 and j >= 0:
            A[i, j] -= g
            A[j, i] -= g

    def I(self, a, b, cur):
        if a.node >= 0:
            self.b[a.node] -= cur
        if b.node >= 0:
            self.b[b.node] += cur

    def Inl(self, a, b, cur, g):
        """A nonlinear branch: current `cur` at the present voltage, slope g."""
        self.G(a, b, g)
        self.I(a, b, cur - g * self.vd(a, b))

    def V(self, comp, p, n, volts, r=0.0, k=0):
        """v(p) - v(n) = volts + r * i, with i the branch unknown (p -> n)."""
        br = comp._br + k
        A = self.A
        if p.node >= 0:
            A[p.node, br] += 1.0
            A[br, p.node] += 1.0
        if n.node >= 0:
            A[n.node, br] -= 1.0
            A[br, n.node] -= 1.0
        A[br, br] -= r
        self.b[br] += volts

    def row(self, comp, k=0):
        """Index of a component's own unknown, for hand-written equations."""
        return comp._br + k


class Result:
    """What a run recorded: a time axis and one array per signal."""

    def __init__(self, t, traces, limits=None, name=""):
        self.name = name
        self.t = np.asarray(t)
        self.traces = {k: np.asarray(v) for k, v in traces.items()}
        self.limits = limits or []

    def __getitem__(self, key):
        try:
            return self.traces[key]
        except KeyError:
            near = [k for k in self.traces if key.split(".")[0] in k][:8]
            raise KeyError(f"no signal {key!r} was recorded"
                           + (f"; did you mean one of {near}" if near else ""))

    def __contains__(self, key):
        return key in self.traces

    def keys(self):
        return self.traces.keys()

    def max(self, key):
        return float(np.max(self[key]))

    def min(self, key):
        return float(np.min(self[key]))

    def mean(self, key, t0=None, t1=None):
        return float(np.mean(self.window(key, t0, t1)))

    def rms(self, key, t0=None, t1=None):
        return float(np.sqrt(np.mean(self.window(key, t0, t1) ** 2)))

    def final(self, key):
        return float(self[key][-1])

    def at(self, key, t):
        return float(np.interp(t, self.t, self[key]))

    def window(self, key, t0=None, t1=None):
        y = self[key]
        m = np.ones(len(self.t), bool)
        if t0 is not None:
            m &= self.t >= t0
        if t1 is not None:
            m &= self.t <= t1
        return y[m]

    def integral(self, key):
        return float(np.trapezoid(self[key], self.t))

    def to_csv(self, path, keys=None):
        keys = list(keys or self.traces)
        with open(path, "w") as f:
            f.write("t," + ",".join(keys) + "\n")
            for i, t in enumerate(self.t):
                f.write(f"{t:.6f}," + ",".join(
                    f"{self.traces[k][i]:.6g}" for k in keys) + "\n")


class Simulation:
    """Owns a circuit's time. Step it, or run it and get a Result.

        sim = Simulation(circuit, dt=1e-3)
        sim.step()                         # one step; read circuit[...].sig
        res = sim.run(2.0, stimulus=fn)    # fn(t, circuit) called each step
    """

    def __init__(self, circuit, dt=1e-3, init="dc", max_iter=60,
                 reltol=1e-5, abstol=1e-7, v_step_limit=5.0):
        self.circuit = circuit
        self.dt = dt
        self.max_iter, self.reltol, self.abstol = max_iter, reltol, abstol
        self.v_step_limit = v_step_limit
        self.sys = System(circuit)
        self.comps = list(circuit)
        self.all_linear = all(c.linear for c in self.comps)
        self.t = 0.0
        self.steps = 0
        from .constraints import Monitor
        self.monitor = Monitor(circuit)
        if init == "dc":
            self.operating_point()
        elif init != "zero":
            raise ValueError("init must be 'dc' or 'zero'")

    # -- the solve ---------------------------------------------------------------
    def _newton(self, dt):
        """Solve one point. If plain Newton fails, try again with the node
        voltages only allowed to creep: slow, but it gets a hand-written
        exponential down off its cliff."""
        try:
            return self._newton_once(dt, self.v_step_limit, self.max_iter)
        except ConvergenceError:
            if self.all_linear:
                raise
        try:
            return self._newton_once(dt, 0.2, 12 * self.max_iter)
        except ConvergenceError:
            # No solution with everything regulating: something has lost its
            # supply. Let those parts shut down, and solve what is left.
            if not any([c.relax() for c in self.comps]):
                raise
            return self._newton_once(dt, self.v_step_limit, 4 * self.max_iter)

    def _newton_once(self, dt, v_step_limit, max_iter):
        s = self.sys
        s.dt = dt
        n_nodes = self.circuit.n_nodes
        diag = np.arange(n_nodes)
        x = s.x.copy()
        start = x.copy()
        for it in range(max_iter):
            s.iteration = it
            s.A.fill(0.0)
            s.b.fill(0.0)
            for c in self.comps:
                c._stamp(s)
            s.A[diag, diag] += GMIN
            try:
                x_new = np.linalg.solve(s.A, s.b)
            except np.linalg.LinAlgError:
                raise ConvergenceError(self._singular_hint())
            if not np.all(np.isfinite(x_new)):
                raise ConvergenceError("solution is not finite")
            if self.all_linear:
                s.x = x_new
                return it + 1
            dx = x_new - x
            # Damp wild node-voltage swings; leave branch currents alone.
            big = np.max(np.abs(dx[:n_nodes])) if n_nodes else 0.0
            if big > v_step_limit:
                x_new = x + dx * (v_step_limit / big)
                dx = x_new - x
            x = x_new
            s.x = x
            if np.all(np.abs(dx) <= self.abstol + self.reltol * np.abs(x)):
                return it + 1
        s.x = start
        raise ConvergenceError(
            f"no convergence after {max_iter} iterations at t={s.t:.6g}")

    def _singular_hint(self):
        s = self.sys
        names = self.circuit.node_names()
        dead = [names[i] for i in range(self.circuit.n_nodes)
                if not np.any(np.abs(s.A[i]) > 10 * GMIN)]
        msg = f"singular system at t={s.t:.6g}"
        if dead:
            msg += f"; nets with no path to anything: {dead}"
        return msg

    def operating_point(self):
        """DC solution: capacitors open, inductors short. Sets initial state."""
        s = self.sys
        s.t = self.t
        for c in self.comps:
            c.pre_step(self.t, None)
        self._newton(None)
        for c in self.comps:
            c.init_state(s)
        for c in self.comps:
            c._measure(s, None, self.circuit.ambient)
        return self.circuit.signals()

    def step(self, dt=None, _depth=0):
        """Advance one step. Halves the step on a failed solve."""
        dt = self.dt if dt is None else dt
        s = self.sys
        s.t = self.t + dt
        saved = s.x.copy()
        for c in self.comps:
            c.pre_step(self.t, dt)
        try:
            self._newton(dt)
        except ConvergenceError:
            s.x = saved
            if _depth >= 8:
                raise
            self.step(dt / 2, _depth + 1)
            self.step(dt / 2, _depth + 1)
            return
        for c in self.comps:
            c.update(s, dt)
        amb = self.circuit.ambient
        for c in self.comps:
            c._measure(s, dt, amb)
        self.t += dt
        self.steps += 1
        self.monitor.observe(self.t, dt)

    def run(self, duration, stimulus=None, record=None, every=1, name=""):
        """Run for `duration` seconds. Returns a Result.

        stimulus   fn(t, circuit), called before each step
        record     list of "comp.signal" names; None records everything
        every      keep one sample in this many
        """
        n = int(round(duration / self.dt))
        ts, rows = [], []
        keys = None
        for k in range(n):
            if stimulus is not None:
                stimulus(self.t, self.circuit)
            self.step()
            if k % every == 0 or k == n - 1:
                sig = self.circuit.signals()
                if keys is None:
                    keys = list(sig) if record is None else list(record)
                    missing = [q for q in keys if q not in sig]
                    if missing:
                        raise KeyError(f"cannot record {missing}: no such "
                                       f"signal. Available: {sorted(sig)[:40]}")
                ts.append(self.t)
                rows.append([sig.get(q, np.nan) for q in keys])
        arr = np.array(rows) if rows else np.zeros((0, 0))
        traces = {q: arr[:, j] for j, q in enumerate(keys or [])}
        return Result(ts, traces, self.monitor.results(), name=name)
