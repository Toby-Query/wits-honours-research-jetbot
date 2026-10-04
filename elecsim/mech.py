"""The little mechanics a circuit simulator needs: a shaft with friction."""
from __future__ import annotations

import math


def friction_step(omega, tau_drive, inertia, dt, tau_coulomb=0.0,
                  tau_static=None, viscous=0.0):
    """Advance a shaft one step under a drive torque and dry + viscous friction.

    Handles sticking properly: a stopped shaft stays stopped until the drive
    exceeds the static friction, and a moving shaft that friction would carry
    through zero within the step is stopped at zero instead of being flung
    backwards (the usual failure of sign(omega) friction).

    Returns (omega_new, friction_torque_applied).
    """
    tau_static = tau_coulomb if tau_static is None else tau_static
    if omega == 0.0:
        if abs(tau_drive) <= tau_static:
            return 0.0, -tau_drive
        tau_f = -math.copysign(tau_coulomb, tau_drive)
        return (tau_drive + tau_f) * dt / inertia, tau_f
    tau_f = -math.copysign(tau_coulomb, omega) - viscous * omega
    new = omega + (tau_drive + tau_f) * dt / inertia
    if new * omega < 0.0:
        # Crossed zero. If the drive alone cannot hold it moving, it stops.
        if abs(tau_drive) <= tau_static:
            return 0.0, -tau_drive
    return new, tau_f
