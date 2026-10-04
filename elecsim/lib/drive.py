"""A motor and its bridge, averaged over the PWM period."""
from __future__ import annotations

from ..core import Component, Limit, Param, Port
from ..pwm import steady_state


def decode_duty(style, in1, in2, pwm=1.0, stby=1.0):
    """Turn input duties into (direction, duty, decay).

    The inputs are duty cycles 0..1 (a steady level is 0 or 1). Works out
    what fraction of the period the supply is applied, in which direction,
    and what the bridge does with the rest.
    """
    if style == "in_in_pwm":
        if stby < 0.5 or (in1 < 0.5 and in2 < 0.5):
            return 0, 0.0, "coast"
        if in1 >= 0.5 and in2 >= 0.5:
            return 0, 0.0, "brake"
        return (1 if in1 >= 0.5 else -1), pwm, "brake"
    if style == "in_in":
        hi, lo = max(in1, in2), min(in1, in2)
        sign = 1 if in1 >= in2 else -1
        if hi <= 0.0:
            return 0, 0.0, "coast"
        if lo <= 0.0:
            return sign, hi, "coast"         # drive / coast
        if hi >= 1.0:
            return sign, 1.0 - lo, "brake"   # drive / brake
        return sign, hi - lo, "brake"        # mixed: treat the overlap as brake
    raise ValueError(f"unknown bridge logic style {style!r}")


class PwmMotorDrive(Component):
    """Bridge + brushed motor as one averaged element.

    Uses the exact periodic solution in `elecsim.pwm`, so it is right in
    discontinuous conduction and in both decay modes, and it costs one step
    per millisecond instead of a hundred per PWM period. What it gives up is
    the waveform inside the period: you get the ripple as a number, not as a
    trace.

    Electrically it is a current drawn from `vm`. Mechanically it is the
    same motor as `lib.motor.DCMotor`: set `inputs["omega"]`, read
    `sig["torque"]`.
    """
    vm = Port("power_in")
    gnd = Port("ground")
    in1 = Port("digital_in")
    in2 = Port("digital_in")
    pwm = Port("digital_in", optional=True)
    stby = Port("digital_in", optional=True)

    # bridge
    logic = Param("in_in", doc="'in_in' or 'in_in_pwm'")
    r_on = Param(unit="ohm", min=0, doc="high side + low side")
    vf = Param(0.8, unit="V", doc="body diode drop")
    f_pwm = Param(unit="Hz", min=1)
    i_cont = Param(None, unit="A")
    i_peak = Param(None, unit="A")
    v_max = Param(None, unit="V")
    v_min = Param(None, unit="V")
    # motor
    r = Param(unit="ohm", min=1e-6)
    l = Param(unit="H", min=1e-9)
    ke = Param(unit="V.s/rad")
    kt = Param(None, unit="N.m/A")
    gear = Param(1.0)
    gear_eff = Param(1.0)
    i_motor_max = Param(None, unit="A", doc="winding current, continuous")
    v_motor_max = Param(None, unit="V", doc="rated motor voltage")
    polarity = Param(1, doc="+1 or -1: which way round the motor leads are")
    # an optional shaft of its own, as on DCMotor
    inertia = Param(None, unit="kg.m2", doc="at the output; None = speed is an input")
    tau_coulomb = Param(0.0, unit="N.m")
    tau_static = Param(None, unit="N.m")
    viscous = Param(0.0, unit="N.m.s/rad")
    linear = False

    def __init__(self, name, **kw):
        super().__init__(name, **kw)
        if self.kt is None:
            self.kt = self.ke
        self.inputs["omega"] = 0.0
        self.inputs["load_torque"] = 0.0
        self.inputs["locked"] = False
        L = self.limit
        if self.i_cont is not None:
            L("i_rms", max="i_cont", for_s=0.5, why="bridge continuous current")
        if self.i_peak is not None:
            L("i_peak", max="i_peak", why="bridge peak current / OCP")
        if self.v_max is not None:
            L("v_m", max="v_max", why="bridge supply maximum")
        if self.v_min is not None:
            L("v_m", min="v_min", why="bridge supply minimum")
        if self.i_motor_max is not None:
            L("i_rms", max="i_motor_max", for_s=1.0, why="winding current")
        if self.v_motor_max is not None:
            L("v_eff", max="v_motor_max", for_s=0.05,
              why="effective motor voltage above its rating")

    def _level(self, pin, default):
        return pin.level if pin.net is not None else default

    def command(self):
        sign, duty, decay = decode_duty(
            self.logic, self._level(self.in1, 0.0), self._level(self.in2, 0.0),
            self._level(self.pwm, 1.0), self._level(self.stby, 1.0))
        return sign * self.polarity, duty, decay

    def solve(self, v):
        """Period-averaged operating point at supply voltage v."""
        sign, duty, decay = self.command()
        e = self.ke * self.gear * self.inputs["omega"]
        if sign == 0:
            if decay == "brake":            # both outputs low: a shorted motor
                i = -e / (self.r + self.r_on)
                return dict(i_avg=i, i_supply=0.0, i_rms=abs(i), i_peak=i,
                            i_min=i, ripple=0.0, continuous=True,
                            p_bridge=self.r_on * i * i, p_winding=self.r * i * i)
            return dict(i_avg=0.0, i_supply=0.0, i_rms=0.0, i_peak=0.0,
                        i_min=0.0, ripple=0.0, continuous=False,
                        p_bridge=0.0, p_winding=0.0)
        ss = steady_state(max(v, 0.0), sign * e, self.r, self.l,
                          1.0 / self.f_pwm, duty, decay, self.r_on, self.vf)
        for k in ("i_avg", "i_peak", "i_min"):
            ss[k] *= sign
        return ss

    def stamp(self, s):
        v = s.vd(self.vm, self.gnd)
        i0 = self.solve(v)["i_supply"]
        dv = 1e-3
        g = (self.solve(v + dv)["i_supply"] - i0) / dv
        s.Inl(self.vm, self.gnd, i0, g)

    def torque(self, i):
        return self.kt * self.gear * self.gear_eff * i

    def update(self, s, dt):
        if self.inputs["locked"]:
            self.inputs["omega"] = 0.0
        elif self.inertia is not None:
            from ..mech import friction_step
            i = self.solve(s.vd(self.vm, self.gnd))["i_avg"]
            self.inputs["omega"], _ = friction_step(
                self.inputs["omega"],
                self.torque(i) - self.inputs["load_torque"], self.inertia, dt,
                self.tau_coulomb, self.tau_static, self.viscous)

    def measure(self, s):
        v = s.vd(self.vm, self.gnd)
        ss = self.solve(v)
        sign, duty, decay = self.command()
        w = self.inputs["omega"]
        i = ss["i_avg"]
        return {"i": i, "i_supply": ss["i_supply"], "i_rms": ss["i_rms"],
                "i_peak": abs(ss["i_peak"]), "ripple": ss["ripple"],
                "continuous": float(ss["continuous"]), "duty": duty * sign,
                "v_m": v, "v_eff": abs(i * self.r + self.ke * self.gear * w),
                "emf": self.ke * self.gear * w, "omega": w,
                "torque": self.torque(i), "heat_bridge": ss["p_bridge"],
                "heat_motor": ss["p_winding"],
                "heat": ss["p_bridge"], "p_mech": self.torque(i) * w}
