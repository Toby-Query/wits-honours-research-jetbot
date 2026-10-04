"""A brushed DC motor, with or without its own shaft."""
from __future__ import annotations

from ..core import Component, Limit, Param, Port
from ..mech import friction_step


class DCMotor(Component):
    """Winding resistance and inductance, back-EMF, torque.

        v = R i + L di/dt + ke * gear * omega
        torque at the output shaft = kt * gear * gear_eff * i   (before friction)

    `omega` is the OUTPUT shaft speed. Either something outside sets it every
    step (`motor.inputs["omega"] = ...`, the way a vehicle model would), or
    give the motor an `inertia` and it spins its own shaft against the
    friction parameters and `inputs["load_torque"]`.
    """
    a = Port()
    b = Port()
    r = Param(unit="ohm", min=1e-6, doc="winding resistance")
    l = Param(unit="H", min=0, doc="winding inductance")
    ke = Param(unit="V.s/rad", doc="back-EMF constant at the rotor")
    kt = Param(None, unit="N.m/A", doc="torque constant at the rotor; default ke")
    gear = Param(1.0, doc="reduction ratio")
    gear_eff = Param(1.0, doc="gearbox efficiency when driving")
    inertia = Param(None, unit="kg.m2", doc="at the output; None = speed is an input")
    tau_coulomb = Param(0.0, unit="N.m", doc="dry friction at the output")
    tau_static = Param(None, unit="N.m", doc="breakaway friction; default coulomb")
    viscous = Param(0.0, unit="N.m.s/rad")
    i_max = Param(None, unit="A", doc="maximum continuous winding current")
    v_max = Param(None, unit="V", doc="rated terminal voltage")
    branches = 1
    fault_modes = ("open", "short")

    def __init__(self, name, **kw):
        super().__init__(name, **kw)
        if self.kt is None:
            self.kt = self.ke
        self.inputs["omega"] = 0.0
        self.inputs["load_torque"] = 0.0
        self.il = 0.0
        if self.i_max is not None:
            self.limit("i", max="i_max", absolute=True, for_s=1.0,
                       why="winding current, continuous")
        if self.v_max is not None:
            self.limit("v", max="v_max", absolute=True, for_s=0.05,
                       why="rated motor voltage")

    @property
    def omega(self):
        return self.inputs["omega"]

    def emf(self):
        return self.ke * self.gear * self.inputs["omega"]

    def stamp(self, s):
        if s.dc:
            s.V(self, self.a, self.b, self.emf(), r=self.r)
        else:
            k = self.l / s.dt
            s.V(self, self.a, self.b, self.emf() - k * self.il, r=self.r + k)

    def init_state(self, s):
        self.il = s.i(self)

    def torque(self, i):
        return self.kt * self.gear * self.gear_eff * i

    def update(self, s, dt):
        self.il = s.i(self)
        if self.inertia is not None:
            tau = self.torque(self.il) - self.inputs["load_torque"]
            self.inputs["omega"], _ = friction_step(
                self.inputs["omega"], tau, self.inertia, dt,
                self.tau_coulomb, self.tau_static, self.viscous)

    def measure(self, s):
        i = s.i(self)
        v = s.vd(self.a, self.b)
        w = self.inputs["omega"]
        return {"i": i, "v": v, "emf": self.emf(), "omega": w,
                "rpm": w * 9.549297, "torque": self.torque(i),
                "heat": i * i * self.r, "p_mech": self.torque(i) * w}
