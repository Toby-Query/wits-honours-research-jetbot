# Robot 2: fixing the caster, and how the fix is tested

Written 2026-10-26, before the refit was run. The first test
([PREREGISTRATION.md](PREREGISTRATION.md), [RESULTS.md](RESULTS.md)) stands as
it was; this is a second round, with a changed twin.

## Why

On the 40 runs that went straight, the twin was 8.2 % short every time. The
cause found ([RESULTS.md](RESULTS.md)): the twin's caster ball has one drag
coefficient, fitted to the spins, where the ball is pushed sideways, and the
same drag then holds it back when driving straight, where the ball rolls.

This idea came from seeing all fifty runs. So the new model's form is not
blind to them, and the report says so. What is kept blind is the fitting.

## The change to the twin

The caster's resistance is split into two parts, each acting on its own
component of the caster's velocity:

- **along the robot's heading** (rolling): `mu_caster_roll x N_caster`;
- **across it** (scrubbing, as in a spin): `mu_caster x N_caster`.

With `mu_caster_roll` unset, the twin behaves exactly as before, so robot 1's
results are unchanged.

## The fit, and the test

- `mu_caster` (scrubbing): refitted to spins 2 and 3 only, as before.
- `mu_caster_roll` (rolling): fitted to **batch 1 only**, runs 1 to 5, all
  five, whatever they did.
- The two are fitted in turn until they settle (the spin barely depends on
  rolling, the straight barely on scrubbing).
- Nothing else changes: the motors, the pack and gearbox efficiency stay as
  in `results/calibrated_robot2.json`.

**Tested on runs 6 to 50 (45 runs), which nothing is fitted to,** with the
same four tests as the first round: the spread test, the bias with a 95 %
interval over batch means (batches 2 to 10), within- and between-batch
spread, and drift and turn. The split into straight and swerved runs, at the
same 25 cm, is repeated and labelled as before.

What would count as the fix working: the bias on the 45 test runs no longer
clearly above zero, and the straight-going runs' bias within a few percent.
