# Results: the twin against the real JetBot

Session `2026-10-04`, robot at 192.168.1.109. Logs are in
[data/real/2026-10-04/](../data/real/2026-10-04/), figures in
[results/2026-10-04/](../results/2026-10-04/). Reproduce with:

```bash
python -m jetbot_twin compare --session 2026-10-04               # as designed
python -m jetbot_twin calibrate --session 2026-10-04
python -m jetbot_twin --calibrated compare --session 2026-10-04
```

**Done so far:** the two stationary experiments, the driver-chip test, the
wheels-up sweep and the stall. **Not yet:** anything on the floor.

## What the probe found

| | |
|---|---|
| Jetson | Nano developer kit, JetPack 4.5 (L4T R32.5), Python 3.6.9 |
| I²C devices | 0x3C OLED, 0x41 INA219, 0x60 PCA9685 (0x70 is its all-call) |
| Battery monitor | INA219, so this is the later board revision |
| PCA9685 | asleep, prescaler at its power-on default: nothing had touched the motors since boot |
| Pack | **10.27 V at rest: about 13 % charged** |
| Jetson software | the `jetbot` package is not installed on the host (the image keeps it in Docker), and there is no Python I²C library |

The addresses match the schematic's strapping. The last row changed the agent:
it now talks to `/dev/i2c-1` directly when no I²C library is present. The
Jetson's own power monitor is readable only by root on this image, so its
files were made world-readable for the session (`chmod a+r`, undone by a
reboot).

## Experiment 1 and 2: rest, and a known load step

The Jetson's CPUs were loaded for 20 s. The Jetson measured its own draw rising
from 1.12 W to 5.03 W.

![Bus voltage through the load step, robot and twin](img/real_jetson_load.png)

| | Robot | Twin as designed | Twin calibrated |
|---|---|---|---|
| Bus voltage lost to the load step | 266 mV | about 150 mV | 266 mV (fitted) |
| Bus voltage error over the run, RMS | | 80 mV | 30 mV |
| 5 V at the Jetson, idle | 4.96 V | 5.118 V | 4.95 V |
| 5 V at the Jetson, loaded | **4.50 V** | 5.118 V | 4.49 V |
| 5 V error over the run, RMS | | 453 mV | 34 mV |
| Jetson idle power | 1.1 W | 2.5 W (assumed) | taken from the robot |

The RMS figures for the calibrated twin are about the size of the bus
reading's own scatter at rest (23 mV), so on this experiment the twin is as
close as the sensor can show. That is one experiment and two fitted numbers;
it says the pack and rail models have the right form, not that the twin is
validated.

### Finding 1: the Jetson's 5 V sags to 4.5 V under load

The twin, built from the schematic, held the Jetson's supply at 5.118 V
whatever the load. The robot's does not: it falls 0.46 V for 0.89 A more
current. That is **0.55 Ω between the regulator and the point where the
Jetson measures its supply**: header pins, carrier traces and the carrier's
input switch, none of which is on the expansion board's schematic.

4.50 V is below the 4.75 V the Jetson Nano is specified for, with only the
CPUs loaded, no GPU work and no motors running. The check that reported "the
Jetson is safe in every scenario" was wrong, and it was wrong because of
something the twin did not contain. `r_5v_path` is now a parameter of the
model; `python -m jetbot_twin --calibrated check` shows the consequence.

Caveat: this rests on the Jetson's own INA3221 reading of its input voltage,
which I have not checked against a multimeter. A meter on the 40-pin header's
5 V pin under load would confirm it and would show how much of the drop is
before the header and how much after.

### Finding 2: the pack's resistance is about twice what was assumed

Fitted `cell_r0` is 0.125 Ω against an assumed 0.060 Ω, which puts the whole
path from cells to rail at about 0.55 Ω instead of 0.35 Ω. Two things to keep
in mind: the pack was nearly empty, and cells have more resistance when empty;
and the fit cannot separate the cells from the holder contacts. Repeat this on
a full pack before using the number for anything.

### Finding 3: the Jetson idles at 1.1 W, not 2.5 W

With no desktop session active and nothing running. The assumed figure was
pessimistic by more than half.

## Which driver chip: the TB6612FNG

Without opening the robot. The two possible chips are wired to different
PCA9685 channels, so `robot/agent.py identify` drives one set at a time
(wheels off the ground, one second each at half command) and watches the
battery rail:

| Channels driven | Rail dipped by |
|---|---|
| 0–3 only (DRV8870 inputs) | 0 mV |
| 8–13 only (TB6612FNG pins) | 204 mV |

So this board has the TB6612FNG, and `board` in `hardware.py` is now
`"tb6612"`. Of the two sets of predictions made beforehand, the ones that
apply are the TB6612FNG ones: slow decay, a nearly straight speed-against-command
line starting at about 0.1, and braking when the command returns to zero. The
wheels-up sweep will test those.

This corrects a guess of mine: I had taken the INA219 to mean the later,
DRV8870 board. It does not.

It also exposed a bug in the agent, which had never driven a real PCA9685:
it sent each PWM setting as one four-byte write, which the chip only spreads
over four registers when its auto-increment bit is set. The first run of this
test therefore moved nothing. The agent now writes one register at a time, as
the Adafruit library does, and reads one back to confirm.

## Experiment 3: the wheels-up sweep (pack charged, 12.2 V)

Ten commands from 0.1 to 1.0, two seconds each, wheels in the air. Run twice.
The second run was filmed in slow motion with a strip of tape on the hub, and
`tools/wheel_tape.py` reads the wheel's angle from the tape in every frame.
The true frame rate, 240.3 fps, comes from the holds themselves, which start
exactly 3 s apart. Nothing is aliased, and speed within a hold is steady to
4–10 rpm.

![Bus voltage through the sweep, robot and twin](img/real_wheels_up_sweep.png)

| Command | Robot, rpm | Twin as designed | Twin calibrated |
|---|---|---|---|
| 0.10 | 43 | 30 | 34 |
| 0.15 | 71 | 56 | 62 |
| 0.20 | 99 | 83 | 90 |
| 0.25 | 125 | 107 | 115 |
| 0.30 | 157 | 133 | 143 |
| 0.40 | 213 | 187 | 198 |
| 0.50 | 268 | 238 | 252 |
| 0.60 | 319 | 290 | 307 |
| 0.80 | 421 | 394 | 414 |
| 1.00 | 484 | 496 | 519 |
| error, rms | | 22 rpm (14 %) | 16 rpm (8 %) |

"As designed" is the twin with the motors as originally assumed and only the
pack fitted. "Calibrated" has the motor's speed constant, friction and the
split between constant and speed-dependent friction fitted to this table.

**The predictions held.** Against what was recorded beforehand for the
TB6612FNG ([predictions.txt](../results/predictions.txt)):

| | Predicted, 5–95 % | Measured |
|---|---|---|
| Lowest command that turns a free wheel | 0.10 | 0.10 |
| Free wheel at 0.5 | 199 – 284 rpm | 268 rpm |
| Free wheel at 1.0 | 413 – 596 rpm | 484 rpm |
| Shape | a straight line from 0.1 | a straight line from 0.1 to 0.8 |
| When the command returns to zero | brakes | stops in 0.09–0.20 s |

**The motors are 6 V units running about 6 % fast.** The fitted speed
constant is 255 rpm at 6 V against Waveshare's "240 RPM". At a command of 1.0
they are at twice their rated voltage.

**The dynamics agree without being fitted.** At a command of 0.5 the wheel
reaches 63 % of its speed in 83 ms and stops in 162 ms; the twin gives 80 ms
and 140 ms. Nothing was adjusted to get those: they follow from the assumed
rotor inertia and the braking the driver chip does.

**What the twin still gets wrong.** After calibration it is 7–17 rpm low from
0.1 to 0.8 and 35 rpm high at 1.0. The cause is one feature of the real motor:
its speed stops rising in proportion at the top. From 0.6 to 0.8 it gains
102 rpm; from 0.8 to 1.0, 63, where a straight line would give about 105. And
at 1.0 the robot draws *less* current than the twin (156 mV of sag against
178), not more. Slower and drawing less means the motor is seeing less voltage
than the twin thinks, or its back-EMF constant is not constant. I tried adding
a loss that grows with the square of speed (`motor_drag2`); it did not help,
because it would raise the current, which is already too high. This is
unexplained. Holds at 0.85, 0.9 and 0.95 would show whether the bend is
gradual or sudden, which would narrow it down.

**Two runs, and the floor they set.** The voltage dips in the two runs differ
by 24 mV rms, hold for hold. Much of that is the Jetson's own draw wandering.
The calibrated twin's dips differ from the robot's by 21 mV rms: as close as
the robot is to itself.

**The first run, at 30 fps with no tape**, is in `run1/`. It had to be read
from the hub's eight identical holes, known only modulo 225 rpm, and came out
about 5 % low. The motor's pitch on its soundtrack, taken as rotor speed and
divided by the 48:1 gearing, was within 2 % of the slow-motion figures from
0.25 up, which also confirms the gear ratio. The soundtrack puts the PWM at
about 1560 Hz rather than the 1526 Hz the chip's arithmetic gives: its
oscillator runs about 2 % fast, within tolerance.

## Experiment 4: stall

Both wheels held, command 0.5 for 1.5 s.

| | Robot | Twin as designed | Twin calibrated |
|---|---|---|---|
| Bus voltage lost | 486 mV | 443 mV | 487 mV (fitted) |
| Stall current, each motor | not measurable | 1.31 A | 1.48 A |

Fitting the winding resistance to that dip gives 3.45 ohm against an assumed
4.0. It is inferred through the pack resistance, not metered, so an error in
one is an error in the other.

## The pack again, charged

The load step was repeated on the charged pack. `cell_r0` came out at
0.0575 ohm, against 0.125 on the flat pack and 0.060 assumed. So the original
assumption was right for a charged pack, and the pack's resistance roughly
doubles as it empties. The twin has one number for it and no dependence on
charge: a known gap. The 5 V path resistance came out the same both times
(0.49 and 0.55 ohm), and the Jetson's supply again fell to 4.57 V under load.

## An unexplained power loss

At about 14:15 the robot lost power abruptly: no shutdown messages in its
log, a few minutes after the driver-chip test had finished and released the
motors, idle, with the charger delivering 1.8 A and the pack at 11.3 V. Not
a low-battery cut-out at that voltage. Candidates are a cell or the switch
losing contact while the robot was being handled, or the protection chip
tripping; the logs cannot distinguish them. It has not recurred.

## What changed in the twin as a result

| | Was | Now | From |
|---|---|---|---|
| `board` | drv8870 (a guess) | tb6612 | driver-chip test |
| `r_5v_path` | absent | 0.49 ohm | load step |
| `cell_r0` | 0.060 assumed | 0.0575 | load step, charged |
| `motor_v_rated` | 6 V assumed | confirmed | wheels-up sweep |
| `motor_rpm_noload` | 240 rpm | 255 rpm | wheels-up sweep |
| `motor_i_noload` | 0.16 A | 0.197 A | wheels-up sweep |
| `motor_coulomb_fraction` | 0.7 | 0.56 | wheels-up sweep |
| `motor_r` | 4.0 ohm | 3.45 ohm | stall |
| `jetson_p_idle` | 2.5 W | 1.15 W | rest |

## Still to do

The floor tests (`straight`, `spin`, `step_response`) and the held-out
`square`. Extra holds near full command, to look at the bend. And the hand
measurements: the robot's mass, the wheel's diameter, the cells'
label.
