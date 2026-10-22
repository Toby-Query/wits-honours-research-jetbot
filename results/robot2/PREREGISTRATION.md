# Robot 2, repeated straights: what will be tested, fixed in advance

Written 2026-10-22, before any of robot 2's straight runs. Nothing below is
changed after the runs start. Anything decided later is reported as such.

## The twin being tested

`results/calibrated_robot2.json`, built only from robot 2's bench and spin
experiments (`data/real/robot2-2026-10-22/`):

| Number | Value | From |
|---|---|---|
| cell_r0 | 0.0742 ohm | jetson_load |
| r_5v_path | 0.521 ohm | jetson_load |
| motor_rpm_noload | 248.9 | wheels-up sweep (60 fps video), holds 0.10-0.60 |
| motor_i_noload | 0.111 A | the same |
| motor_r | 3.15 ohm | stall |
| mu_caster | 0.506 | spins 2 and 3 (984 and 996 deg) |
| gear_eff | 0.514 | robot 1 (hardware.py), unchanged |

No robot-2 straight run was used. gear_eff was fitted partly on robot 1's
straight run of 2026-10-04, which is a different robot.

The twin's prediction, by battery voltage at rest, is in
`prediction_straight.json`: 1.418 m at 12.2 V, 1.380 m at 11.9 V,
1.343 m at 11.6 V. It predicts no drift and no turn.

## The runs

- `straight`, command 0.5 on both wheels for 2.0 s, from rest. 50 good
  runs, in batches of 5, with `tools/straights.py`. The logs go in
  `data/real/straights_robot2/`.
- The same 50 cm tiles, start mark, direction and camera position each
  batch. The pack is charged before each batch and the charger unplugged.
- Measured from top-down video, with the tile joints as the ruler
  (`tools/path_video.py --tile 0.5`): distance travelled, sideways drift and
  heading change.

## Exclusions

A run is excluded only when it is marked bad in `runs.csv` for a reason
recorded at the time: it touched something, it left the tiles in view, or
the video failed. Runs are not excluded for their distance.

## The comparison

For each run, the twin is replayed at that run's own rest voltage and its
logged command times (`compare.replay`). The residual is the robot's
distance minus the twin's.

1. **Spread test (pass or fail).** Pass if zero lies inside the middle 95 %
   of the 50 residuals (between the 2.5th and 97.5th percentiles). In words:
   the twin is no further from the robot than the robot is from itself.
2. **Bias (reported with its uncertainty).** The mean residual with a 95 %
   confidence interval. The interval is taken over the batch means (10
   batches, t with 9 degrees of freedom), because runs on the same day share
   conditions. The interval over all 50 runs is reported alongside it. A
   bias whose interval excludes zero is a finding, not a failure of test 1.
3. **Within-batch and between-batch spread.** The standard deviation of the
   residuals within a batch, and of the batch means, so that day-to-day
   change can be told apart from run-to-run change.
4. **Drift and heading (reported, not tested).** The twin predicts zero for
   both. Their mean, with an interval, tells whether the drift is systematic
   (the two motors differ) or scatters around zero (the floor, the start).

## Known limits, stated before the data

- The twin cannot follow robot 2's wheel speed above command 0.6, where the
  real speed flattens (`data/real/robot2-2026-10-22/NOTES.md`). The
  straights run at 0.5, inside the range it was fitted on, where it is
  within 1.1 %.
- gear_eff, tyre grip and rolling resistance are not robot 2's own.
- The twin's two motors are identical, so it cannot predict drift.
