"""Things that make logic levels."""
from __future__ import annotations

from ..core import Component, Param, Port


class PwmController(Component):
    """A 16-channel, 12-bit PWM generator: the PCA9685.

    Each channel has an ON count and an OFF count in 0..4095 and a full-on /
    full-off override, exactly as the chip's registers do, and `set_pwm`
    takes the same arguments as the usual Python driver, so code written
    for the real chip can be pointed at this.

    `resolve`:
        "average"    each output carries its duty cycle (0..1). Use with
                     averaged loads; steps can be milliseconds.
        "switching"  each output is 0 or 1 at the instant of the step. Use
                     with switching loads and a step well below the period.
    """
    vdd = Port("power_in", v_abs_max=6.0)
    gnd = Port("ground")
    sda = Port("bidir", voh=3.3, optional=True)
    scl = Port("bidir", voh=3.3, optional=True)
    for _k in range(16):
        locals()[f"led{_k}"] = Port("digital_out", voh=3.3, optional=True)
    del _k

    f_osc = Param(25e6, unit="Hz", doc="internal oscillator")
    prescale = Param(30, doc="PRE_SCALE register; power-on default is 30 (200 Hz)")
    resolve = Param("average")
    i_supply = Param(6e-3, unit="A")
    address = Param(0x40)

    def __init__(self, name, **kw):
        super().__init__(name, **kw)
        self.on = [0] * 16
        self.off = [0] * 16          # power-on: outputs low
        self.prescale = int(self.prescale)

    # -- the register interface, as the Python drivers expose it ---------------
    def set_pwm_freq(self, hz):
        """Same arithmetic as Adafruit_PCA9685.set_pwm_freq (including its
        0.9 correction factor), so the frequency that results is the one the
        real chip would be programmed to."""
        import math
        prescaleval = self.f_osc / 4096.0 / (hz * 0.9) - 1.0
        self.prescale = int(math.floor(prescaleval + 0.5))

    def set_pwm(self, channel, on, off):
        self.on[channel], self.off[channel] = int(on), int(off)

    @property
    def freq(self):
        return self.f_osc / (4096.0 * (self.prescale + 1))

    def duty(self, ch):
        on, off = self.on[ch], self.off[ch]
        if on & 0x1000 and not off & 0x1000:
            return 1.0                              # full on
        if off & 0x1000:
            return 0.0                              # full off wins
        return ((off - on) % 4096) / 4096.0

    def level(self, ch, t):
        on, off = self.on[ch], self.off[ch]
        if on & 0x1000 and not off & 0x1000:
            return 1.0
        if off & 0x1000 or on == off:
            return 0.0
        count = int((t * self.freq) % 1.0 * 4096)
        if on < off:
            return 1.0 if on <= count < off else 0.0
        return 0.0 if off <= count < on else 1.0

    def pre_step(self, t, dt):
        avg = self.resolve == "average" or dt is None
        tm = t if dt is None else t + 0.5 * dt
        for ch in range(16):
            pin = self._pins[f"led{ch}"]
            if pin.net is not None:
                pin.drive(self.duty(ch) if avg else self.level(ch, tm))

    def stamp(self, s):
        s.I(self.vdd, self.gnd, self.i_supply)

    def measure(self, s):
        out = {"freq": self.freq, "v": s.vd(self.vdd, self.gnd),
               "heat": s.vd(self.vdd, self.gnd) * self.i_supply}
        for ch in range(16):
            if self._pins[f"led{ch}"].net is not None:
                out[f"duty{ch}"] = self.duty(ch)
        return out


class LogicLevel(Component):
    """A pin tied high or low, or driven by a scenario via inputs["level"]."""
    out = Port("digital_out", voh=3.3)
    level = Param(1.0)

    def __init__(self, name, **kw):
        super().__init__(name, **kw)
        self.inputs["level"] = float(self.level)

    def pre_step(self, t, dt):
        self.out.drive(self.inputs["level"])

    def stamp(self, s):
        pass
