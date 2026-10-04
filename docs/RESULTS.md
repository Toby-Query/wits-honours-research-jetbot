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

## Experiment 3: the wheels-up sweep (pack charged, 12.35 V)

Ten commands from 0.1 to 1.0, two seconds each, wheels in the air, filmed.
Wheel speed is read from the video by `tools/wheel_video.py`: the hub's eight
holes give the wheel's angle frame by frame, and the motor's pitch on the
soundtrack gives an independent check.

![Bus voltage through the sweep, robot and twin](img/real_wheels_up_sweep.png)

| Command | Robot, rpm | Twin as designed | Twin calibrated | Sound check, rpm |
|---|---|---|---|---|
| 0.10 | 40 | 30 | 24 | 39 |
| 0.15 | 68 | 57 | 52 | 62 |
| 0.20 | 94 | 84 | 79 | 93 |
| 0.25 | 120 | 109 | 105 | 128 |
| 0.30 | 147 | 136 | 132 | 156 |
| 0.40 | 200 | 190 | 187 | 215 |
| 0.50 | 252 | 241 | 239 | 272 |
| 0.60 | 306 | 293 | 293 | 323 |
| 0.80 | 416 | 398 | 399 | 425 |
| 1.00 | 464 | 502 | 504 | 488 |

**The prediction held.** The twin as designed, before anything about the
motors had been measured, is within 10–17 rpm of the robot from 0.1 to 0.8.
Against the predictions recorded beforehand for the TB6612FNG
([predictions.txt](../results/predictions.txt)):

| | Predicted, 5–95 % | Measured |
|---|---|---|
| Lowest command that turns a free wheel | 0.10 | 0.10 |
| Free wheel at 0.5 | 199 – 284 rpm | 252 rpm |
| Free wheel at 1.0 | 413 – 596 rpm | 464 rpm |
| Shape | a straight line from 0.1 | a straight line from 0.1 to 0.8 |
| When the command returns to zero | brakes | stops within about 0.15 s (from the sound) |

**The motors are ordinary 6 V units.** Fitting the motor's speed constant to
these speeds gives 240.5 rpm at 6 V against Waveshare's "240 RPM". So the
assumption the twin was built on was right, and so is its consequence: at a
command of 1.0 these motors are run at twice their rated voltage.

**Calibration did not improve the speeds**, and that is worth saying plainly.
The calibrated twin is 13–17 rpm low on nine holds and 39 rpm high on the
tenth; the as-designed twin was 10–17 low and 38 high. What calibration fixed
is the current: the voltage dips now match much better (see the stall, below).
The speed error that remains is one the model cannot remove by changing its
numbers, for a reason visible in the table: **the robot's speed stops rising in
proportion at the top.** From 0.6 to 0.8 it gains 110 rpm; from 0.8 to 1.0,
only 48. The twin's motor is linear and has no such bend, so the fit splits the
difference. Either the real motor genuinely falls away near full voltage
(brush and iron losses rising with speed, which the twin does not model), or
the 1.0 reading is wrong.

**How far to trust the speeds.** The video is 30 frames a second, and the
hub's eight holes look the same every 45 degrees, so each reading is only
known modulo 225 rpm. They were unwrapped by assuming speed rises with
command. The sound agrees with every reading to within 8 %, but runs
consistently about 6 % above the picture, which is unexplained (it would fit a
gear ratio of about 51 rather than 48, or a bias in picking the tone). A
slow-motion retake with a strip of tape on the tyre would remove the
unwrapping assumption and settle the top two points.

Also from the soundtrack: the PWM whine is at about 1560 Hz, not the 1526 Hz
the chip's arithmetic gives. The PCA9685's oscillator is running about 2 %
fast, within its tolerance.

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
| `motor_v_rated`, `motor_rpm_noload` | 6 V, 240 rpm assumed | confirmed | wheels-up sweep |
| `motor_i_noload` | 0.16 A | 0.237 A | wheels-up sweep |
| `motor_r` | 4.0 ohm | 3.45 ohm | stall |
| `jetson_p_idle` | 2.5 W | 1.15 W | rest |

## Still to do

The floor tests (`straight`, `spin`, `step_response`) and the held-out
`square`. A slow-motion retake of the wheels-up sweep with tape on the tyre.
And the hand measurements: the robot's mass, the wheel's diameter, the cells'
label.
