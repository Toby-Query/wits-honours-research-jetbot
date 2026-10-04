# Results: the twin against the real JetBot

Session `2026-10-04`, robot at 192.168.1.109. Logs are in
[data/real/2026-10-04/](../data/real/2026-10-04/), figures in
[results/2026-10-04/](../results/2026-10-04/). Reproduce with:

```bash
python -m jetbot_twin compare --session 2026-10-04               # as designed
python -m jetbot_twin calibrate --session 2026-10-04
python -m jetbot_twin --calibrated compare --session 2026-10-04
```

**Only the two stationary experiments have been run so far.** Nothing about
the motors, the driver chip or the motion has been tested yet.

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

## Still to do

In the order of [VALIDATION.md](VALIDATION.md): the wheels-up sweep (which
also settles which driver chip is fitted), the stall, then the floor tests.
**Charge the pack first.**
