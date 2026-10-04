"""A PWM-driven R-L-E load in periodic steady state, in closed form.

A brushed motor seen over one PWM period is a resistance, an inductance and
a back-EMF that does not have time to change. The bridge applies the supply
for a fraction d of the period and then does one of two things:

    "brake"   shorts the motor through two FETs      (slow decay)
    "coast"   turns everything off; the current      (fast decay)
              finds its way back to the supply
              through the body diodes and then stops

The two give very different motors. Braking is linear and the average
current is just (d V - E) / R. Coasting is not: once the current has died
the winding floats at its own back-EMF, so the bridge is only "there" for
part of the cycle, and at light load a small duty gives a surprisingly high
speed. Which one you have is a property of how the board is wired and how
the software drives it, not of the motor.

`steady_state` solves the periodic waveform exactly for either case and
returns its averages. `lib.drive.PwmMotorDrive` uses it; the switching
H-bridge model in `lib.bridge` is what it is tested against.
"""
from __future__ import annotations

import math


def _seg(i0, i_inf, tau, t):
    """One exponential segment. Returns (i_end, integral i, integral i^2)."""
    if t <= 0:
        return i0, 0.0, 0.0
    k = i0 - i_inf
    e = math.exp(-t / tau)
    q = i_inf * t + k * tau * (1 - e)
    q2 = (i_inf * i_inf * t + 2 * i_inf * k * tau * (1 - e)
          + k * k * tau / 2 * (1 - e * e))
    return i_inf + k * e, q, q2


def _t_zero(i0, i_inf, tau):
    """Time for a segment starting at i0 and heading to i_inf to cross zero."""
    if i0 == 0:
        return 0.0
    if i_inf == 0 or (i_inf > 0) == (i0 > 0):
        return math.inf
    return tau * math.log((i0 - i_inf) / (-i_inf))


def steady_state(v, e, r, l, period, duty, decay="brake", r_on=0.0, vd=0.8,
                 r_brake=None):
    """Averages of one PWM period in periodic steady state.

    v       supply voltage              e      back-EMF (same sign sense as v)
    r, l    winding resistance, inductance
    duty    fraction of the period the supply is applied, 0..1
    r_on    bridge resistance in the drive path (high side + low side)
    r_brake bridge resistance in the brake path (default: same as r_on)
    vd      one body-diode drop (coast decay passes through two)

    Returns a dict: i_avg (motor), i_supply (average drawn from the supply),
    i_rms, i_peak, i_min, ripple, continuous (bool), p_bridge, p_winding.
    """
    d = min(max(duty, 0.0), 1.0)
    t_on, t_off = d * period, (1 - d) * period
    r_d = r + r_on
    tau_d = l / r_d
    i_inf_on = (v - e) / r_d

    if decay == "brake":
        r_b = r + (r_on if r_brake is None else r_brake)
        tau_b = l / r_b
        i_inf_off = -e / r_b
        a = math.exp(-t_on / tau_d) if t_on > 0 else 1.0
        b = math.exp(-t_off / tau_b) if t_off > 0 else 1.0
        # i1 = inf_on + (i0 - inf_on) a ;  i0 = inf_off + (i1 - inf_off) b
        i0 = (i_inf_off * (1 - b) + i_inf_on * (1 - a) * b) / (1 - a * b) \
            if a * b < 1 else i_inf_on
        i1, q_on, q2_on = _seg(i0, i_inf_on, tau_d, t_on)
        _, q_off, q2_off = _seg(i1, i_inf_off, tau_b, t_off)
        i_avg = (q_on + q_off) / period
        i_sup = q_on / period
        i_rms = math.sqrt(max((q2_on + q2_off) / period, 0.0))
        p_bridge = (r_on * q2_on + (r_b - r) * q2_off) / period
        return dict(i_avg=i_avg, i_supply=i_sup, i_rms=i_rms,
                    i_peak=max(i0, i1), i_min=min(i0, i1),
                    ripple=abs(i1 - i0), continuous=True,
                    p_bridge=p_bridge, p_winding=r * i_rms ** 2)

    if decay != "coast":
        raise ValueError("decay must be 'brake' or 'coast'")

    tau_c = l / r
    vclamp = v + 2 * vd

    def off_phase(i1):
        """Current after the off time, starting from i1. (i_end, q, q2, t_c)"""
        if i1 > 0:
            i_inf = (-vclamp - e) / r          # diodes return it to the supply
        elif i1 < 0:
            i_inf = (vclamp - e) / r
        else:
            return 0.0, 0.0, 0.0, 0.0
        tz = _t_zero(i1, i_inf, tau_c)
        tc = min(tz, t_off)
        i_end, q, q2 = _seg(i1, i_inf, tau_c, tc)
        if tz <= t_off:
            i_end = 0.0
        return i_end, q, q2, tc

    # The period map i0 -> i0' is a contraction; iterate it to its fixed point.
    i0 = 0.0
    for _ in range(200):
        i1, _, _ = _seg(i0, i_inf_on, tau_d, t_on)
        nxt, _, _, _ = off_phase(i1)
        if abs(nxt - i0) < 1e-12 + 1e-9 * abs(i0):
            i0 = nxt
            break
        i0 = nxt
    i1, q_on, q2_on = _seg(i0, i_inf_on, tau_d, t_on)
    i_end, q_off, q2_off, tc = off_phase(i1)
    i_avg = (q_on + q_off) / period
    i_sup = (q_on - abs(q_off)) / period
    i_rms = math.sqrt(max((q2_on + q2_off) / period, 0.0))
    p_bridge = (r_on * q2_on + 2 * vd * abs(q_off)) / period
    return dict(i_avg=i_avg, i_supply=i_sup, i_rms=i_rms,
                i_peak=max(i0, i1, key=abs), i_min=min(i0, i1, key=abs),
                ripple=abs(i1 - i0), continuous=bool(tc >= t_off and t_off > 0
                                                     and i_end != 0.0) or d >= 1.0,
                p_bridge=p_bridge, p_winding=r * i_rms ** 2)
