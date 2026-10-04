# How the twin works

Two models stepped together, a millisecond or two at a time
([jetbot_twin/twin.py](../jetbot_twin/twin.py)):

```
            wheel speeds (as back-EMF)
        ┌──────────────────────────────────┐
        ▼                                  │
  ┌───────────┐   motor currents     ┌───────────┐
  │  circuit  │ ───────────────────▶ │ dynamics  │ ──▶ pose, speed, slip
  │ (elecsim) │   (as wheel torque)  │           │
  └───────────┘                      └───────────┘
        ▲
        │ PCA9685 register writes, exactly as jetbot.Motor makes them
  robot.set_motors(l, r)
```

## The software interface

`twin.robot` has the same methods as `jetbot.Robot`. Underneath,
`electrical.write_motor` does what `jetbot.Motor._write_value` does: scales to
0–255, multiplies by 16, and writes the same PCA9685 channels with the same
counts. `robot/agent.py`, which runs on the real JetBot, makes the identical
writes. So the twin and the robot are not compared on "a command of 0.3" in the
abstract, but on the same register values.

One consequence is visible in both: `int(255 * 0.3) * 16 = 1216` counts out of
4096 is a duty of 0.297, not 0.3, and a command of 1.0 is 4080/4096, never
quite fully on.

## The circuit

See [ELECTRICAL.md](ELECTRICAL.md). In the twin each motor and its bridge is
one averaged element; the PWM period is solved exactly rather than simulated,
so the twin runs at about three times real time.

## The dynamics

[jetbot_twin/dynamics.py](../jetbot_twin/dynamics.py). State: position and
heading, forward speed *v*, yaw rate *ω*, and the two wheel speeds.

**Each wheel**, with the rotor's inertia reflected through the gearbox:

    J_w · dω_w/dt = τ_motor − r·F_tyre − friction

where friction is dry (with a higher breakaway value) plus viscous, from the
motor's no-load current, plus rolling resistance.

**Each tyre** transmits a force that depends on how fast it is slipping:

    F_tyre = μ · N · tanh(slip / slip_knee),    slip = r·ω_w − (ground speed under that wheel)

The wheels are *not* assumed to roll without slipping. A launch at full command
spins them, in the twin as on a real floor.

**The body**, with its centre of mass a distance *d* ahead of the axle (it is
negative: the mass is behind):

    m · (dv/dt − d·ω²) = F_left + F_right + F_caster,x
    (I + m·d²) · dω/dt + m·d·v·ω = (F_right − F_left)·W/2 + F_caster,y · x_caster

**The caster** is a ball that drags: a force μ_c·N_c opposing its motion over
the floor. In a spin it is being pushed sideways the whole time, which is why
caster friction shows up in the spin test and hardly at all in the straight
one.

**The loads** on the wheels and caster come from the centre of mass position
and height, and shift with acceleration. The robot rests on its rear caster
(the mass is behind the axle); if it brakes hard enough the load transfers to
the front one.

One thing this predicts that is easy to look for: **the robot does not spin
about the middle of its axle.** The centre of mass is 19 mm behind it, so in a
spin the body wants to orbit, weight shifts onto one wheel, and the pivot moves
toward that wheel: about 10 mm off-centre at 200°/s and 35 mm at 460°/s. A
strip of tape on the floor under the axle will show it.

### Where the numbers come from

| | Source |
|---|---|
| Track, caster positions, body size, camera position | measured from Waveshare's STEP file (`tools/inspect_step.py`) |
| Mass, centre of mass, yaw inertia | summed from a parts list in `hardware.py`: sheet-metal masses from STEP volume, the rest typical. **Weigh the robot** |
| Wheel radius | 65 mm per Waveshare; the CAD tyre is 67.2 mm. **Measure it** |
| Motor constants | derived from "1:48, 240 RPM" on the assumption of a 6 V motor. **The largest uncertainty in the twin** |
| Friction coefficients | assumed |

`python -m jetbot_twin info` prints all of it. `python -m jetbot_twin
assumptions` lists what is unverified.

## What is left out, and what it costs

| Left out | Effect |
|---|---|
| Lateral tyre slip | The axle cannot slide sideways. Wrong for a fast spin on a slippery floor |
| Gearbox backlash | A short dead time on every reversal |
| Left/right mismatch | The twin drives perfectly straight. A real JetBot never does: its two motors differ by several percent. The `straight` test measures this, and it can be given to the twin as different `alpha` values on the two `Motor`s |
| The body rocking between its casters | The real robot nods when it stops |
| Motor heating | Winding resistance rises about 0.4 % per °C; a hot motor is weaker |
| Floor texture | One friction coefficient for everything |
| The camera image | The lens geometry is in `hardware.py` and the viewer shows a rectilinear stand-in; there is no fisheye rendering or image noise |

## The 3D model

`tools/build_model.py` converts Waveshare's own STEP assembly
([reference/jetbot.STEP](../reference/jetbot.STEP), from their wiki) into
`web/jetbot.glb` for the browser and `export/meshes/` for other simulators.
No geometry is redrawn. What is added: part names (theirs do not survive the
file format), colours, and the change of frame.

One part is not Waveshare's. Their CAD has a fixed camera bracket; this robot
has a hinged one, so the bracket is dropped and a hinged mount is built in its
place from photographs of the robot (a likeness: sized against the 25 mm
camera board, not measured). The camera is moved onto it, its four screws go
with it, and the ribbon cable is redrawn between its two connectors.

Because that geometry is hand-made, the build checks it before writing
anything, and writes nothing if a check fails: the mount passes through no
other part, the upright is wholly ahead of the circuit boards and stands on
the chassis, the camera clears the upright at the tilt set, the ribbon starts
in the Jetson's connector, ends in the camera's and passes through nothing,
and every screw is in the part it holds. The limits of that check: it does not
test Waveshare's own parts against each other, and it tests their vertices
against the added shapes, so a large flat face passing clean through one could
be missed.

## Other simulators

```bash
python -m jetbot_twin export            # export/jetbot.urdf, jetbot.sdf, world.sdf
```

URDF and Gazebo SDF generated from the same `hardware.py`: same mass, inertia,
geometry and friction. They do **not** carry the electrical model. Gazebo's
diff-drive plugin commands wheel speed directly, which skips exactly the part
of the robot this project is about. Use them for geometry and sensors.

The SDF is checked only for being well-formed XML: the Gazebo on this machine
would not start (`gz sdf` fails on a missing `libtinyxml2`), so it has not been
loaded in a simulator.
