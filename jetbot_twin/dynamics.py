"""Planar dynamics of a two-wheel differential-drive robot on casters.

State: pose (x, y, theta), body speed v and yaw rate w, and the two wheel
speeds. The wheels are NOT assumed to roll without slipping: each tyre
transmits a force that grows with its slip speed and saturates at mu N, so
a robot that is given more torque than its tyres can hold spins its wheels,
as the real one does.

What is modelled
    tyre traction with saturation        rolling resistance
    gearbox friction, static and running (inside the wheel equation)
    caster drag, rolling and scrubbing   weight transfer under acceleration
    centre of mass off the axle

What is not
    lateral tyre slip (the axle is a rolling constraint)
    gearbox backlash                     caster swivel dynamics
    floor unevenness                     the body pitching on its casters

Nothing here is specific to the JetBot: it takes a `Hardware` and uses its
geometry, mass and friction figures.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from elecsim.mech import friction_step

G = 9.81


@dataclass
class State:
    x: float = 0.0
    y: float = 0.0
    th: float = 0.0
    v: float = 0.0              # forward speed of the axle midpoint, m/s
    w: float = 0.0              # yaw rate, rad/s
    wl: float = 0.0             # left wheel speed, rad/s
    wr: float = 0.0
    a: float = 0.0              # forward acceleration, filtered
    phi_l: float = 0.0          # wheel angles, for drawing and for odometry
    phi_r: float = 0.0


class DiffDrive:
    def __init__(self, hw, substeps=4):
        self.hw = hw
        self.s = State()
        self.substeps = substeps
        self.d = hw.cog[0]                      # CoG ahead of the axle (+)
        self.h = hw.cog[2]
        self.m = hw.mass
        self.I_axle = hw.yaw_inertia_cog + self.m * self.d ** 2
        self.Jw = hw.wheel_assembly_inertia
        self.forces = {}
        self.lifted = False                     # wheels off the ground
        self.held = False                       # wheels gripped: cannot turn

    # -- contact loads ----------------------------------------------------------
    def normal_loads(self):
        """(N_left, N_right, N_caster, caster_x) with weight transfer."""
        hw, s = self.hw, self.s
        W = self.m * G
        # moment tipping the robot back onto its rear caster
        M = -W * self.d + self.m * s.a * self.h
        if M >= 0:
            xc = hw.caster_rear_x
            Nc = M / abs(xc)
        else:
            xc = hw.caster_front_x
            Nc = -M / abs(xc)
        Nc = min(Nc, W)
        Nw = W - Nc
        lat = self.m * s.v * s.w * self.h / hw.track       # to the outer wheel
        lat = max(-Nw / 2, min(Nw / 2, lat))
        return Nw / 2 - lat, Nw / 2 + lat, Nc, xc

    # -- one step -----------------------------------------------------------------
    def step(self, tau_l, tau_r, dt):
        """Advance by dt with motor torques (at the wheel, after gearbox
        efficiency, before friction) held constant."""
        hw, s = self.hw, self.s
        r, Wt = hw.wheel_radius, hw.track
        h = dt / self.substeps
        for _ in range(self.substeps):
            if self.held:
                s.wl = s.wr = s.v = s.w = 0.0
                self.forces = dict(Fl=0.0, Fr=0.0, Nl=0.0, Nr=0.0, Nc=0.0,
                                   slip_l=0.0, slip_r=0.0)
                continue
            if self.lifted:
                c2 = hw.motor_drag2
                s.wl, _ = friction_step(s.wl, tau_l - c2 * s.wl * abs(s.wl), self.Jw, h,
                                        hw.motor_tau_coulomb, hw.motor_tau_static,
                                        hw.motor_viscous)
                s.wr, _ = friction_step(s.wr, tau_r - c2 * s.wr * abs(s.wr), self.Jw, h,
                                        hw.motor_tau_coulomb, hw.motor_tau_static,
                                        hw.motor_viscous)
                s.phi_l += s.wl * h
                s.phi_r += s.wr * h
                self.forces = dict(Fl=0.0, Fr=0.0, Nl=0.0, Nr=0.0, Nc=0.0,
                                   slip_l=0.0, slip_r=0.0)
                continue
            Nl, Nr, Nc, xc = self.normal_loads()
            vl = s.v - s.w * Wt / 2                  # ground speed under each wheel
            vr = s.v + s.w * Wt / 2
            sl, sr = r * s.wl - vl, r * s.wr - vr    # slip speeds
            Fl = hw.mu_tyre * Nl * math.tanh(sl / hw.slip_knee)
            Fr = hw.mu_tyre * Nr * math.tanh(sr / hw.slip_knee)
            # rolling resistance acts on the wheel as a torque
            rr_l = hw.c_rr * Nl * r
            rr_r = hw.c_rr * Nr * r
            c2 = hw.motor_drag2
            s.wl, _ = friction_step(
                s.wl, tau_l - r * Fl - c2 * s.wl * abs(s.wl), self.Jw, h,
                hw.motor_tau_coulomb + rr_l, hw.motor_tau_static + rr_l,
                hw.motor_viscous)
            s.wr, _ = friction_step(
                s.wr, tau_r - r * Fr - c2 * s.wr * abs(s.wr), self.Jw, h,
                hw.motor_tau_coulomb + rr_r, hw.motor_tau_static + rr_r,
                hw.motor_viscous)
            # caster: a ball at xc on the centreline
            vcx, vcy = s.v, s.w * xc
            if hw.mu_caster_roll is None:
                # one drag, the same in every direction
                vc = math.hypot(vcx, vcy)
                Fc = hw.mu_caster * Nc * math.tanh(vc / 0.01)
                fcx = -Fc * vcx / vc if vc > 1e-9 else 0.0
                fcy = -Fc * vcy / vc if vc > 1e-9 else 0.0
            else:
                # rolls along the heading, scrubs across it
                fcx = -hw.mu_caster_roll * Nc * math.tanh(vcx / 0.01)
                fcy = -hw.mu_caster * Nc * math.tanh(vcy / 0.01)
            # body, with the centre of mass a distance d ahead of the axle
            Fx = Fl + Fr + fcx
            Mz = (Fr - Fl) * Wt / 2 + fcy * xc
            a = Fx / self.m + self.d * s.w ** 2
            alpha = (Mz - self.m * self.d * s.v * s.w) / self.I_axle
            s.v += a * h
            s.w += alpha * h
            s.a += (a - s.a) * min(1.0, h / 0.02)          # 20 ms filter
            s.th += s.w * h
            s.x += s.v * math.cos(s.th) * h
            s.y += s.v * math.sin(s.th) * h
            s.phi_l += s.wl * h
            s.phi_r += s.wr * h
            self.forces = dict(Fl=Fl, Fr=Fr, Nl=Nl, Nr=Nr, Nc=Nc,
                               slip_l=sl, slip_r=sr)
        return s

    # -- what the wheels alone would say ---------------------------------------
    def kinematic_twist(self):
        """Body speed and yaw rate if the wheels were not slipping."""
        r, Wt = self.hw.wheel_radius, self.hw.track
        return (r * (self.s.wr + self.s.wl) / 2, r * (self.s.wr - self.s.wl) / Wt)
