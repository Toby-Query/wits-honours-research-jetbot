"""Constraints: what a design has to satisfy, and the report that says if it does.

Three kinds, deliberately separate because they answer different questions.

    Rule          is it WIRED sensibly?      static, no simulation   (erc.py)
    Limit         is any PART over a rating? watched on every step
    Requirement   does the DESIGN do its job? asked of a finished run

A Limit belongs to a component and is about that component's datasheet. A
Requirement belongs to the circuit and is about what you are building it for.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

from .core import ELECTRICAL


# --------------------------------------------------------------------------
# requirements
# --------------------------------------------------------------------------
class Requirement:
    """A pass/fail question asked of a Result.

    `check(result)` returns True/False, or (ok, "detail to print").
    """

    def __init__(self, name, check, severity="error", doc=""):
        self.name, self.check, self.severity, self.doc = name, check, severity, doc

    def evaluate(self, result):
        try:
            out = self.check(result)
        except Exception as e:                       # a broken check is a fail
            return False, f"check raised {type(e).__name__}: {e}"
        if isinstance(out, tuple):
            return bool(out[0]), str(out[1])
        return bool(out), ""


def at_least(signal, bound, name=None, **kw):
    return Requirement(
        name or f"{signal} never below {bound:g}",
        lambda r: (r.min(signal) >= bound, f"min {r.min(signal):.4g}"), **kw)


def at_most(signal, bound, name=None, **kw):
    return Requirement(
        name or f"{signal} never above {bound:g}",
        lambda r: (r.max(signal) <= bound, f"max {r.max(signal):.4g}"), **kw)


def within(signal, lo, hi, name=None, **kw):
    return Requirement(
        name or f"{signal} stays within {lo:g}..{hi:g}",
        lambda r: (r.min(signal) >= lo and r.max(signal) <= hi,
                   f"range {r.min(signal):.4g}..{r.max(signal):.4g}"), **kw)


def final_at_least(signal, bound, name=None, **kw):
    return Requirement(
        name or f"{signal} ends at or above {bound:g}",
        lambda r: (r.final(signal) >= bound, f"final {r.final(signal):.4g}"),
        **kw)


# --------------------------------------------------------------------------
# limits, watched while the simulation runs
# --------------------------------------------------------------------------
@dataclass
class LimitResult:
    component: str
    signal: str
    which: str                  # "max" | "min"
    bound: float
    worst: float
    severity: str
    for_s: float = 0.0
    violated: bool = False
    t_first: float = None
    time_over: float = 0.0
    why: str = ""

    @property
    def margin(self):
        """Fraction of the bound left. Negative means over it."""
        if self.bound == 0 or not math.isfinite(self.worst):
            return float("nan")
        if self.which == "max":
            return (self.bound - self.worst) / abs(self.bound)
        return (self.worst - self.bound) / abs(self.bound)

    @property
    def label(self):
        return f"{self.component}.{self.signal} {'<=' if self.which == 'max' else '>='} {self.bound:.4g}"


class _Watch:
    __slots__ = ("comp", "limit", "which", "res", "run")

    def __init__(self, comp, limit, which):
        self.comp, self.limit, self.which = comp, limit, which
        self.run = 0.0
        self.res = LimitResult(
            comp.name, ("|" + limit.signal + "|") if limit.absolute else limit.signal,
            which, float("nan"),
            -math.inf if which == "max" else math.inf,
            limit.severity, limit.for_s, why=limit.why)


class Monitor:
    """Watches every Limit of every component, every step."""

    def __init__(self, circuit):
        self.circuit = circuit
        self.watches = []
        from .core import Limit
        for c in circuit:
            limits = c.all_limits()
            if c.thermal is not None and c.thermal.t_max is not None:
                limits.append(Limit("T", max=c.thermal.t_max,
                                    why="maximum temperature"))
            for lim in limits:
                for which in ("max", "min"):
                    if getattr(lim, which) is not None:
                        self.watches.append(_Watch(c, lim, which))
        # absolute-maximum pin voltages, from the Port declarations
        self.pin_watches = []
        for c in circuit:
            ref = next((p for p in c.pins if p.kind == "ground"), None)
            for p in c.pins:
                if p.kind in ELECTRICAL and p.spec.v_abs_max is not None:
                    lo = p.spec.v_abs_min if p.spec.v_abs_min is not None else -0.3
                    self.pin_watches.append((
                        p, ref,
                        LimitResult(c.name, f"v({p.name})", "max",
                                    p.spec.v_abs_max, -math.inf, "error",
                                    why="absolute maximum pin voltage"),
                        LimitResult(c.name, f"v({p.name})", "min", lo,
                                    math.inf, "error",
                                    why="absolute minimum pin voltage")))
        self._sys = None

    def attach(self, system):
        self._sys = system

    def observe(self, t, dt):
        for w in self.watches:
            v = w.comp.sig.get(w.limit.signal)
            if v is None:
                continue
            if w.limit.absolute:
                v = abs(v)
            bound = w.limit.bound(w.comp, w.which)
            r = w.res
            r.bound = float(bound)
            over = v > bound if w.which == "max" else v < bound
            if w.which == "max":
                r.worst = max(r.worst, v)
            else:
                r.worst = min(r.worst, v)
            if over:
                w.run += dt
                if w.run >= w.limit.for_s:
                    r.time_over += dt
                    if not r.violated:
                        r.violated, r.t_first = True, t
            else:
                w.run = 0.0
        if self.pin_watches:
            for p, ref, hi, lo in self.pin_watches:
                v = p.comp.sig.get("_v_" + p.name)
                if v is None:
                    continue
                hi.worst = max(hi.worst, v)
                lo.worst = min(lo.worst, v)
                if v > hi.bound:
                    hi.time_over += dt
                    if not hi.violated:
                        hi.violated, hi.t_first = True, t
                if v < lo.bound:
                    lo.time_over += dt
                    if not lo.violated:
                        lo.violated, lo.t_first = True, t

    def results(self):
        out = [w.res for w in self.watches if math.isfinite(w.res.worst)]
        for _, _, hi, lo in self.pin_watches:
            if math.isfinite(hi.worst):
                out.append(hi)
            if math.isfinite(lo.worst) and lo.violated:
                out.append(lo)
        return out


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------
@dataclass
class Finding:
    severity: str               # "error" | "warn" | "info"
    rule: str
    message: str
    where: str = ""


@dataclass
class ScenarioReport:
    name: str
    doc: str = ""
    limits: list = field(default_factory=list)
    requirements: list = field(default_factory=list)   # (Requirement, ok, detail)
    error: str = ""
    metrics: dict = field(default_factory=dict)

    @property
    def failures(self):
        n = 1 if self.error else 0
        n += sum(1 for l in self.limits if l.violated and l.severity == "error")
        n += sum(1 for r, ok, _ in self.requirements
                 if not ok and r.severity == "error")
        return n

    @property
    def warnings(self):
        n = sum(1 for l in self.limits if l.violated and l.severity == "warn")
        n += sum(1 for r, ok, _ in self.requirements
                 if not ok and r.severity == "warn")
        return n


@dataclass
class Report:
    design: str
    findings: list = field(default_factory=list)       # from the rule check
    scenarios: list = field(default_factory=list)
    assumptions: list = field(default_factory=list)

    @property
    def failures(self):
        return (sum(1 for f in self.findings if f.severity == "error")
                + sum(s.failures for s in self.scenarios))

    @property
    def warnings(self):
        return (sum(1 for f in self.findings if f.severity == "warn")
                + sum(s.warnings for s in self.scenarios))

    @property
    def ok(self):
        return self.failures == 0

    # -- rendering -----------------------------------------------------------
    def text(self, verbose=False):
        tag = {"error": "FAIL", "warn": "WARN", "info": "info"}
        L = [f"{self.design}", "=" * max(len(self.design), 40), ""]
        L.append("Design rules")
        L.append("------------")
        if not self.findings:
            L.append("  [PASS] nothing to report")
        for f in self.findings:
            L.append(f"  [{tag[f.severity]}] {f.rule}: {f.message}")
        for s in self.scenarios:
            L += ["", f"Scenario: {s.name}", "-" * (10 + len(s.name))]
            if s.doc:
                L.append(f"  {s.doc}")
            if s.error:
                L.append(f"  [FAIL] did not run: {s.error}")
                continue
            for k, v in s.metrics.items():
                L.append(f"  {k:<34} {v}")
            for r, ok, detail in s.requirements:
                t = "PASS" if ok else tag[r.severity]
                L.append(f"  [{t}] {r.name}" + (f"  --  {detail}" if detail else ""))
            shown = [l for l in s.limits if l.violated or verbose]
            for l in sorted(shown, key=lambda l: (not l.violated, l.margin)):
                t = tag[l.severity] if l.violated else "PASS"
                extra = (f", over for {l.time_over * 1e3:.0f} ms from "
                         f"t={l.t_first:.3f} s" if l.violated else "")
                L.append(f"  [{t}] {l.label}  --  worst {l.worst:.4g} "
                         f"(margin {l.margin * 100:+.0f}%){extra}"
                         + (f"  [{l.why}]" if l.why and l.violated else ""))
            n_ok = sum(1 for l in s.limits if not l.violated)
            if not verbose and n_ok:
                tight = sorted((l for l in s.limits if not l.violated
                                and l.margin >= 0), key=lambda l: l.margin)[:3]
                L.append(f"  [PASS] {n_ok} ratings respected; tightest: "
                         + ", ".join(f"{l.component}.{l.signal} "
                                     f"({l.margin * 100:.0f}% left)"
                                     for l in tight))
        if self.assumptions:
            L += ["", f"Resting on {len(self.assumptions)} unverified numbers",
                  "-" * 34]
            merged = {}
            for comp, k, v, why in self.assumptions:
                merged.setdefault((k, v, why), []).append(comp)
            for (k, v, why), comps in merged.items():
                who = comps[0] if len(comps) == 1 else f"{comps[0]} (+{len(comps) - 1})"
                L.append(f"  {who}.{k} = {v:g}" + (f"   ({why})" if why else ""))
        L += ["", f"{self.failures} failure(s), {self.warnings} warning(s)"]
        return "\n".join(L)

    def to_dict(self):
        return {
            "design": self.design, "ok": self.ok,
            "failures": self.failures, "warnings": self.warnings,
            "findings": [vars(f) for f in self.findings],
            "scenarios": [{
                "name": s.name, "doc": s.doc, "error": s.error,
                "metrics": s.metrics,
                "requirements": [{"name": r.name, "ok": ok, "detail": d,
                                  "severity": r.severity}
                                 for r, ok, d in s.requirements],
                "limits": [{"label": l.label, "worst": l.worst,
                            "bound": l.bound, "margin": l.margin,
                            "violated": l.violated, "severity": l.severity,
                            "time_over": l.time_over, "why": l.why}
                           for l in s.limits],
            } for s in self.scenarios],
            "assumptions": [{"component": c, "param": k, "value": v, "why": w}
                            for c, k, v, w in self.assumptions],
        }

    def to_json(self, path=None):
        txt = json.dumps(self.to_dict(), indent=1, default=float)
        if path:
            with open(path, "w") as f:
                f.write(txt)
        return txt
