"""The framework on its own: solver, parts, constraints, rules, faults."""
import math

import pytest

from elecsim import (Circuit, Component, Limit, Param, Port, Scenario,
                     Simulation, Thermal, assumed, erc, evaluate, within)
from elecsim.faults import fmea, open_circuit
from elecsim.lib import (BuckConverter, Capacitor, Cell, ConstantPowerLoad,
                         DCMotor, Diode, Fuse, Inductor, Resistor, Switch,
                         VoltageSource, series_pack)


def divider(r1=1000.0, r2=1000.0, v=10.0):
    c = Circuit("divider")
    src = c.add(VoltageSource("src", v=v))
    a = c.add(Resistor("r1", r=r1))
    b = c.add(Resistor("r2", r=r2, p_max=0.01))
    c.connect("in", src.p, a.a)
    c.connect("mid", a.b, b.a)
    c.ground(src.n, b.b)
    return c


def test_dc_divider():
    c = divider()
    Simulation(c)
    assert c["r2"].sig["v"] == pytest.approx(5.0)
    assert c["src"].sig["i"] == pytest.approx(5e-3)


def test_rc_charges_with_the_right_time_constant():
    c = Circuit("rc")
    v = c.add(VoltageSource("v", v=10))
    r = c.add(Resistor("r", r=1e3))
    cap = c.add(Capacitor("c", c=1e-6, ic=0.0))
    c.connect("in", v.p, r.a)
    c.connect("out", r.b, cap.a)
    c.ground(v.n, cap.b)
    res = Simulation(c, dt=1e-6).run(1e-3)
    assert res.final("c.v") == pytest.approx(10 * (1 - math.exp(-1)), rel=2e-3)


def test_rl_current_rises_to_v_over_r():
    c = Circuit("rl")
    v = c.add(VoltageSource("v", v=5))
    sw = c.add(Switch("sw", closed=False))
    ind = c.add(Inductor("l", l=1e-3, dcr=1.0))
    c.connect("a", v.p, sw.a)
    c.connect("b", sw.b, ind.a)
    c.ground(v.n, ind.b)
    sim = Simulation(c, dt=1e-5)
    sw.inputs["closed"] = True
    res = sim.run(10e-3)
    assert res.final("l.i") == pytest.approx(5 / 1.02, rel=1e-3)
    assert res.at("l.i", 1e-3) == pytest.approx(5 / 1.02 * (1 - math.exp(-1.02)), rel=0.02)


def test_diode_drops_its_forward_voltage():
    c = Circuit("d")
    v = c.add(VoltageSource("v", v=5))
    r = c.add(Resistor("r", r=4.3))
    d = c.add(Diode("d", vf=0.7, i_f=1.0))
    c.connect("in", v.p, r.a)
    c.connect("k", r.b, d.a)
    c.ground(v.n, d.k)
    Simulation(c)
    assert d.sig["v"] == pytest.approx(0.7, abs=0.005)
    assert d.sig["i"] == pytest.approx(1.0, rel=0.01)


def test_cell_discharges_and_sags():
    c = Circuit("cell")
    cell = c.add(Cell("cell", capacity_ah=1.0, r0=0.1))
    r = c.add(Resistor("r", r=4.0))
    c.connect("p", cell.p, r.a)
    c.ground(cell.n, r.b)
    res = Simulation(c, dt=1.0).run(360)
    i = res["cell.i"]
    assert i[0] == pytest.approx(4.2 / 4.1, rel=1e-3)
    used = res.integral("cell.i") / 3600
    assert 1.0 - res.final("cell.soc") == pytest.approx(used, rel=0.01)
    assert res.final("cell.v") < res["cell.v"][0]


def test_buck_conserves_power_and_reports_its_losses():
    c = Circuit("buck")
    src = c.add(VoltageSource("src", v=12))
    b = c.add(BuckConverter("b", v_set=5.0, i_limit=4.0))
    ld = c.add(ConstantPowerLoad("ld", power=10.0))
    c.connect("in", src.p, b.vin)
    c.connect("out", b.out, ld.p)
    c.ground(src.n, b.gnd, ld.n)
    Simulation(c)
    s = b.sig
    assert s["v_out"] == pytest.approx(5.0)
    assert s["i_out"] == pytest.approx(2.0, rel=1e-6)
    assert src.sig["p"] == pytest.approx(10.0 + s["heat"], rel=1e-6)
    assert 0.85 < s["eff"] < 0.99


def test_buck_drops_out_then_shuts_down_as_the_input_falls():
    c = Circuit("sag")
    src = c.add(VoltageSource("src", v=lambda t: 12.0 - 10.0 * t))
    b = c.add(BuckConverter("b", v_set=5.0, i_limit=4.0))
    ld = c.add(Resistor("ld", r=5.0))
    c.connect("in", src.p, b.vin)
    c.connect("out", b.out, ld.a)
    c.ground(src.n, b.gnd, ld.b)
    res = Simulation(c, dt=1e-3).run(1.0)
    assert res.at("b.v_out", 0.3) == pytest.approx(5.0, abs=1e-6)
    assert 4.0 < res.at("b.v_out", 0.72) < 5.0            # dropout
    assert res.final("b.v_out") == pytest.approx(0.0, abs=1e-3)   # UVLO


def test_motor_reaches_its_no_load_speed():
    c = Circuit("m")
    v = c.add(VoltageSource("v", v=6))
    m = c.add(DCMotor("m", r=4.0, l=2e-3, ke=0.2, inertia=1e-4))
    c.connect("a", v.p, m.a)
    c.ground(v.n, m.b)
    res = Simulation(c, dt=1e-4).run(0.5)
    assert res.final("m.omega") == pytest.approx(30.0, rel=0.01)
    assert abs(res.final("m.i")) < 0.02


def test_fuse_blows_on_overload_and_not_at_rating():
    def run(load_r):
        c = Circuit("f")
        v = c.add(VoltageSource("v", v=12))
        f = c.add(Fuse("f", i_rating=2.0))
        r = c.add(Resistor("r", r=load_r))
        c.connect("a", v.p, f.a)
        c.connect("b", f.b, r.a)
        c.ground(v.n, r.b)
        return Simulation(c, dt=1e-3).run(2.0)
    assert run(6.5).max("f.blown") == 0           # 1.85 A
    assert run(1.0).max("f.blown") == 1           # 12 A


# ---------------------------------------------------------------- constraints
def test_limit_is_reported_when_exceeded():
    c = divider()
    res = Simulation(c).run(0.01)
    lim = [l for l in res.limits if l.component == "r2"][0]
    assert lim.violated and lim.worst == pytest.approx(0.025)
    assert lim.margin < 0


def test_limit_with_a_duration_ignores_a_short_peak():
    class Pulse(Component):
        a = Port()
        b = Port()
        limits = (Limit("i", max=1.0, for_s=0.05),)

        def stamp(self, s):
            s.G(self.a, self.b, 1.0)

        def measure(self, s):
            return {"i": 5.0 if 0.010 <= s.t < self.inputs["until"] else 0.0}

    def run(until):
        c = Circuit("p")
        v = c.add(VoltageSource("v", v=1))
        p = c.add(Pulse("p"))
        p.inputs["until"] = until
        c.connect("a", v.p, p.a)
        c.ground(v.n, p.b)
        return Simulation(c, dt=1e-3).run(0.2).limits[0]
    assert not run(0.030).violated
    assert run(0.100).violated


def test_thermal_model_settles_at_p_times_rth():
    c = Circuit("t", ambient=25.0)
    v = c.add(VoltageSource("v", v=10))
    r = c.add(Resistor("r", r=100, thermal=Thermal(rth=50, cth=0.01, t_max=60)))
    c.connect("a", v.p, r.a)
    c.ground(v.n, r.b)
    res = Simulation(c, dt=0.01).run(5.0)
    assert res.final("r.T") == pytest.approx(25 + 1.0 * 50, rel=1e-3)
    assert any(l.signal == "T" and l.violated for l in res.limits)


def test_pin_absolute_maximum_is_watched():
    class Chip(Component):
        vdd = Port("power_in", v_abs_max=3.6)
        gnd = Port("ground")

        def stamp(self, s):
            s.G(self.vdd, self.gnd, 1e-3)

    c = Circuit("abs")
    v = c.add(VoltageSource("v", v=5))
    u = c.add(Chip("u"))
    c.connect("a", v.p, u.vdd)
    c.ground(v.n, u.gnd)
    res = Simulation(c).run(0.01)
    assert any(l.signal == "v(vdd)" and l.violated for l in res.limits)


def test_requirements_and_report():
    def build():
        c = divider()
        c.require("mid is half the input", lambda r: abs(r.final("r2.v") - 5) < 1e-6)
        return c
    rep = evaluate(build, [Scenario("s", 0.01, require=[within("r2.v", 0, 4)])])
    assert not rep.ok
    reqs = {r.name: ok for r, ok, _ in rep.scenarios[0].requirements}
    assert reqs["mid is half the input"] is True
    assert reqs["r2.v stays within 0..4"] is False
    assert "FAIL" in rep.text()
    assert rep.to_dict()["failures"] == rep.failures


def test_assumptions_are_collected():
    c = Circuit("a")
    v = c.add(VoltageSource("v", v=5))
    r = c.add(Resistor("r", r=assumed(10.0, "a guess")))
    c.connect("a", v.p, r.a)
    c.ground(v.n, r.b)
    assert c.assumptions() == [("r", "r", 10.0, "a guess")]


# ----------------------------------------------------------------------- rules
def test_rules_catch_bad_wiring():
    c = Circuit("bad")
    a = c.add(VoltageSource("a", v=5))
    b = c.add(VoltageSource("b", v=3.3))
    r = c.add(Resistor("r", r=10))
    c.connect("rail", a.p, b.p, r.a)
    c.ground(a.n, b.n)
    rules = {f.rule for f in erc.check(c)}
    assert "supply conflict" in rules
    assert "unconnected pin" in rules


def test_rule_logic_levels():
    class Out5(Component):
        o = Port("digital_out", voh=5.0)

        def stamp(self, s):
            pass

    class In3(Component):
        i = Port("digital_in", vih=2.0, v_abs_max=3.6)

        def stamp(self, s):
            pass

    c = Circuit("lvl")
    c.connect("sig", c.add(Out5("u1")).o, c.add(In3("u2")).i)
    assert "overvoltage on input" in {f.rule for f in erc.check(c)}


def test_unknown_parameter_is_rejected_with_a_useful_message():
    with pytest.raises(TypeError, match="has no parameter"):
        Resistor("r", r=1, ohms=2)
    with pytest.raises(TypeError, match="needs 'r'"):
        Resistor("r")
    with pytest.raises(ValueError, match="below the minimum"):
        Resistor("r", r=-1)


# ---------------------------------------------------------------------- faults
def test_fmea_finds_the_single_point_of_failure():
    def build():
        c = Circuit("redundant")
        v = c.add(VoltageSource("v", v=10))
        a = c.add(Resistor("feed_a", r=1.0))
        b = c.add(Resistor("feed_b", r=1.0))
        s = c.add(Resistor("series", r=0.1))
        ld = c.add(Resistor("load", r=100.0))
        c.connect("in", v.p, a.a, b.a)
        c.connect("mid", a.b, b.b, s.a)
        c.connect("out", s.b, ld.a)
        c.ground(v.n, ld.b)
        c.require("load powered", lambda r: r.final("load.v") > 9.0)
        return c
    table = fmea(build, Scenario("on", 0.01),
                 [open_circuit("feed_a"), open_circuit("feed_b"),
                  open_circuit("series")])
    effect = {r.fault: bool(r.failed_requirements) for r in table.rows}
    assert effect == {"feed_a open": False, "feed_b open": False,
                      "series open": True}
