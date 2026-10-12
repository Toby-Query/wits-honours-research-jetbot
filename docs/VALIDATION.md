# Comparing the twin with the real JetBot

This is the lab sheet. It says what to do with the robot, what each step is
for, and how the result gets back into the twin.

**This procedure was run on the real robot on 2026-10-04.** What it found is
in [RESULTS.md](RESULTS.md); this page is the procedure, with a note wherever
the session went differently from the plan. The twin's predictions are in
`results/predictions_*.json`, made before the robot was switched on. Before
that, the procedure was run end to end against a stand-in (a second twin with
altered numbers and noisy sensors) to show that it recovers known answers;
that is described at the end and is not data.

## What the robot can and cannot tell you

| The robot measures | With | Notes |
|---|---|---|
| Battery rail voltage | INA219 at 0x41 (or ADS1115 at 0x48 on older boards) | 4 mV steps |
| Charger current | the same INA219 | zero when unplugged. **Not load current** |
| Power into the Jetson | the Jetson's own INA3221 | a known load, which is useful |
| Temperatures, CPU load | sysfs | |

| It has no sensor for | So |
|---|---|
| Motor current | inferred from the voltage it costs |
| Wheel speed | film a taped wheel and count turns |
| Position | tape measure, or film from above |

The twin reports the same sensors with the same resolution
(`bus_v`, `charge_i`, `p_5v`), so like is compared with like. Everything else
the twin knows (currents, speeds, slip) is judged by whether it reproduces
what can be seen.

## Before anything else: five minutes with the robot off

These go straight into `hardware.py` as `measured(...)`. None of them should be
left to a fit.

1. **Which motor driver is on the board.** One 24-pin chip (TB6612FNG) or two
   8-pin chips (DRV8870) near the motor connectors. Set `board`.
2. **Weigh it**, cells in. Set `mass_override`.
3. **Wheel diameter**, across the tyre. Better: roll it ten wheel-turns along a
   ruler and divide. Set `wheel_radius`.
4. **Track**: tyre centre to tyre centre. The CAD says 114.9 mm.
5. **The cells**: make and capacity, off the label. Set `cell_capacity_ah`.
6. **The switch**: any marking on S1.
7. **Motor resistance**, if you have a multimeter: unplug one motor, measure
   across its leads, turning the wheel slightly by hand to find the lowest
   reading. Set `motor_r`.

In the 2026-10-04 session only the wheel was measured (item 3). The driver
chip was identified without opening the robot, by driving each chip's PWM
channels in turn and watching the battery rail (`robot/agent.py identify`).
The mass, the cells' label, the switch and the motor resistance by meter are
still to do.

## Connecting

```bash
ssh-copy-id jetbot@<ip>                           # once; the password is jetbot
python -m jetbot_twin real probe --host jetbot@<ip>
```

`probe` copies `robot/agent.py` across and asks it what it finds: the I²C
devices, which battery monitor is fitted, the PCA9685's prescaler, the
Jetson's power rails, and the `motor.py` actually installed (so it can be
checked against what the twin assumes). It moves nothing. The answer is saved
to `data/real/probe.json`.

`robot/agent.py` is one file, needs only what the JetBot image already has,
and writes to the PCA9685 the same register values the twin simulates. It
stops the motors when it finishes, when it fails, and when the connection
drops, and it refuses any profile longer than three minutes.

## The experiments

```bash
python -m jetbot_twin experiments                 # this list, from the code
python -m jetbot_twin run straight                # what the twin expects
python -m jetbot_twin real run straight --host jetbot@<ip> --session 2026-10-05
```

Each `real run` says how to set the robot up, waits for you, counts down, runs,
and saves `data/real/<session>/<name>.csv`. Things measured by hand go in
`data/real/<session>/measurements.toml`; there is a template in
[data/real/measurements.example.toml](../data/real/measurements.example.toml).

Do them in this order. Each one is arranged so that only a few of the twin's
unknowns can explain its result.

| # | Experiment | Set-up | Settles | Write down |
|---|---|---|---|---|
| 1 | `rest` | on the bench | idle power, state of charge | |
| 2 | `jetson_load` | on the bench | **pack resistance** | |
| 3 | `wheels_up_sweep` | on a stand, wheels free | **motor constant, friction, dead band** | wheel turns per hold |
| 4 | `stall` | hold both wheels | **winding resistance** | |
| 5 | `straight` | floor, 3 m clear | gearbox efficiency, rolling friction | distance, roll-on, drift |
| 6 | `spin` | floor | caster friction | rotation, wander |
| 7 | `step_response` | floor | inertia; forward/back asymmetry | final offset |
| 8 | `square` | floor | **nothing: this is the test** | where it ended up |

Why each one works:

**2, the Jetson load step.** The Jetson's CPU is loaded for 20 s. The Jetson
measures its own power, so the extra current is *known*, and the voltage it
costs gives the pack's resistance without the motors being involved. Every
later inference about motor current depends on this number, which is why it
comes first.

**3, wheels up.** With no floor and no mass, speed depends only on the motor
and its driver. The shape of speed against command also shows which driver
chip is fitted (see [ELECTRICAL.md](ELECTRICAL.md)): a dead band then a steep
rise, or a straight line.

**4, stall.** A motor that is not turning has no back-EMF, so the sag is
resistance alone. Hold the wheels firmly; it is 1.5 s.

**5 and 6, the floor.** By now the electrical side is pinned down, and what
is left is mechanical. They run at a command of 0.5 and 0.4, clear of the
dead band: a robot that is barely moving tells you about static friction and
nothing else. If your robot runs smoothly at lower commands, lower `FLOOR` and
`SPIN` in `experiments.py`.

**8, the square.** Not used in any fit. After calibration the twin predicts
where an open-loop square ends; the robot does it; the difference is the
result.

What the session added and left out. The square's corners came out wrong, so
its corner was run on its own: `short_turns` (ten times) and `short_turns_24`,
which measure how repeatable a quarter-turn is. `square_small` is the square
with shorter sides, to fit under a phone held still. `step_response` was not
run. Wheel speed, heading and path were read from video with the tools in
`tools/`, not by counting turns or with a tape measure.

Two things to do differently, both learned the hard way: take any tape used
as a wheel marker off the tyre before the floor tests, and check from the log
how long each command really lasted before trusting a short manoeuvre.

## Comparing and calibrating

```bash
python -m jetbot_twin compare   --session 2026-10-05          # twin as designed
python -m jetbot_twin calibrate --session 2026-10-05 --board drv8870
python -m jetbot_twin --calibrated compare --session 2026-10-05
```

`compare` replays each experiment on the twin, giving it the robot's commands,
the Jetson power the robot measured, and the state of charge that matches the
robot's resting voltage. It reports the voltage error and sag for each driven
segment and each hand measurement against the twin's value, and writes an
overlay figure per experiment to `results/<session>/`.

`calibrate` fits in stages ([jetbot_twin/calibrate.py](../jetbot_twin/calibrate.py)),
each stage using one experiment and a few numbers, holding what earlier stages
found. It writes `results/calibrated.json`. When you are satisfied, move those
values into `hardware.py` as `measured(...)`.

Two things are deliberately not fitted:

- **Geometry.** Measure the wheel and the track. A fit will shrink a wheel to a
  third of its size to absorb an error that belongs elsewhere (it did, in an
  early version of this procedure).
- **The driver chip**, if you can look. If `--board` is not given, `calibrate`
  fits the wheels-up stage under each hypothesis and keeps the one that
  explains the data. It warns if the two are close.

A fitted value that stops at its bound is flagged. That means the model cannot
explain the data by moving that number, and the number should not be believed.

## What to report

For a comparison that would stand up:

1. **The predictions made beforehand** (`results/predictions_*.json`): a
   median and a 5–95 % band for each measurable quantity, from the stated
   uncertainty in each unverified number. Did the robot land inside the band?
2. **The as-designed error**, experiment by experiment.
3. **What calibration changed**, and whether each change is physically
   reasonable.
4. **The held-out square**, before and after calibration.
5. **What still does not fit.** That is the interesting part: it is where the
   model is wrong rather than merely mis-set.

Things I expect not to fit, so you are not surprised:

- **Straight-line drift.** The twin's motors are identical. Yours are not.
- **Voltage recovery after a load.** One RC pair is a crude model of a cell.
- **Fast spins.** No lateral tyre slip in the twin.
- **Repeatability.** Run `straight` five times before trusting any one run;
  the spread between runs is the floor under any model error.

How that list fared: the drift was there (about 2.6 % between the motors).
The two-second spin fitted to 6 %, so lateral slip was not the problem. What
did not fit was not on the list: short turns, and the top of the speed range.
And the advice on repeatability was mostly not followed; only the short turn
(24 times) and the wheels-up sweep (twice) were repeated.

## The predictions, as they were before the session

`python -m jetbot_twin predict` draws 40 robots from the uncertainty in
`hardware.py` and runs each through the experiments. The full output, with
what each prediction depends on most, is in
[results/predictions.txt](../results/predictions.txt). The table below is for
the DRV8870 board, which this robot turned out not to have; the TB6612FNG
predictions in the same file are the ones the robot was held to, and
[RESULTS.md](RESULTS.md) sets them against what was measured.

| | 5 % | median | 95 % | depends most on |
|---|---|---|---|---|
| Free wheel at command 0.5, rpm | 240 | 342 | 400 | the motor's speed constant |
| Free wheel at command 1.0, rpm | 411 | 506 | 593 | the motor's speed constant |
| Lowest command that turns a wheel | 0.20 | 0.25 | 0.30 | friction, inductance |
| `straight`: distance, m | 1.23 | 1.90 | 2.43 | wheel radius, speed constant |
| `straight`: roll-on, m | 0.16 | 0.43 | 0.71 | speed constant, friction |
| `straight`: bus sag, mV | 73 | 113 | 176 | pack resistance |
| `stall`: bus sag, mV | 84 | 148 | 288 | inductance, pack resistance |
| `spin`: rotation, degrees | 425 | 911 | 1120 | inductance, friction |
| `square`: final heading, degrees | 20 | 161 | 339 | everything |

Two things to take from it. The bands are wide: a factor of two on distance,
because nobody has yet measured what the motors are. And **the open-loop
square cannot be predicted at all before calibration**; the band on its final
heading is nearly the whole circle. Small errors in speed become large errors
in a pose reached by dead reckoning. That is the honest starting point, and
how much calibration narrows it is the result. (It narrowed the sides of the
square to a few centimetres and did not narrow the corners.)

## The rehearsal

```bash
python -m jetbot_twin rehearse          # about five minutes
```

The whole procedure, run against a stand-in robot: a second twin with nine of
its numbers changed by 10–45 %, read through sensors with noise and
quantisation, and a tape measure that rounds. It is in
[jetbot_twin/synthetic.py](../jetbot_twin/synthetic.py), and every log it
produces is marked `source = synthetic`.

It is there to answer one question before real data arrives: if the twin's
numbers are wrong in a known way, does this procedure find out how? The last
run is in [results/rehearsal/rehearsal.txt](../results/rehearsal/rehearsal.txt).

It also caught two faults in the procedure itself, both now fixed: the fit
originally chose the driver chip before fitting anything and chose wrongly,
and it was allowed to fit the wheel radius. The result still predicted the
square well, with a 10 mm wheel. A good held-out score does not mean the
parameters are right.
