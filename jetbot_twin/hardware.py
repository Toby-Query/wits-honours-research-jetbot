"""Every number the twin is built from, and where each one came from.

This is the file you edit. Nothing else in the package contains a dimension,
a rating or a mass.

Each value is tagged with how it is known:

    datasheet(x, "...")   printed by the manufacturer of the part
    measured(x, "...")    read off the manufacturer's CAD or schematic, or
                          off the real robot
    assumed(x, "...")     an engineering estimate nobody has checked yet

The assumed ones are the twin's open questions. `python -m jetbot_twin
assumptions` lists them, ranked by how much each one moves the predictions,
and docs/VALIDATION.md says which measurement on the real robot settles
which number. As you measure, change `assumed` to `measured` here.

Frame: x forward, y left, z up, origin on the floor under the middle of the
axle. SI units throughout. The STEP file's frame is different (x backward,
y up, z left, origin elsewhere); `tools/build_model.py` does the conversion.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, fields, replace

from elecsim import Q, assumed, datasheet, measured

G = 9.81


@dataclass
class MassItem:
    name: str
    mass: float                  # kg
    pos: tuple                   # centre of mass (x, y, z), m
    size: tuple = (0, 0, 0)      # bounding box, for its own inertia
    src: str = ""
    assumed: bool = False


@dataclass
class Hardware:
    # ---------------------------------------------------------------- geometry
    wheel_radius: float = measured(
        0.0315, "63 mm across the tyre, by ruler, 2026-10-04 (was assumed 65 mm). "
        "Unloaded; the loaded radius is a little less")
    track: float = measured(
        0.1149, "tyre centre to tyre centre, Jetbot STEP (wheel mid-planes at +/-57.45 mm)")
    caster_rear_x: float = measured(-0.082, "rear caster ball centre, STEP")
    caster_front_x: float = measured(0.038, "front caster ball centre, STEP")
    length: float = measured(0.1535, "overall, camera to antenna, STEP")
    width: float = measured(0.1344, "overall, wheel face to wheel face, STEP")
    height_body: float = measured(0.122, "floor to fan top, STEP")
    height_total: float = measured(0.260, "floor to antenna tips, STEP")
    chassis_box: tuple = (0.1336, 0.0956, 0.0351)        # STEP
    axle_height: float = measured(0.0336, "wheel centre above floor, STEP")

    # -------------------------------------------------------------------- mass
    # Positions from the STEP. Masses: computed from STEP volume and material
    # where that is honest (sheet metal, acrylic), otherwise typical figures
    # for the part. Weigh the robot: the total is the number that matters.
    mass_items: list = field(default_factory=lambda: [
        MassItem("chassis box", 0.132, (-0.022, 0, 0.0353), (0.134, 0.096, 0.035),
                 "48 963 mm3 of 2.70 g/cm3 aluminium, STEP"),
        MassItem("bottom plate", 0.027, (-0.022, 0, 0.0169), (0.134, 0.096, 0.002),
                 "23 000 mm3; acrylic assumed (62 g if aluminium)", True),
        MassItem("motor L", 0.036, (-0.019, 0.0407, 0.0336), (0.070, 0.030, 0.023),
                 "metal-gear TT motor, typical", True),
        MassItem("motor R", 0.036, (-0.019, -0.0407, 0.0336), (0.070, 0.030, 0.023),
                 "metal-gear TT motor, typical", True),
        MassItem("wheel L", 0.034, (0.0, 0.0575, 0.0336), (0.067, 0.026, 0.067),
                 "65 mm TT wheel with tyre, typical", True),
        MassItem("wheel R", 0.034, (0.0, -0.0575, 0.0336), (0.067, 0.026, 0.067),
                 "65 mm TT wheel with tyre, typical", True),
        MassItem("expansion board", 0.050, (-0.0248, 0, 0.0529), (0.120, 0.079, 0.012),
                 "120 x 79 mm FR4 with parts and cell holder, estimate", True),
        MassItem("3 x 18650", 0.138, (-0.0216, 0, 0.0693), (0.059, 0.078, 0.022),
                 "46 g per cell, typical; weigh yours", True),
        MassItem("Jetson Nano dev kit", 0.140, (-0.0148, 0, 0.0959), (0.100, 0.080, 0.030),
                 "module, heatsink and carrier, typical", True),
        MassItem("fan", 0.014, (-0.0128, -0.019, 0.1165), (0.040, 0.040, 0.011),
                 "40 mm fan, typical", True),
        MassItem("camera and holder", 0.015, (0.048, 0, 0.093), (0.025, 0.040, 0.055),
                 "3 791 mm3 aluminium holder + IMX219 module", True),
        MassItem("antenna L", 0.013, (-0.080, 0.039, 0.153), (0.014, 0.014, 0.195),
                 "195 mm dual-band antenna, typical", True),
        MassItem("antenna R", 0.013, (-0.080, -0.039, 0.153), (0.014, 0.014, 0.195),
                 "195 mm dual-band antenna, typical", True),
        MassItem("caster front", 0.006, (0.038, 0, 0.008), (0.02, 0.02, 0.015), "typical", True),
        MassItem("caster rear", 0.006, (-0.082, 0, 0.008), (0.02, 0.02, 0.015), "typical", True),
        MassItem("fasteners, standoffs, cables, wireless card", 0.026,
                 (-0.022, 0, 0.060), (0.10, 0.08, 0.06), "estimate", True),
    ])
    mass_override: float = None      # set this to the kitchen-scale reading, kg
    # The inertia about the vertical axis is summed from the list above, most
    # of whose masses are estimates. This multiplies it.
    yaw_inertia_scale: float = assumed(1.0, "the parts list taken at face value")

    # ------------------------------------------------------------------ ground
    mu_tyre: float = assumed(0.8, "rubber tyre on a smooth hard floor")
    slip_knee: float = assumed(0.03, "slip speed at which traction saturates, m/s")
    c_rr: float = assumed(0.015, "tyre rolling resistance coefficient")
    mu_caster: float = measured(0.324, "fitted to the spin on a tiled floor, "
                                "2026-10-04 (was assumed 0.25)")
    # The caster's resistance along the robot's heading, where the ball rolls.
    # None: the same as mu_caster, in every direction (the model robot 1 was
    # calibrated with). Set, mu_caster is the resistance across the heading
    # only (the ball scrubbed sideways, as in a spin) and this is the rolling
    # resistance along it. Robot 2's 50 straights showed one number cannot
    # serve both (results/robot2/RESULTS.md).
    mu_caster_roll: float = None

    # ------------------------------------------------------------------- motor
    # The kit's motors are "TT" gearmotors, 1:48, "idle speed 240 RPM"
    # (Waveshare). Waveshare do not say at what voltage. The figures below are
    # for the common 6 V TT motor; if yours turn out to be 12 V units, ke
    # doubles and r roughly quadruples. This is the first thing to measure.
    # 2026-10-04, wheels-up sweep filmed in slow motion (docs/RESULTS.md): the
    # measured speeds fit 255 rpm at 6 V. So these ARE ordinary 6 V motors,
    # run from a 12 V pack.
    motor_v_rated: float = measured(6.0, "wheels-up sweep 2026-10-04: a 6 V motor "
                                    "reproduces the measured speeds")
    motor_rpm_noload: float = measured(255.0, "fitted to wheel speeds from the "
                                       "slow-motion video, 2026-10-04. Waveshare "
                                       "say 240: these run 6 % faster")
    motor_i_noload: float = measured(0.197, "fitted to the wheels-up sweep, "
                                     "2026-10-04 (was assumed 0.16)")
    motor_r: float = measured(3.45, "fitted to the bus sag with the wheels held, "
                              "2026-10-04 (was assumed 4.0). Inferred through the "
                              "pack resistance, not metered")
    motor_l: float = assumed(2.0e-3, "130-size motor wound for 6 V, estimate")
    gear: float = datasheet(48.0, "Waveshare: reduction 1:48")
    gear_eff: float = measured(0.514, "fitted to the straight run and the spin on "
                               "the floor, 2026-10-04 (was assumed 0.65). It "
                               "stands for all the drag the twin has no other "
                               "name for, not only the gearbox")
    rotor_inertia: float = assumed(1.7e-7, "130-size armature, kg.m2 at the rotor")
    motor_friction_static_ratio: float = assumed(
        1.4, "breakaway friction over running friction")
    motor_coulomb_fraction: float = measured(
        0.56, "share of the no-load loss that is dry friction (rest is viscous); "
        "fitted to the wheels-up sweep, 2026-10-04 (was assumed 0.7)")
    wheel_inertia: float = assumed(1.8e-5, "34 g wheel as a ring at 0.7 r")
    # Loss that grows with the SQUARE of speed, as torque at the wheel per
    # (rad/s)^2. Zero in the twin as designed. The real wheel's speed bends
    # over near full command (docs/RESULTS.md), which nothing linear explains:
    # at 480 rpm the rotor is at 23 000 rpm, twice what the motor is made for.
    motor_drag2: float = assumed(0.0, "no speed-squared loss until measured")

    # ----------------------------------------------------------- motor drivers
    # The board has footprints for both; which is fitted decides how the
    # motor is PWMed. See docs/ELECTRICAL.md.
    # MEASURED 2026-10-04 on the robot (`agent.py identify`): driving only the
    # TB6612FNG's channels dipped the rail 204 mV; driving only the DRV8870
    # channels did nothing. This board has an INA219 AND a TB6612FNG.
    board: str = "tb6612"            # "drv8870" | "tb6612"
    pwm_request_hz: float = datasheet(1600.0, "Adafruit_MotorHAT default")
    drv8870_r_on: float = datasheet(0.565, "DRV8870: HS + LS, typical at 25 C")
    drv8870_i_peak: float = datasheet(3.6, "DRV8870: peak / overcurrent trip")
    drv8870_i_cont: float = assumed(2.0, "DRV8870: thermally limited, board dependent")
    drv8870_v_min: float = datasheet(6.5, "DRV8870: minimum VM")
    drv8870_v_max: float = datasheet(45.0, "DRV8870: maximum VM")
    drv8870_vf: float = assumed(0.8, "body diode at ~1 A")
    drv8870_rth: float = datasheet(41.0, "DRV8870 HSOP: junction to ambient, C/W")
    tb6612_r_on: float = datasheet(0.5, "TB6612FNG: upper + lower, typical")
    tb6612_i_cont: float = datasheet(1.2, "TB6612FNG: average output current per channel")
    tb6612_i_peak: float = datasheet(3.2, "TB6612FNG: peak, single 10 ms pulse")
    tb6612_v_max: float = datasheet(15.0, "TB6612FNG: absolute maximum VM")
    tb6612_vf: float = assumed(1.0, "body diode")
    tb6612_rth: float = assumed(110.0, "SSOP24 on a board, C/W")

    # ----------------------------------------------------------------- battery
    cells: int = 3
    cell_capacity_ah: float = assumed(2.6, "the kit ships without cells; a common 18650")
    cell_r0: float = measured(0.0575, "Jetson load step on a charged pack, "
                              "2026-10-04. It was 0.125 on a nearly flat pack: "
                              "this number rises as the cells empty, and it "
                              "includes whatever part of holder_r is not there")
    cell_r1: float = assumed(0.030, "slow polarisation")
    cell_c1: float = assumed(1500.0, "gives the sag a ~45 s tail")
    cell_i_max: float = assumed(5.2, "2C continuous")
    holder_r: float = assumed(0.12, "six spring contacts at ~20 mohm each")
    soc0: float = 1.0

    # -------------------------------------------------- protection and sensing
    prot_r_on: float = datasheet(
        0.013, "AO4407A 13 mohm max at Vgs -10 V; two in parallel each way, in series")
    prot_i_oc: float = assumed(
        20.0, "S-8254AA variant unknown: 0.20 V across R15 (10 mohm)")
    prot_v_uv: float = assumed(2.40, "S-8254AA variant unknown")
    prot_v_uv_rel: float = assumed(3.00, "S-8254AA variant unknown")
    prot_v_ov: float = assumed(4.35, "S-8254AA variant unknown")
    prot_v_ov_rel: float = assumed(4.15, "S-8254AA variant unknown")
    r_sense: float = datasheet(0.010, "R15, 10 mohm 1% 2512, schematic")
    r_shunt_charge: float = datasheet(0.100, "R16, 100 mohm 1% 2512, schematic")
    r_2512_p_max: float = datasheet(1.0, "2512 chip resistor")
    switch_r: float = assumed(0.03, "slide switch contact resistance")
    switch_i_max: float = assumed(
        0.3, "the schematic names SS-12D06, commonly rated 0.3 A at 50 V DC "
        "(some vendors say 2 A). Read the part actually on the board")
    charger_v: float = assumed(12.6, "3S CC/CV charger")
    charger_i: float = assumed(2.0, "Waveshare supply")
    c_bulk: float = datasheet(330e-6, "C2 + C23/C30 + C34: 100 uF each on 12VCC, schematic")

    # --------------------------------------------------------------- regulator
    buck_v_set: float = datasheet(
        0.925 * (1 + 68.0 / 15.0), "APW7313 with R3 68k / R7 15k, schematic: 5.118 V")
    buck_i_rated: float = datasheet(3.0, "APW7313")
    buck_i_limit: float = assumed(4.0, "APW7313 current limit, typical")
    buck_rds: float = datasheet(0.110, "APW7313 integrated MOSFETs")
    buck_dcr: float = assumed(0.040, "L1, 4.7 uH / 3 A")
    buck_f_sw: float = datasheet(340e3, "APW7313")
    buck_v_in_max: float = datasheet(24.0, "APW7313")
    buck_rth: float = assumed(60.0, "SOP-8P on this board, C/W")
    # Everything between the regulator's output and the point where the
    # Jetson measures its own supply: header pins, carrier traces, the
    # carrier's input switch. The schematic shows none of it, and the twin
    # was built without it. See docs/RESULTS.md.
    r_5v_path: float = measured(0.49, "Jetson load step, 2026-10-04: the Jetson's "
                                "own reading of its supply against its own current")

    # ------------------------------------------------------------------ Jetson
    jetson_v_min: float = datasheet(4.75, "Jetson Nano dev kit: 5 V +/- 5 %")
    jetson_v_max: float = datasheet(5.25, "Jetson Nano dev kit: 5 V +/- 5 %")
    jetson_i_header_max: float = datasheet(5.0, "J41 pins 2 and 4, 2.5 A each")
    # What the whole 5 V rail draws, by what the robot is doing. Read these
    # off the Jetson's own INA3221 (robot/agent.py logs it) and replace them.
    jetson_p_idle: float = measured(1.15, "the Jetson's own monitor at rest, 2026-10-04")
    jetson_p_teleop: float = assumed(5.0, "camera streaming to a notebook, W")
    jetson_p_inference: float = assumed(7.5, "collision-avoidance model running, W")
    jetson_p_max: float = assumed(11.0, "MAXN, CPU and GPU loaded, peripherals, W")
    oled_i: float = assumed(0.015, "SSD1306 128x32, typical")

    # ------------------------------------------------------------------ camera
    cam_res: tuple = (3280, 2464)                    # IMX219
    cam_f_mm: float = datasheet(3.15, "IMX219-160")
    cam_fov_diag_deg: float = datasheet(160.0, "IMX219-160")
    cam_pixel_um: float = datasheet(1.12, "IMX219")
    cam_pos: tuple = (0.0746, 0.0, 0.0967)           # lens centre with the mount level
    cam_pitch_deg: float = assumed(
        0.0, "the mount is hinged (photographed 2026-10-04), so this is whatever "
        "it was last set to; Waveshare's CAD has a fixed bracket at -20 degrees. "
        "Measure it. Also set CAM_PITCH_DEG in tools/build_model.py")

    ambient: float = 25.0

    # ======================================================================
    # derived
    # ======================================================================
    def with_(self, **kw):
        return replace(self, **kw)

    # -- mass properties ------------------------------------------------------
    @property
    def mass_sum(self):
        return sum(i.mass for i in self.mass_items)

    @property
    def mass(self):
        return self.mass_override if self.mass_override else self.mass_sum

    @property
    def _scale(self):
        return self.mass / self.mass_sum

    @property
    def cog(self):
        m = self.mass_sum
        return tuple(sum(i.mass * i.pos[k] for i in self.mass_items) / m
                     for k in range(3))

    @property
    def yaw_inertia_cog(self):
        """About the vertical axis through the centre of mass, kg.m2."""
        cx, cy, _ = self.cog
        J = 0.0
        for i in self.mass_items:
            a, b = i.size[0], i.size[1]
            J += i.mass * ((a * a + b * b) / 12.0
                           + (i.pos[0] - cx) ** 2 + (i.pos[1] - cy) ** 2)
        return J * self._scale * self.yaw_inertia_scale

    # -- motor ------------------------------------------------------------------
    @property
    def motor_w_noload(self):
        """Output shaft no-load speed at rated voltage, rad/s."""
        return self.motor_rpm_noload * 2 * math.pi / 60.0

    @property
    def motor_ke(self):
        """Back-EMF constant at the rotor, V.s/rad (= kt, N.m/A)."""
        return ((self.motor_v_rated - self.motor_i_noload * self.motor_r)
                / (self.motor_w_noload * self.gear))

    @property
    def motor_kt_out(self):
        """Torque per amp at the wheel, after gearbox losses."""
        return self.motor_ke * self.gear * self.gear_eff

    @property
    def motor_loss_noload(self):
        """Friction torque at the output at no-load speed, N.m."""
        return self.motor_kt_out * self.motor_i_noload

    @property
    def motor_tau_coulomb(self):
        return self.motor_coulomb_fraction * self.motor_loss_noload

    @property
    def motor_tau_static(self):
        return self.motor_friction_static_ratio * self.motor_tau_coulomb

    @property
    def motor_viscous(self):
        return ((1 - self.motor_coulomb_fraction) * self.motor_loss_noload
                / self.motor_w_noload)

    @property
    def wheel_assembly_inertia(self):
        """Wheel plus rotor reflected through the gearbox, kg.m2."""
        return self.wheel_inertia + self.rotor_inertia * self.gear ** 2

    @property
    def drive_inertia_equiv(self):
        """What one motor feels driving half the robot in a straight line."""
        return self.wheel_assembly_inertia + 0.5 * self.mass * self.wheel_radius ** 2

    # -- driver, by which chip is fitted ---------------------------------------
    @property
    def pwm_hz(self):
        """What the PCA9685 actually produces for the requested frequency."""
        prescale = math.floor(25e6 / 4096.0 / (self.pwm_request_hz * 0.9) - 1.0 + 0.5)
        return 25e6 / (4096.0 * (prescale + 1))

    @property
    def driver(self):
        if self.board == "drv8870":
            return dict(logic="in_in", r_on=self.drv8870_r_on, vf=self.drv8870_vf,
                        i_cont=self.drv8870_i_cont, i_peak=self.drv8870_i_peak,
                        v_min=self.drv8870_v_min, v_max=self.drv8870_v_max,
                        rth=self.drv8870_rth, decay="coast", polarity=1)
        if self.board == "tb6612":
            return dict(logic="in_in_pwm", r_on=self.tb6612_r_on, vf=self.tb6612_vf,
                        i_cont=self.tb6612_i_cont, i_peak=self.tb6612_i_peak,
                        v_min=2.5, v_max=self.tb6612_v_max,
                        rth=self.tb6612_rth, decay="brake", polarity=-1)
        raise ValueError(f"board must be 'drv8870' or 'tb6612', not {self.board!r}")

    # -- pack -------------------------------------------------------------------
    @property
    def pack_r_total(self):
        """Everything in series between the cells' chemistry and 12VCC."""
        return (self.cells * self.cell_r0 + self.holder_r + self.prot_r_on
                + self.r_sense + self.switch_r)

    # -- camera -----------------------------------------------------------------
    @property
    def cam_k_equidistant(self):
        """Pixels per radian for an equidistant fisheye filling the diagonal."""
        w, h = self.cam_res
        return 0.5 * math.hypot(w, h) / math.radians(self.cam_fov_diag_deg / 2)

    # -- bookkeeping --------------------------------------------------------------
    def numbers(self):
        """Every scalar field: {name: value}. What sweeps and fits operate on."""
        return {f.name: getattr(self, f.name) for f in fields(self)
                if isinstance(getattr(self, f.name), (int, float))
                and not isinstance(getattr(self, f.name), bool)}

    def assumptions(self):
        out = [(f.name, float(getattr(self, f.name)), getattr(self, f.name).src)
               for f in fields(self)
               if isinstance(getattr(self, f.name), Q) and getattr(self, f.name).assumed]
        out += [(f"mass: {i.name}", i.mass, i.src) for i in self.mass_items if i.assumed]
        return out

    def summary(self):
        cx, cy, cz = self.cog
        d = self.driver
        return "\n".join([
            f"mass                {self.mass * 1e3:7.0f} g"
            + ("  (weighed)" if self.mass_override else "  (sum of parts, unweighed)"),
            f"centre of mass      {cx * 1e3:+7.1f} mm ahead of the axle, "
            f"{cz * 1e3:.1f} mm up",
            f"yaw inertia         {self.yaw_inertia_cog * 1e6:7.0f} kg.mm2 about the CoG",
            f"wheel               {self.wheel_radius * 2e3:7.1f} mm, track "
            f"{self.track * 1e3:.1f} mm",
            f"motor               ke {self.motor_ke * 1e3:.3f} mV.s/rad at the rotor, "
            f"{self.motor_kt_out:.3f} N.m/A at the wheel",
            f"                    R {self.motor_r:.1f} ohm, L {self.motor_l * 1e3:.1f} mH"
            f" (L/R {self.motor_l / self.motor_r * 1e3:.2f} ms)",
            f"driver              {self.board}: {d['decay']} decay at "
            f"{self.pwm_hz:.0f} Hz (period {1e3 / self.pwm_hz:.2f} ms)",
            f"pack                {self.cells}S, {self.cell_capacity_ah:.1f} Ah, "
            f"{self.pack_r_total * 1e3:.0f} mohm to the 12 V rail",
            f"5 V rail            {self.buck_v_set:.3f} V set point",
        ])


DEFAULT = Hardware()
