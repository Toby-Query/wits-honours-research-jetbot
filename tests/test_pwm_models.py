"""The averaged drive must agree with the switching bridge it stands in for."""
import numpy as np
import pytest

from elecsim import Circuit, Simulation
from elecsim.lib import DCMotor, HBridge, PwmController, PwmMotorDrive, VoltageSource
from elecsim.pwm import steady_state

R, L, KE, V, RON, VF = 4.0, 2e-3, 0.2, 12.0, 0.56, 0.8


def switching(duty, omega, logic):
    """Run the four-switch model to periodic steady state; return averages."""
    c = Circuit("sw")
    src = c.add(VoltageSource("src", v=V))
    pca = c.add(PwmController("pca", resolve="switching"))
    pca.set_pwm_freq(1600)
    br = c.add(HBridge("br", r_hs=RON / 2, r_ls=RON / 2, vf=VF, logic=logic))
    m = c.add(DCMotor("m", r=R, l=L, ke=KE))
    c.connect("VM", src.p, br.vm, pca.vdd)
    c.ground(src.n, br.gnd, pca.gnd)
    c.connect("A", br.out1, m.a)
    c.connect("B", br.out2, m.b)
    c.connect("IN1", pca.led0, br.in1)
    c.connect("IN2", pca.led1, br.in2)
    counts = int(round(duty * 4096))
    if logic == "in_in":                       # drive / coast
        pca.set_pwm(0, 0, counts)
        pca.set_pwm(1, 0, 4096)
    else:                                      # drive / brake
        c.connect("PWM", pca.led2, br.pwm)
        pca.set_pwm(0, 4096, 0)
        pca.set_pwm(1, 0, 4096)
        pca.set_pwm(2, 0, counts)
    m.inputs["omega"] = omega
    period = 1.0 / pca.freq
    sim = Simulation(c, dt=period / 400)
    sim.run(12 * period, record=["m.i"])                   # settle
    res = sim.run(4 * period, record=["m.i", "br.i_supply"])
    return res.mean("m.i"), res.mean("br.i_supply"), pca.freq, counts / 4096


@pytest.mark.parametrize("logic,decay", [("in_in", "coast"),
                                         ("in_in_pwm", "brake")])
@pytest.mark.parametrize("duty", [0.15, 0.4, 0.8])
@pytest.mark.parametrize("omega", [0.0, 20.0])
def test_average_matches_switching(logic, decay, duty, omega):
    i_m, i_s, f, d = switching(duty, omega, logic)
    ss = steady_state(V, KE * omega, R, L, 1 / f, d, decay, RON, VF)
    tol = 0.03 * max(abs(i_m), 0.1)
    assert ss["i_avg"] == pytest.approx(i_m, abs=tol)
    assert ss["i_supply"] == pytest.approx(i_s, abs=tol)


def test_brake_decay_is_linear_in_duty():
    for d in (0.2, 0.5, 0.9):
        ss = steady_state(V, 1.0, R, L, 1 / 1526, d, "brake", RON)
        assert ss["i_avg"] == pytest.approx((d * V - 1.0) / (R + RON), rel=1e-9)


def test_coast_decay_runs_faster_at_light_load():
    """The same duty balances a much higher back-EMF when the bridge coasts."""
    def no_load_emf(decay):
        lo, hi = 0.0, V
        for _ in range(60):
            mid = (lo + hi) / 2
            i = steady_state(V, mid, R, L, 1 / 1526, 0.3, decay, RON, VF)["i_avg"]
            lo, hi = (mid, hi) if i > 0.05 else (lo, mid)
        return lo
    assert no_load_emf("coast") > 1.5 * no_load_emf("brake")


def test_full_duty_is_dc():
    for decay in ("coast", "brake"):
        ss = steady_state(V, 2.0, R, L, 1 / 1526, 1.0, decay, RON, VF)
        assert ss["i_avg"] == pytest.approx((V - 2.0) / (R + RON), rel=1e-6)
        assert ss["i_supply"] == pytest.approx(ss["i_avg"], rel=1e-6)


def test_averaged_drive_component_draws_the_supply_current():
    c = Circuit("avg")
    src = c.add(VoltageSource("src", v=V, r=0.3))
    pca = c.add(PwmController("pca"))
    pca.set_pwm_freq(1600)
    d = c.add(PwmMotorDrive("d", logic="in_in", r_on=RON, vf=VF,
                            f_pwm=pca.freq, r=R, l=L, ke=KE))
    c.connect("VM", src.p, d.vm, pca.vdd)
    c.ground(src.n, d.gnd, pca.gnd)
    c.connect("IN1", pca.led0, d.in1)
    c.connect("IN2", pca.led1, d.in2)
    pca.set_pwm(0, 0, 2048)
    d.inputs["omega"] = 10.0
    sim = Simulation(c, dt=1e-3)
    sim.run(0.01)
    vm = d.sig["v_m"]
    ss = steady_state(vm, KE * 10, R, L, 1 / pca.freq, 0.5, "coast", RON, VF)
    assert d.sig["i"] == pytest.approx(ss["i_avg"], rel=1e-6)
    assert src.sig["i"] == pytest.approx(ss["i_supply"] + pca.i_supply, rel=1e-4)
    assert vm < V
