"""The JetBot twin: the board, the dynamics, and the tooling around them."""
import importlib.util
import math
import xml.dom.minidom
from pathlib import Path

import pytest

from elecsim import erc
from jetbot_twin import calibrate, compare, electrical, export, synthetic
from jetbot_twin.experiments import EXPERIMENTS, predict_manual, run_on_twin
from jetbot_twin.hardware import DEFAULT
from jetbot_twin.twin import JetBotTwin, Log

ROOT = Path(__file__).resolve().parents[1]


def load_agent():
    spec = importlib.util.spec_from_file_location("agent", ROOT / "robot" / "agent.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ the board
@pytest.mark.parametrize("board", ["drv8870", "tb6612"])
@pytest.mark.parametrize("fidelity", ["average", "switching"])
def test_board_passes_its_design_rules(board, fidelity):
    c = electrical.build(board=board, fidelity=fidelity)
    assert [f for f in erc.check(c) if f.severity == "error"] == []


def test_five_volt_set_point_comes_from_the_feedback_divider():
    assert DEFAULT.buck_v_set == pytest.approx(5.118, abs=1e-3)


def test_pwm_frequency_is_what_the_prescaler_can_make():
    c = electrical.build()
    assert c["pca9685"].prescale == 3
    assert c["pca9685"].freq == pytest.approx(1525.9, abs=0.1)
    assert DEFAULT.pwm_hz == pytest.approx(c["pca9685"].freq)


def test_motor_write_is_the_register_sequence_of_jetbot_motor():
    c = electrical.build()
    pca = c["pca9685"]
    assert electrical.write_motor(c, "left", 0.3) == 76          # int(255 * 0.3)
    assert (pca.on[8], pca.off[8]) == (0, 76 * 16)               # MotorHAT PWM pin
    assert (pca.on[1], pca.off[1]) == (0, 76 * 16)               # Waveshare IN pin
    assert pca.off[0] == 0
    electrical.write_motor(c, "left", -1.0)
    assert pca.off[0] == 255 * 16 and pca.off[1] == 0
    assert pca.duty(0) == pytest.approx(4080 / 4096)             # never quite 1


def test_twin_and_agent_write_identical_registers():
    """What the twin simulates is what the robot would be sent."""
    agent = load_agent()

    class Recorder:
        def __init__(self):
            self.reg = {}

        def set_pwm(self, ch, on, off):
            self.reg[ch] = (on, off)

    # ... and the agent must put each count in its own register: the real
    # chip ignores a block write unless auto-increment is on.
    class Bus:
        def __init__(self):
            self.reg = {}

        def read_byte_data(self, addr, r):
            return self.reg.get(r, 0)

        def write_byte_data(self, addr, r, v):
            self.reg[r] = v

        def write_i2c_block_data(self, *a):
            raise AssertionError("block write to the PCA9685")

    bus = Bus()
    pca = agent.Pca9685(bus)
    pca.set_pwm(8, 0, 1216)
    assert pca.get_pwm(8) == (0, 1216)
    assert pca.prescale == 3

    for side in ("left", "right"):
        for value in (-1.0, -0.37, 0.0, 0.05, 0.3, 0.999, 1.0):
            rec = Recorder()
            agent.write_motor(rec, side, value)
            c = electrical.build()
            electrical.write_motor(c, side, value)
            pca = c["pca9685"]
            for ch, (on, off) in rec.reg.items():
                assert (pca.on[ch], pca.off[ch]) == (on, off), (side, value, ch)


def test_ina219_reads_the_charger_not_the_load():
    tw = JetBotTwin()
    tw.robot.set_motors(0.6, 0.6)
    tw.sleep(1.0)
    t = tw.telemetry()
    assert t["i_pack"] > 0.3
    assert abs(t["charge_i"]) < 0.01          # unplugged: it sees nothing
    tw.plug_charger()
    tw.robot.stop()
    tw.sleep(1.0)
    assert tw.telemetry()["charge_i"] > 0.05


# --------------------------------------------------------------- the dynamics
@pytest.mark.parametrize("board", ["drv8870", "tb6612"])
def test_positive_command_drives_forward_and_straight(board):
    log = JetBotTwin(board=board).run([(2.0, 0.5, 0.5)])
    assert log.final("x") > 0.3
    assert abs(log.final("y")) < 1e-6 and abs(log.final("th")) < 1e-6


def test_steady_rolling_is_nearly_slip_free():
    tw = JetBotTwin()
    tw.run([(2.5, 0.5, 0.5)])
    s = tw.dyn.s
    assert s.v == pytest.approx(DEFAULT.wheel_radius * s.wl, rel=0.02)


def test_spin_turns_on_the_spot_at_the_kinematic_rate():
    tw = JetBotTwin()
    tw.robot.set_motors(-0.3, 0.3)
    tw.sleep(2.0)
    s = tw.dyn.s
    assert s.w > 1.0
    assert s.w == pytest.approx(tw.dyn.kinematic_twist()[1], rel=0.05)
    assert math.hypot(s.x, s.y) < 0.05


def test_a_fast_spin_orbits_because_the_mass_is_behind_the_axle():
    """The pivot leaves the middle of the axle as the rate rises."""
    def pivot(cmd):
        tw = JetBotTwin()
        tw.robot.set_motors(-cmd, cmd)
        tw.sleep(2.0)
        return abs(tw.dyn.s.v / tw.dyn.s.w)
    assert pivot(0.5) > 2 * pivot(0.3)


def test_full_throttle_launch_spins_the_wheels():
    log = JetBotTwin().run([(0.4, 1.0, 1.0)])
    assert abs(log["slip_l"]).max() > 0.2


def test_load_makes_the_bus_sag_and_uses_charge():
    log = JetBotTwin().run([(0.5, 0, 0), (2.0, 0.8, 0.8)])
    assert log.at("bus_v", 0.4) - log["bus_v"].min() > 0.1
    assert log.final("soc") < log["soc"][0]


def test_the_two_chips_have_different_dead_bands():
    """The headline prediction: see docs/ELECTRICAL.md."""
    def moves(board, cmd):
        return JetBotTwin(board=board).run([(1.5, cmd, cmd)]).final("x") > 0.01
    assert moves("tb6612", 0.15)
    assert not moves("drv8870", 0.15)
    assert moves("drv8870", 0.5)


def test_held_wheels_draw_stall_current_and_go_nowhere():
    tw = JetBotTwin(board="drv8870")
    tw.dyn.held = True
    log = tw.run([(1.0, 0.5, 0.5)])
    assert log.final("x") == 0
    assert log.final("i_l") > 0.4          # coast decay: well under d V / R


def test_a_motor_coming_unplugged_turns_the_robot():
    tw = JetBotTwin()
    tw.circuit["drive_left"].fault = "open"
    log = tw.run([(1.5, 0.5, 0.5)])
    assert log.final("th") > 0.5              # only the right wheel drives


def test_step_size_does_not_change_the_answer():
    exp = EXPERIMENTS["straight"]
    a = run_on_twin(exp, dt=5e-4).final("x")
    b = run_on_twin(exp, dt=2e-3).final("x")
    assert b == pytest.approx(a, rel=0.01)


# ---------------------------------------------------------------- the tooling
def test_log_survives_a_round_trip_through_csv(tmp_path):
    log = JetBotTwin().run([(0.2, 0.5, 0.5)])
    log.meta["experiment"] = "x"
    log.write_csv(tmp_path / "a.csv")
    back = Log.read_csv(tmp_path / "a.csv")
    assert back.meta["experiment"] == "x"
    assert back["bus_v"] == pytest.approx(log["bus_v"], rel=1e-5)


def test_experiments_serialise_for_the_agent():
    import json
    for exp in EXPERIMENTS.values():
        d = json.loads(exp.to_json())
        assert sum(p[0] for p in d["profile"]) == pytest.approx(exp.duration)
        assert exp.duration <= 180                  # the agent's own limit


def test_calibration_recovers_a_known_pack_resistance():
    """The stand-in's pack resistance is 0.085 ohm per cell; start from 0.060."""
    exp = EXPERIMENTS["jetson_load"]
    real, manual = synthetic.observe(exp)
    assert real.meta["source"] == "synthetic"
    start = DEFAULT.with_(r_5v_path=synthetic.TRUTH["r_5v_path"])
    _, info = calibrate.fit(start, ["cell_r0"], [calibrate.Case(exp, real, manual)])
    assert info["after"][0] == pytest.approx(synthetic.TRUTH["cell_r0"], rel=0.05)
    assert info["cost_after"] < 0.05 * info["cost_before"]


def test_state_of_charge_is_recovered_from_the_resting_voltage():
    hw = DEFAULT.with_(soc0=0.6)
    tw = JetBotTwin(hw)
    tw.sleep(0.5)
    v = tw.telemetry()["bus_v"]
    assert compare.estimate_soc(DEFAULT, v, DEFAULT.jetson_p_idle) == pytest.approx(0.6, abs=0.02)


def test_exports_are_well_formed_and_carry_the_twins_mass():
    for text in (export.urdf(DEFAULT, meshes=False), export.sdf(DEFAULT, meshes=False)):
        xml.dom.minidom.parseString(text)
    doc = xml.dom.minidom.parseString(export.urdf(DEFAULT, meshes=False))
    total = sum(float(m.getAttribute("value")) for m in doc.getElementsByTagName("mass"))
    assert total == pytest.approx(DEFAULT.mass + 2 * 0.006, rel=1e-3)   # + caster links
