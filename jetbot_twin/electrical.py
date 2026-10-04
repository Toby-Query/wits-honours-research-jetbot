"""The JetBot expansion board as a circuit, transcribed from the schematic.

    reference/JetBot_schematic.pdf   (Waveshare, 2020-02-28)

Reference designators in the comments are the schematic's. Read this file
next to the PDF and the two should agree line for line; where they do not,
the PDF is right and this is a bug.

    cells -> holder -> U1/U2/U4/U5 (protection FETs) -> EB+
    cells- -> R15 (10 mohm sense) -> GND
    DC jack -> R16 (100 mohm, read by the INA219) -> EB+
    EB+ -> S1 (slide switch) -> 12VCC
    12VCC -> U3 APW7313 buck -> 5V -> header -> Jetson Nano
    12VCC -> motor driver(s) -> motors
    Jetson 3V3 -> PCA9685, INA219, OLED, driver logic

Run it on its own as a design under test:

    python -m elecsim check jetbot_twin.electrical
"""
from __future__ import annotations

from elecsim import Circuit, Scenario, Thermal, at_least
from elecsim.lib import (BuckConverter, Capacitor, ConstantCurrentLoad, DCMotor,
                         HBridge, PackProtector, PwmController, PwmMotorDrive,
                         Resistor, ShuntMonitor, Switch, VoltageSource,
                         series_pack)

from .hardware import DEFAULT, Hardware
from .parts import JetsonNano

# PCA9685 channel -> what it drives, from the schematic.
#   TB6612FNG (TB1):  PWMA S8, AIN2 S9, AIN1 S10, BIN1 S11, BIN2 S12, PWMB S13
#   DRV8870 (DRV1/2): IN1/IN2 on LED0/LED1 and LED2/LED3
TB6612_PINS = {"left": dict(pwm=8, in2=9, in1=10),
               "right": dict(pwm=13, in2=12, in1=11)}
DRV8870_PINS = {"left": dict(in1=1, in2=0), "right": dict(in1=2, in2=3)}


def build(hw: Hardware = None, fidelity="average", standalone=True, **overrides):
    """Build the circuit.

    fidelity     "average": each motor and its bridge is one period-averaged
                 element; millisecond steps. "switching": real H-bridges and
                 PWM edges; needs steps of a few microseconds.
    standalone   give each motor its own shaft (half the robot's mass
                 reflected to the wheel) so the circuit can be exercised
                 without the vehicle model. The twin passes False and drives
                 the shaft speeds itself.
    overrides    any field of Hardware, e.g. build(board="tb6612")
    """
    hw = (hw or DEFAULT)
    if overrides:
        hw = hw.with_(**overrides)
    c = Circuit(f"JetBot expansion board ({hw.board})", ambient=hw.ambient)
    c.hw = hw

    # ------------------------------------------------------------ the pack
    cells, taps = series_pack(
        c, "cell", hw.cells, "PACK-", "PACK+",
        capacity_ah=hw.cell_capacity_ah, soc=hw.soc0, r0=hw.cell_r0,
        r1=hw.cell_r1, c1=hw.cell_c1, i_max=hw.cell_i_max,
        v_min=2.5, v_max=4.25)
    for cell in cells:                    # a continuous rating, not a peak one
        for lim in cell.extra_limits:
            if lim.signal == "i":
                lim.for_s = 5.0
    holder = c.add(Resistor("holder", r=hw.holder_r,
                            note="BAT1: the six spring contacts, lumped"))
    c.connect("PACK+", holder.a)

    # U6 S-8254AA driving U1/U4 (charge) and U2/U5 (discharge) AO4407A pairs
    prot = c.add(PackProtector(
        "protector", cells=hw.cells, r_on=hw.prot_r_on, i_oc=hw.prot_i_oc,
        v_uv=hw.prot_v_uv, v_uv_release=hw.prot_v_uv_rel,
        v_ov=hw.prot_v_ov, v_ov_release=hw.prot_v_ov_rel,
        note="U6 S-8254AA with U1, U2, U4, U5 AO4407A"))
    c.connect("BAT_H", holder.b, prot.bat)
    c.connect("EB+", prot.out)
    c.connect("PACK-", prot.vss)
    for k, tap in enumerate(taps, 1):
        c.connect(tap, getattr(prot, f"tap{k}"))

    r15 = c.add(Resistor("R15", r=hw.r_sense, p_max=hw.r_2512_p_max,
                         note="overcurrent sense, pack negative to ground"))
    c.connect("PACK-", r15.a)
    c.ground(r15.b)

    # ---------------------------------------------------------- the charger
    charger = c.add(VoltageSource(
        "charger", v=hw.charger_v, i_limit=hw.charger_i, enabled=False,
        note="CN1 DC jack and the wall supply; unplugged unless a scenario "
             "sets inputs['enabled']"))
    r16 = c.add(Resistor("R16", r=hw.r_shunt_charge, p_max=hw.r_2512_p_max,
                         note="charge-current shunt, read by the INA219"))
    c.connect("JACK", charger.p, r16.a)
    c.connect("EB+", r16.b)
    c.ground(charger.n)

    ina = c.add(ShuntMonitor(
        "ina219", r_shunt=hw.r_shunt_charge, address=0x41,
        note="A0 = 3V3, A1 = GND -> 0x41. It is across R16: it reads charger "
             "current and the EB+ voltage, NOT what the robot draws"))
    c.connect("JACK", ina.in_p)
    c.connect("EB+", ina.in_n)

    # ------------------------------------------------------- switch and rail
    s1 = c.add(Switch("S1", r_on=hw.switch_r, i_max=hw.switch_i_max,
                      note="SS-12D06 slide switch: everything flows through it"))
    for lim in s1.extra_limits:           # the rating is itself a guess
        lim.severity = "warn"
    c.connect("EB+", s1.a)
    c.connect("12VCC", s1.b)
    bulk = c.add(Capacitor("C_bulk", c=hw.c_bulk, esr=0.1, v_max=16.0,
                           note="C2 and the driver bulk capacitors"))
    c.connect("12VCC", bulk.a)
    c.ground(bulk.b)

    # ------------------------------------------------------------ 5 V supply
    buck = c.add(BuckConverter(
        "U3", v_set=hw.buck_v_set, i_limit=hw.buck_i_limit,
        i_rated=hw.buck_i_rated, rds_on=hw.buck_rds, dcr=hw.buck_dcr,
        f_sw=hw.buck_f_sw, v_in_max=hw.buck_v_in_max,
        thermal=Thermal(hw.buck_rth, cth=0.3, t_max=125.0),
        note="APW7313, R3 68k / R7 15k"))
    c.connect("12VCC", buck.vin)
    c.connect("5V", buck.out)
    c.ground(buck.gnd)
    c5 = c.add(Capacitor("C_5V", c=120e-6, esr=0.05, v_max=6.3,
                         note="C4, C5, C6"))
    c.connect("5V", c5.a)
    c.ground(c5.b)

    jetson = c.add(JetsonNano(
        "jetson", power=hw.jetson_p_idle, v_min=hw.jetson_v_min,
        v_max=hw.jetson_v_max, i_header_max=hw.jetson_i_header_max,
        note="Header1: 5V on two pins, 3V3 back out"))
    path = c.add(Resistor(
        "header", r=max(hw.r_5v_path, 1e-4),
        note="5 V path from the regulator to where the Jetson measures it: "
             "header pins, carrier traces, input switch"))
    c.connect("5V", path.a)
    c.connect("5V_JETSON", path.b, jetson.v5)
    c.connect("3V3", jetson.v3v3)
    c.ground(jetson.gnd)

    # ------------------------------------------------------------ 3.3 V logic
    pca = c.add(PwmController(
        "pca9685", address=0x60, resolve=fidelity,
        note="A5 high, A0-A4 low -> 0x60"))
    pca.set_pwm_freq(hw.pwm_request_hz)
    oled = c.add(ConstantCurrentLoad("oled", i=hw.oled_i,
                                     note="U8 SSD1306 128x32"))
    c.connect("3V3", pca.vdd, ina.vs, oled.p)
    c.ground(pca.gnd, ina.gnd, oled.n)
    c.connect("SDA", jetson.sda, pca.sda, ina.sda)
    c.connect("SCL", jetson.scl, pca.scl, ina.scl)

    # -------------------------------------------------------------- the drive
    d = hw.driver
    motor = dict(r=hw.motor_r, l=hw.motor_l, ke=hw.motor_ke, gear=hw.gear,
                 gear_eff=hw.gear_eff)
    shaft = dict(inertia=hw.drive_inertia_equiv, tau_coulomb=hw.motor_tau_coulomb,
                 tau_static=hw.motor_tau_static, viscous=hw.motor_viscous) \
        if standalone else {}
    pins = DRV8870_PINS if hw.board == "drv8870" else TB6612_PINS
    for side in ("left", "right"):
        ch = pins[side]
        th = Thermal(d["rth"], cth=0.15, t_max=150.0)
        if fidelity == "average":
            drv = c.add(PwmMotorDrive(
                f"drive_{side}", logic=d["logic"], r_on=d["r_on"], vf=d["vf"],
                f_pwm=pca.freq, i_cont=d["i_cont"], i_peak=d["i_peak"],
                v_min=d["v_min"], v_max=d["v_max"],
                i_motor_max=None, v_motor_max=hw.motor_v_rated,
                polarity=d["polarity"], thermal=th, **motor, **shaft))
            for lim in drv.extra_limits:            # an over-volted motor wears
                if lim.signal == "v_eff":           # out; it does not fail today
                    lim.severity = "warn"
            c.connect("12VCC", drv.vm)
            c.ground(drv.gnd)
            bridge = drv
        elif fidelity == "switching":
            bridge = c.add(HBridge(
                f"bridge_{side}", r_hs=d["r_on"] / 2, r_ls=d["r_on"] / 2,
                vf=d["vf"], logic=d["logic"], i_cont=d["i_cont"],
                i_peak=d["i_peak"], v_min=d["v_min"], v_max=d["v_max"],
                thermal=th))
            m = c.add(DCMotor(f"motor_{side}", **motor, **shaft))
            c.connect("12VCC", bridge.vm)
            c.ground(bridge.gnd)
            # The motor leads go on whichever way round makes a positive
            # command drive forward; that differs between the two chips.
            a, b = (m.a, m.b) if d["polarity"] > 0 else (m.b, m.a)
            c.connect(f"M_{side}_1", bridge.out1, a)
            c.connect(f"M_{side}_2", bridge.out2, b)
        else:
            raise ValueError("fidelity must be 'average' or 'switching'")
        c.connect(f"{side}_IN1", getattr(pca, f"led{ch['in1']}"), bridge.in1)
        c.connect(f"{side}_IN2", getattr(pca, f"led{ch['in2']}"), bridge.in2)
        if "pwm" in ch:
            c.connect(f"{side}_PWM", getattr(pca, f"led{ch['pwm']}"), bridge.pwm)

    # ---------------------------------------------- what the board is FOR
    c.require("the Jetson stays up (no brown-out)",
              lambda r: (r.max("jetson.browned_out") == 0,
                         f"5 V rail minimum {r.min('jetson.v'):.3f} V"))
    c.require("the pack protector never trips",
              lambda r: (r.max("protector.tripped") == 0,
                         f"lowest cell {r.min('protector.cell_min'):.2f} V, "
                         f"peak current {r.max('protector.i'):.2f} A"))
    c.require("5 V regulator stays in regulation",
              lambda r: r.min("U3.regulating") == 1)
    return c


# --------------------------------------------------------------------------
# driving it the way the software does
# --------------------------------------------------------------------------
def write_motor(circuit, side, value, alpha=1.0, beta=0.0):
    """What `jetbot.Motor._write_value` does to the PCA9685, register for
    register. Returns the 8-bit speed it computed.

    The real method writes BOTH sets of channels every time -- the Adafruit
    MotorHAT ones (8..13) and the Waveshare ones (0..3) -- which is why one
    piece of software drives either chip. So does this.
    """
    pca = circuit["pca9685"]
    mapped = int(255.0 * (alpha * value + beta))
    speed = min(max(abs(mapped), 0), 255)
    hat = TB6612_PINS[side]
    drv = DRV8870_PINS[side]
    pca.set_pwm(hat["pwm"], 0, speed * 16)               # setSpeed
    if mapped < 0:                                       # run(FORWARD)
        pca.set_pwm(hat["in2"], 0, 4096)
        pca.set_pwm(hat["in1"], 4096, 0)
        pca.set_pwm(drv["in1"], 0, 0)
        pca.set_pwm(drv["in2"], 0, speed * 16)
    else:                                                # run(BACKWARD)
        pca.set_pwm(hat["in1"], 0, 4096)
        pca.set_pwm(hat["in2"], 4096, 0)
        pca.set_pwm(drv["in1"], 0, speed * 16)
        pca.set_pwm(drv["in2"], 0, 0)
    return speed


def release_motor(circuit, side):
    """`Motor._release`: what happens when the notebook kernel stops."""
    pca = circuit["pca9685"]
    hat, drv = TB6612_PINS[side], DRV8870_PINS[side]
    for ch in (hat["in1"], hat["in2"]):
        pca.set_pwm(ch, 0, 4096)
    for ch in (drv["in1"], drv["in2"]):
        pca.set_pwm(ch, 0, 0)


def drive_names(circuit):
    """The two components that carry motor current, whatever the fidelity."""
    if "drive_left" in circuit:
        return "drive_left", "drive_right"
    return "motor_left", "motor_right"


# --------------------------------------------------------------------------
# scenarios: the situations the board has to survive
# --------------------------------------------------------------------------
def _cmd(value, jetson_p=None, t_on=0.2, locked=False):
    def setup(c):
        if jetson_p is not None:
            c["jetson"].inputs["p"] = jetson_p
        for n in drive_names(c):
            c[n].inputs["locked"] = locked

    def stim(t, c):
        v = value if t >= t_on else 0.0
        write_motor(c, "left", v)
        write_motor(c, "right", v)
    return setup, stim


def _mk(name, doc, value, duration=3.0, hw_kw=None, extra=None, **kw):
    setup, stim = _cmd(value, **kw)
    return Scenario(
        name, duration, dt=1e-3, setup=setup, stimulus=stim, doc=doc,
        build_args=hw_kw or {}, require=extra or [], every=5,
        metrics={
            "12 V rail, lowest": lambda r: f"{r.min('U3.v_in'):.2f} V",
            "5 V rail, lowest": lambda r: f"{r.min('jetson.v'):.3f} V",
            "pack current, peak": lambda r: f"{r.max('protector.i'):.2f} A",
            "motor current, each, rms": lambda r: f"{r.max('drive_left.i_rms'):.2f} A",
            "wheel speed, final": lambda r: f"{r.final('drive_left.omega') * 9.549:.0f} rpm",
        })


SCENARIOS = [
    _mk("idle", "Sitting still, Jetson idle.", 0.0, duration=1.0),
    _mk("cruise", "Command 0.3 on both motors, camera streaming.", 0.3,
        jetson_p=DEFAULT.jetson_p_teleop),
    _mk("full speed", "Command 1.0 from rest, model running.", 1.0,
        jetson_p=DEFAULT.jetson_p_inference),
    _mk("stall", "Both wheels held, command 1.0: driven into a wall.", 1.0,
        jetson_p=DEFAULT.jetson_p_inference, locked=True),
    _mk("stall on a flat pack", "The same, at 10 % charge.", 1.0,
        jetson_p=DEFAULT.jetson_p_inference, locked=True, hw_kw={"soc0": 0.10}),
    _mk("worst case", "Stalled, Jetson at full power, pack nearly empty.", 1.0,
        jetson_p=DEFAULT.jetson_p_max, locked=True, hw_kw={"soc0": 0.05}),
]
