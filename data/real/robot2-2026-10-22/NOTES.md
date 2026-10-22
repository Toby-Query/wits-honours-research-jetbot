# Robot 2, bench session 2026-10-22

Robot 2 at 192.168.1.104, running robot 1's SD card. Jetson serial
1422119075230 (../probe_robot2.json). Its OLED is broken and its camera is
disconnected.

| File | What | Status |
|---|---|---|
| rest.csv | 20 s at rest | good |
| identify.json | driver-chip test, wheels off the ground | **TB6612FNG** (128 mV dip on its channels, none on the DRV8870's) |
| jetson_load.csv | CPU load step | good (third attempt) |
| jetson_load_corrupt1.csv.bad, jetson_load_corrupt2.csv.bad | the same | last 10 s corrupt: agent bug, see below |
| stall.csv | both wheels held by hand, 0.5 for 1.5 s | good: 574 mV sag (robot 1: 519 mV) |
| wheels_up_sweep.csv | on a stand, filmed in slow motion with tape on a hub | log good; video to be read |
| spin_1.csv, spin_2.csv, spin_3.csv | -0.4/+0.4 for 2 s on one tile, filmed from above | logs good; videos to be read. spin.csv is a copy of spin_1 (the loader reads spin.csv) |

Fitted from rest + jetson_load + stall (results/calibrated_robot2.json).
motor_r is to be refitted once the wheels-up sweep is in, as the stages run
in order:

| | Robot 1 (hardware.py) | Robot 2 |
|---|---|---|
| cell_r0, pack series resistance | 0.0575 ohm | 0.0742 ohm (+29 %) |
| r_5v_path, regulator to Jetson | 0.49 ohm | 0.52 ohm (+6 %) |
| motor_r, winding resistance | 3.45 ohm | 3.15 ohm (-9 %) |
| Jetson idle power | 1.15 W | 0.93 W (no OLED or camera) |
| Jetson 5 V under CPU load | 4.50 V | 4.52 V |

The 5 V sag below the Jetson's 4.75 V minimum is on both robots, so it is the
design, not one unit.

## Two faults found in this session

1. **Agent bug, also present in robot 1's data.** The CPU-load processes
   inherited run()'s SIGTERM handler. When they were stopped they talked to
   the PCA9685 over the shared /dev/i2c-1 file. That changed the file's
   slave address under the main process, which cached it and went on reading
   the PCA9685 (SUBADR1 = 0xE2, MODE2 = 0x04) as the INA219: 29.04 V and
   0.1028 A. Fixed in robot/agent.py (the burners reset those handlers; the
   address is set on every transaction). In robot 1's 2026-10-04
   jetson_load.csv it cost 23 samples (30.16 to 30.44 s), outside the windows
   the pack and rail fits use, so those fits stand.
2. **I2C bus jam (hardware).** About 12 minutes after boot, bus 1 began
   "arb lost / timed out for bus clear" on every device, and a Jetson reboot
   did not clear it (the expansion board stays powered). A full power-off
   cleared it. The stock jetbot_display container had been polling the
   broken OLED (0x3c) every 10 s since boot. It is now set not to restart
   (`docker update --restart=always jetbot_display` undoes that). Watch for
   a recurrence.

## The first wheels-up sweep video is unreliable above command 0.4

wheels_up_sweep_slowmo.mp4 (Samsung slow motion, 239 fps by the hold
spacing). The first five holds are 717-721 frames apart, as 3 s at 240 fps
should be; from the sixth on they are 608-624 apart, and in the 0.50 and 0.80
holds 17 % and 30 % of frames repeat the one before. The phone dropped and
duplicated frames, so frame count is not time there. Correcting with the
robot's clock gives speeds that go down as the command goes up, so no
correction was trusted. Median-step speeds (tools/wheel_tape.py now uses the
median step, which reproduces robot 1's 2026-10-04 table to 1-3 %):

| cmd | 0.10 | 0.15 | 0.20 | 0.25 | 0.30 | 0.40 | 0.50 | 0.60 | 0.80 | 1.00 |
|---|---|---|---|---|---|---|---|---|---|---|
| robot 2, rpm | 42.2 | 68.2 | 94.2 | 118.2 | 144.7 | 192.0* | 241.0* | 290.0* | 339.1* | 384.8* |
| robot 1, rpm | 42.4 | 71.2 | 98.5 | 123.4 | 152.1 | 211.2 | 268.0 | 316.7 | 415.4 | 475.6 |

\* unreliable. The motor fit was taken back out of calibrated_robot2.json;
motor_r from the stall stays. To redo: the sweep filmed at normal 60 fps.
The values above are in measurements_sweep1_unreliable.toml.txt.

## Spins, from video

Spin 1 was not filmed. Spins 2 and 3 were (spin_2.mp4, spin_3.mp4, slow
motion), read with tools/spin_shape.py: whole quarter-turns counted by eye on
spin_N_sheet.jpg (the camera ribbon, up -> left -> down -> right, 11
quarter-turns each), the rest from the boards' edges at rest.

| | rotation | battery at rest |
|---|---|---|
| spin 2 | 983.9 deg (2.73 turns) | 11.71 V |
| spin 3 | 996.3 deg (2.77 turns) | 11.70 V |
| robot 1, 2026-10-04 | 1033-1037 deg | 11.94 V |

The floor fit (mu_caster from these; gear_eff kept from robot 1, which used
robot 1's straights, not robot 2's) waits for a good wheels-up sweep, since
the spin depends on the motor numbers too.
