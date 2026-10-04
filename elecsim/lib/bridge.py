"""H-bridges: a switching model, and the logic that decides its state."""
from __future__ import annotations

from ..core import Component, Limit, Param, Port
from .semis import diode_fit, diode_iv, limit_junction

# bridge states: which of the four switches are on  (h1, l1, h2, l2)
FORWARD = (1, 0, 0, 1)        # out1 high, out2 low
REVERSE = (0, 1, 1, 0)
BRAKE = (0, 1, 0, 1)          # both low sides: motor shorted
COAST = (0, 0, 0, 0)          # everything off: body diodes only


def decode_in_in(in1, in2, pwm=1, stby=1):
    """Two-input bridges (DRV8870, DRV8871, DRV8833, L9110...).

        0 0  coast     1 0  forward     0 1  reverse     1 1  brake
    """
    if in1 and in2:
        return BRAKE
    if in1:
        return FORWARD
    if in2:
        return REVERSE
    return COAST


def decode_in_in_pwm(in1, in2, pwm=1, stby=1):
    """Direction pins plus a PWM pin (TB6612FNG, L298 with enable tied...).

    TB6612FNG truth table: with a direction selected, PWM low is a SHORT
    BRAKE, not a coast -- which is what makes it a slow-decay driver.
    """
    if not stby:
        return COAST
    if in1 and in2:
        return BRAKE
    if not in1 and not in2:
        return COAST
    if not pwm:
        return BRAKE
    return FORWARD if in1 else REVERSE


DECODERS = {"in_in": decode_in_in, "in_in_pwm": decode_in_in_pwm}


class HBridge(Component):
    """Four switches and their body diodes, switched by logic inputs.

    This is the time-domain model: it needs a step small enough to see the
    PWM (a hundredth of a period is comfortable). For the same hardware
    averaged over the period, use `lib.drive.PwmMotorDrive`.
    """
    vm = Port("power_in")
    gnd = Port("ground")
    out1 = Port()
    out2 = Port()
    in1 = Port("digital_in")
    in2 = Port("digital_in")
    pwm = Port("digital_in", optional=True)
    stby = Port("digital_in", optional=True)

    r_hs = Param(unit="ohm", min=1e-6, doc="high-side on resistance")
    r_ls = Param(unit="ohm", min=1e-6, doc="low-side on resistance")
    vf = Param(0.8, unit="V", doc="body diode drop at 1 A")
    logic = Param("in_in", doc="a key of DECODERS, or a decoder function")
    i_cont = Param(None, unit="A", doc="continuous output current")
    i_peak = Param(None, unit="A", doc="peak output current / OCP threshold")
    v_max = Param(None, unit="V", doc="maximum motor supply")
    v_min = Param(None, unit="V", doc="minimum motor supply")
    linear = False

    def __init__(self, name, **kw):
        super().__init__(name, **kw)
        self.i_s = diode_fit(self.vf, 1.0, 1.8)
        self._vj = [0.0] * 4
        self.state = COAST
        self._decode = DECODERS[self.logic] if isinstance(self.logic, str) \
            else self.logic
        if self.i_cont is not None:
            self.limit("i", max="i_cont", absolute=True, for_s=0.5,
                       why="continuous output current")
        if self.i_peak is not None:
            self.limit("i", max="i_peak", absolute=True, why="peak output current")
        if self.v_max is not None:
            self.limit("v_m", max="v_max", why="motor supply maximum")
        if self.v_min is not None:
            self.limit("v_m", min="v_min", why="motor supply minimum")

    def refresh(self):
        self.i_s = diode_fit(self.vf, 1.0, 1.8)

    def _logic(self, pin, default):
        return (pin.level >= 0.5) if pin.net is not None else default

    def pre_step(self, t, dt):
        self.state = self._decode(self._logic(self.in1, 0),
                                  self._logic(self.in2, 0),
                                  self._logic(self.pwm, 1),
                                  self._logic(self.stby, 1))

    def _legs(self):
        # (drain side, source side, r_on) for h1, l1, h2, l2
        return ((self.vm, self.out1, self.r_hs), (self.out1, self.gnd, self.r_ls),
                (self.vm, self.out2, self.r_hs), (self.out2, self.gnd, self.r_ls))

    def stamp(self, s):
        for k, ((d, src, r), on) in enumerate(zip(self._legs(), self.state)):
            if on:
                s.G(d, src, 1.0 / r)
                continue
            s.G(d, src, 1e-9)
            v = limit_junction(s.vd(src, d), self._vj[k], self.i_s, 1.8)
            self._vj[k] = v
            i, g = diode_iv(v, self.i_s, 1.8)
            s.G(src, d, g)
            s.I(src, d, i - g * v)

    def _leg_currents(self, s):
        out = []
        for (d, src, r), on in zip(self._legs(), self.state):
            v = s.vd(d, src)
            out.append(v / r if on else
                       v * 1e-9 - diode_iv(-v, self.i_s, 1.8)[0])
        return out

    def measure(self, s):
        ih1, il1, ih2, il2 = self._leg_currents(s)
        legs = self._legs()
        heat = sum(abs(s.vd(d, src) * i)
                   for (d, src, _), i in zip(legs, (ih1, il1, ih2, il2)))
        return {"i": ih1 - il1,               # out of out1, into the motor
                "i_supply": ih1 + ih2,
                "v_out": s.vd(self.out1, self.out2),
                "v_m": s.vd(self.vm, self.gnd), "heat": heat,
                "state": float(self.state == FORWARD) - float(self.state == REVERSE)}
