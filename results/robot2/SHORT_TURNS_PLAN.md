# Robot 2, short turns: what will be tested, fixed in advance

Written 2026-10-26, before the run.

## The twin

`results/calibrated_robot2_caster.json`: robot 2's calibration with the
caster rolling and scrubbing. Nothing is fitted to the turns.

Its prediction for `short_turns_24` (24 turns of -0.3 / +0.3 for 0.29 s from
rest, 1.2 s apart) at the nominal timing: **93.6 deg per turn**. Each real
turn is compared with the twin replayed on that turn's logged command times
and the run's rest voltage. (For reference, before the caster change the
same twin gave 94.8 deg.)

## The run

`short_turns_24` once, robot 2, on one 50 cm tile, pack charged, filmed from
above in normal video (not slow motion), close, with nothing on the tyres.

## The measurement

Each turn is a little under or over a quarter-turn, and the robot rests 1.2 s
between turns, so each turn's angle is a quarter-turn plus the change in the
angle of the boards' edges between the rest before and the rest after
(as `tools/spin_shape.py`, between -45 and +45 deg). A turn outside 45 to
135 deg would make this ambiguous; it is checked on a frame strip by eye.

## The tests

1. **Bias:** mean of (robot minus twin) over the 24 turns, with a 95 %
   interval. Robot 1's twin was 12.6 % low on the same turn.
2. **Spread:** the robot's standard deviation over the 24 (robot 1: 7.6 deg),
   and whether the twin's prediction is inside the robot's middle 95 %.
3. **Trend** through the 24.

No turn is excluded except for a failed measurement, recorded with its reason.
