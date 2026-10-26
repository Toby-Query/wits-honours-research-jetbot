# Robot 2, ten spins: what will be tested, fixed in advance

Written 2026-10-26, before the runs.

## The twin

`results/calibrated_robot2_caster.json`, unchanged. Its caster's sideways
friction was fitted to spins 2 and 3 of 2026-10-22; these ten are new, and
nothing is fitted to them. Each spin is compared with the twin replayed on
that spin's logged command times and rest voltage. At a full pack and the
nominal timing the twin gives about 990 deg (2.75 turns).

## The runs

`spin` (-0.4 / +0.4 for 2 s, from rest) ten times, with
`tools/straights.py --experiment spin --folder data/real/spins_robot2 -n 10 --total 10`,
the robot put back in the middle of one 50 cm tile between spins. Pack
charged, nothing on the tyres. One continuous top-down video, normal (not slow
motion), close.

## The measurement

As `tools/spin_shape.py`: whole quarter-turns counted by eye on a strip of
frames, the rest from the boards' edges at rest before and after each spin.
Each spin is found in the video by the spacing of the logs.

## The tests

1. Mean of (robot minus twin) over the ten, with a 95 % interval.
2. The robot's own spread (standard deviation), and whether the twin is inside
   the robot's range.

No spin is excluded except for a failed measurement or one marked bad at the
time, each recorded with its reason.

## Amendment, 2026-10-26 (before any spin was run)

The ten spins are run as one experiment, `spins_10`: ten spins of 2 s, each
followed by 2 s at rest, without putting the robot back between them (each
wanders a few centimetres; the camera keeps it in view). One run, one video,
started by `real run spins_10`. Each spin is measured between the rest before
it and the rest after it. Everything else above is unchanged.
