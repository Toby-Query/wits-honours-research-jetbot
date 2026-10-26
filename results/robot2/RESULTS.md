# Robot 2, 50 straights against the twin: results

Test plan, fixed beforehand: [PREREGISTRATION.md](PREREGISTRATION.md).
Reproduce:

```bash
# per batch video: the robot's start and end at rest, matched to the logs
.venv/bin/python tools/straights_video.py data/real/straights_robot2/batch01.mp4 \
    data/real/straights_robot2/straight_0{1,2,3,4,5}.csv \
    --out data/real/straights_robot2/batch01_video.json          # ... to batch10
.venv/bin/python tools/straights_analysis.py --camera-height 1.01
```

Per-run numbers: `straights_result.json`. Figure: `straights_result.png`.

## The runs

`straight_short` (0.5 on both wheels for 1.5 s), 50 runs in 10 batches of 5,
all on 2026-10-23 between 17:54 and 18:17, on the 50 cm tiles. None was marked
bad. The motors were on for 1.487 to 1.503 s every time. The pack went from
12.39 to 12.25 V. The breaks between batches were 30 to 90 s, not the 5 to 10
minutes the amendment asked for, so the batches are close together in time.

Filmed from 1.01 m above the floor (Samsung S20 FE, 0.5x, 1440 x 1440 at
30 fps), one video per batch.

## How the distances were measured

`tools/straights_video.py`, written for these videos (several runs each,
with someone resetting the robot in between):

- **Ruler.** The tile joints, 4 x 3 of them. The 12 crossings land on a
  0.5 m grid to 2.4 mm RMS, and within 2.1 mm along the row the robot drives
  on. The joint beside the path is straight to 0.4 px over 1200 px, so the
  ultra-wide lens's bending is corrected where it matters.
- **Which runs.** Matched to the logs by the gaps between runs on the
  robot's clock. Every run starts within 0.06 s of where its log says.
- **Where the robot is.** At rest before and after each run, the whole
  robot, picked out as what differs between the frame before and the frame
  after. Thin parts (the antenna, the ribbon loop) are left out, because they
  show against some backgrounds and not others. Changing how much is left out
  moves the distances by under 5 mm. Following the green chassis alone was
  tried first and was about 10 cm short: only parts of it catch the light,
  and different parts at each end.
- **Height.** The camera sees the robot from above, not its wheels on the
  floor, so every distance looks too long by H / (H - h), with H = 1.01 m
  the phone's height. The h that applies to the whole outline was
  **calibrated against robot 2's lit green LED**, measured by ruler at
  8.5 cm above the floor (the fan top is 12.4 cm). The tool finds the LED to
  a fraction of a pixel in the same still frames. It is hidden behind the
  antenna in some positions, and clearly in view at both ends in 22 runs.
  On those runs, the outline's distances match the LED's if the outline is
  taken as **5.3 cm** up (sd 0.6 cm over runs; 5.0 cm for runs that went
  straight, 5.9 cm for those that swerved). It is lower than the robot's top
  because at the start mark, near the edge of the picture, the camera also
  sees the side panels and wheels. So the distances are multiplied by 0.9475.
  The range 5.0 to 5.9 cm moves them by +/-0.5 %. (A first estimate from the
  parts list alone, 8.7 cm, was too high: it understated every distance by
  about 3.7 %.)
- **Drift and turn** are measured against the direction of the first 0.3 m of
  travel, which is sensitive: re-measuring with a slightly different tracker
  moved them by up to 5 cm and several degrees. Two turns (runs 21 and 49:
  147 and 92 deg) are misreads. Treat both as rough.

## Results as planned

| Test | Result |
|---|---|
| 1. Spread: middle 95 % of (robot - twin) | -6.6 to +11.7 cm: **zero is inside, PASS** |
| 2. Bias, 95 % interval over batch means | **+6.3 cm (+6.0 %)**, +4.6 to +8.0 cm (over runs: +4.8 to +7.9) |
| 3. Spread within batches / of batch means | 5.5 cm / 2.4 cm (2.4 expected if batches did not differ) |
| 4. Drift / turn (twin: 0 / 0) | +10.2 cm left (+5.7 to +14.7) / +15.8 deg (+7.4 to +24.2), rough |
| Through the session | residual falls 1.7 cm over the 50 runs |

Robot 1.120 m mean (sd 5.5 cm, range 0.93 to 1.18); twin 1.056 m. With the
outline's height at the ends of its range (5.0, 5.9 cm) the bias is +6.7 and
+5.6 cm, and the spread test passes both ways. The 22 runs measured by the
LED alone give +3.9 cm (+3.7 %), as their mix of straight and swerved runs
would.

The batches did not differ from one another beyond what five runs each would
give by chance (2.4 against 2.4 cm). That is in one session, though, so it
says nothing about day-to-day change.

## What the plan did not anticipate: the robot swerves

In 10 of the 50 runs (9, 17, 27, 31, 33, 35, 36, 40, 42, 46), the robot
swerved hard to its left: it ended 26 to 49 cm to the left, turned 30 to 60 deg,
and stopped in the shaded strip along the bottom of the picture. It did not
hit anything (the frames show open floor there). The other 40 went nearly
straight. Most of the swerves were in the second half of the session. (The
line at 25 cm is where the gap between the two groups falls; run 40, at 26
cm, is the one nearest it.)

**This analysis was decided after seeing the data**, so it is a finding to
report, not a replacement for the planned test:

| | Runs | Robot | Twin | Robot - twin, mean (95 %) | Middle 95 % of residuals |
|---|---|---|---|---|---|
| Went straight | 40 | 1.144 m, sd 1.8 cm | 1.057 m | **+8.7 cm (+8.2 %)**, +8.1 to +9.3 | +5.3 to +11.7 cm |
| Swerved | 10 | 1.024 m, sd 4.8 cm | 1.055 m | -3.1 cm (-2.9 %), -6.6 to +0.4 | -10.9 to +3.8 cm |

What this says:

1. **The planned spread test passes because of the swerves.** They widen the
   robot's spread enough to take in the twin. On the runs that went
   straight, the robot is repeatable to 1.8 cm, and the twin is short by
   8.2 % every time. On those alone, the spread test would fail.
2. **The twin under-predicts straight-line distance by about 8 %, and the
   caster model is why.** The twin treats the caster as a ball that always
   drags, with one friction number, mu_caster. That number was fitted to the
   spins, where the caster really is dragged sideways (0.506). Driving
   straight, a caster ball rolls. Checked on run 11's own timing and voltage
   (this was done after seeing the data, and nothing was refitted):

   | Twin's caster drag | Twin's distance |
   |---|---|
   | 0.506, fitted to robot 2's spins | 1.061 m |
   | 0.324, robot 1's | 1.113 m |
   | 0.02, rolling freely | 1.198 m |
   | robot 2's straight runs, measured | 1.144 m |

   gear_eff, by contrast, barely matters here: 10 % more adds 7 mm. So a
   single caster number cannot serve both a spin and a straight run, and
   calibrating it on spins carries badly to straight driving. A caster that
   rolls along its direction of travel and scrubs sideways would need two
   numbers, or a model of the ball itself.
3. **The twin cannot produce a swerve at all**: its two motors are
   identical and its tyres do not slip sideways. Something in the robot
   changes from run to run: the rear caster's direction when the run starts
   (the robot is put down by hand), one wheel starting late, or the tile
   joints. Which one is not known. The swerves becoming more frequent through
   the session points to something that changes with use or placement.

## Departures from the plan

- Distances were measured with `tools/straights_video.py`, not
  `tools/path_video.py` as the plan said: each video holds a batch of runs
  with someone resetting the robot in between, which path_video cannot
  handle. The method is described above.
- The breaks between batches were 30 to 90 s, not 5 to 10 minutes.
- The height correction was calibrated against the LED after the first
  analysis, and the numbers above replace that first analysis (which, with
  h = 8.7 cm from the parts list, gave a bias of +2.2 %). The LED's height
  was measured by ruler and nothing was fitted to the twin.

## To do, if there is time
- Film a few runs close up at the start, to see what the caster does when
  a swerve begins.

## Round 2: the caster fixed (2026-10-26)

Plan written and committed before the refit: [REFIT_PLAN.md](REFIT_PLAN.md).
Reproduce:

```bash
.venv/bin/python tools/refit_caster.py
.venv/bin/python tools/straights_analysis.py --camera-height 1.01 \
    --calibration results/calibrated_robot2_caster.json --runs 6-50 --tag refit
```

**The change.** The twin's caster now resists in two ways: rolling, along
the robot's heading (`mu_caster_roll`), and scrubbing, across it
(`mu_caster`). Before, one coefficient applied in every direction. With
`mu_caster_roll` unset the twin is exactly as before, so robot 1 is
unaffected.

**The fit.** Scrubbing from spins 2 and 3 only: **0.524** (spins matched to
7 deg RMS). Rolling from straight runs 1 to 5 only: **0.227** (matched to
0.9 cm RMS). Nothing else changed. The rolling figure is well above what a
free ball would show, so it is an effective number: it also takes up losses
the twin does not model separately (the tyres' rolling resistance is still
assumed, and gearbox efficiency is robot 1's).

**The test: runs 6 to 50, which nothing was fitted to.** Results in
`straights_result_refit.json` and `.png`.

| Test | Round 1 (50 runs) | **Round 2 (runs 6 to 50)** |
|---|---|---|
| 1. Spread test | pass | **pass** (middle 95 %: -14.5 to +3.9 cm) |
| 2. Bias, interval over batch means | +6.0 % (+4.6 to +8.0 cm) | **-1.5 % (-3.5 to +0.2 cm)** |
| 3. Within batches / batch means | 5.5 / 2.4 cm | 5.8 / 2.5 cm (2.6 by chance) |
| Robot / twin, mean | 1.120 / 1.056 m | 1.117 / 1.134 m |

The split by swerving, as in round 1 (same 25 cm line, the same ten runs):

| | Round 1 | **Round 2** |
|---|---|---|
| Went straight (35 test runs) | twin 8.2 % short | **twin 0.9 % short** (+0.3 to +1.7 cm); middle 95 % -2.6 to +3.9 cm |
| Swerved (10) | -2.9 % | -9.6 % |

**What this says.**

1. **The caster was the problem, and fixing it fixes straight-line distance.**
   On the runs that went straight, the twin went from 8.2 % short to 0.9 %,
   with every one of them within -2.6 to +3.9 cm. The spread test now passes
   on those runs alone, which round 1 failed.
2. **The overall bias is no longer clearly different from zero** (-1.5 %,
   interval -3.5 to +0.2 cm). What is left is the swerves pulling the mean
   down: the twin, which cannot swerve, now over-predicts those ten by 9.6 %.
3. **The swerves are the twin's remaining limit on straight driving.** They
   are a fifth of the runs, and the twin has no model of what causes them.

**Caveats.** The form of the fix was chosen after seeing all fifty runs;
only its numbers were fitted blind (to the spins and batch 1). Five runs is a
small set to fit the rolling number on. The swerve split is still decided
after the fact.

## Short turns and ten spins (2026-10-26)

Plans fixed beforehand: [SHORT_TURNS_PLAN.md](SHORT_TURNS_PLAN.md),
[SPINS_PLAN.md](SPINS_PLAN.md). Twin: `results/calibrated_robot2_caster.json`,
nothing fitted to these runs. Measured with `tools/turns_video.py` from
`short_turns_24.mp4` and `spins_10.mp4` (60 fps, from above).

| | Robot | Twin | Robot minus twin (95 %) |
|---|---|---|---|
| 24 short turns (0.29 s at ∓0.3, from rest) | 79.8 deg, sd 16.2 (49 to 101) | 90.3 deg | **−10.5 deg, −11.6 %** (−17.3 to −3.6) |
| 10 spins (2 s at ∓0.4) | 1039.4 deg, sd 15.6 (1012 to 1061) | 1044.4 deg | **−5.0 deg, −0.5 %** (−16.4 to +6.4) |

**Spins: the twin is right.** Within half a percent on average, inside the
robot's range, no clear bias, on ten spins nothing was fitted to.

**Short turns: the twin is not, on this robot either.** Robot 2 turns 12 %
less than its twin, where robot 1 turned 13 % more; and robot 2 is twice as
erratic from turn to turn (16 deg against 8). The twin's prediction is inside
the robot's middle 95 % only because that spread is so wide. A 0.3 s turn is
almost all starting and stopping, which the twin models only roughly, and
the two robots differ in the direction they miss.

**How the angles were measured, and two mistakes caught.** At rest between
turns the boards' edges give the angle to a quarter-turn. The whole number
of quarter-turns:

- *Short turns:* one each, checked on the frame strip
  (`short_turns_24_sheet.jpg`). The first two alignments of video to log
  were a whole turn out (the turns repeat every 1.5 s, and the outline
  flickers at rest); the strip showed each row starting mid-turn. The log is
  now placed on the video by sliding the pattern of motors-on along the
  video's motion signal, checked against the last turn's time.
- *Spins:* assuming 11 quarter-turns each was wrong: spins 2 and 4 made 12.
  The count now comes from the robot's lit LED, whose bearing from the
  robot's centre gives the heading all the way round; it agrees with the
  edges to 8 deg where both show. Where the LED is hidden behind the antenna
  (spins 1, 8, 9, 10), the count is the one nearest the LED-confirmed spins;
  spin 5 is the LED alone (±8 deg). Following the edges frame by frame was
  tried and lost quarters in the motion blur.
