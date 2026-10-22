"""Drive the `straight` experiment again and again, one press of Enter each.

    .venv/bin/python tools/straights.py [-n 5] [--total 50]

The 50 runs are done in batches, a few per sitting (5 by default), so the
spread includes day-to-day changes as well as run-to-run ones. Each run is
command 0.5 for 2 s, exactly as `real run straight`. All batches go in one
folder per robot, data/real/straights_robot2/, numbered on from the last run: straight_NN.csv,
plus a line each in runs.csv (number, batch, time, battery at rest, sag, how
long the motors were on, and whether you marked it bad). The batch is
today's date unless --batch says otherwise.

At the prompt:
    Enter   drive the next run
    b       the last run was bad (bumped, off the tile, video failed):
            it is kept but marked, and does not count towards the total
    q       quit (run it again later and it carries on from where it was)

The Mac says "run 7" aloud as each run starts, so the video's sound track
labels every run. --quiet turns that off.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jetbot_twin import remote  # noqa: E402
from jetbot_twin.experiments import EXPERIMENTS  # noqa: E402

FIELDS = ["run", "batch", "file", "started", "rest_v", "sag_mv", "motors_on_s", "status"]
WARN_V = 11.5      # below this the runs get noticeably slower
STOP_V = 11.0      # charge before going on


def read_index(path):
    if not path.exists():
        return []
    with open(path) as f:
        return list(csv.DictReader(f))


def write_index(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(rows)


def summarise(log):
    # When the motors went on and off, from the agent's own record of each
    # command sent. The sample log can pause for ~0.3 s, so it is not used
    # for timing.
    sent = [e.split(":") for e in log.meta["commands_sent_at"].split()]
    times = [float(s) for s, left, right in sent]
    moving = [float(left) != 0 or float(right) != 0 for s, left, right in sent]
    t_on = times[moving.index(True)]
    t_off = next(t for t, m in zip(times, moving) if t > t_on and not m)
    t, v = log.t, log["bus_v"]
    rest = v[t < t_on - 0.05].mean()
    driving = v[(t > t_on + 0.3) & (t < t_off)].mean()
    return rest, (rest - driving) * 1000, t_off - t_on


def say(text, quiet):
    if not quiet:
        subprocess.Popen(["say", text], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="jetbot@192.168.1.104")   # robot 2
    ap.add_argument("--batch", default=dt.date.today().isoformat())
    ap.add_argument("-n", type=int, default=5, help="good runs this sitting")
    ap.add_argument("--total", type=int, default=50, help="good runs overall")
    ap.add_argument("--folder", default=str(ROOT / "data" / "real" / "straights_robot2"))
    ap.add_argument("--countdown", type=int, default=3)
    ap.add_argument("--quiet", action="store_true", help="no spoken run numbers")
    a = ap.parse_args()

    exp = EXPERIMENTS["straight"]
    folder = Path(a.folder)
    folder.mkdir(parents=True, exist_ok=True)
    index = folder / "runs.csv"
    rows = read_index(index)

    done_before = sum(r["status"] == "ok" and r["batch"] != a.batch for r in rows)
    batches = sorted({r["batch"] for r in rows} | {a.batch})
    print(f"\nStraights: command 0.5 for 2 s. Batch {batches.index(a.batch) + 1} "
          f"({a.batch}): {a.n} runs this time; {done_before} of {a.total} "
          "done before today")
    print(f"saving to {folder}")
    print("Enter = drive   b = last run was bad   q = quit\n")

    while True:
        good = sum(r["status"] == "ok" for r in rows)
        today = sum(r["status"] == "ok" and r["batch"] == a.batch for r in rows)
        number = len(rows) + 1                  # numbers are never reused
        if good >= a.total:
            print(f"All {good} runs done.")
            say("All runs done", a.quiet)
            return 0
        if today >= a.n:
            print(f"Batch done: {today} good runs today, {good} of {a.total} "
                  "overall. Run this again next time to carry on.")
            say("Batch done", a.quiet)
            return 0
        prompt = (f"Run {number}  (today {today} of {a.n}, "
                  f"overall {good} of {a.total})  > ")
        try:
            key = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if key == "q":
            return 0
        if key == "b":
            if not rows:
                print("  no run to mark yet")
            elif rows[-1]["status"] == "bad":
                print(f"  run {rows[-1]['run']} is already marked bad")
            else:
                rows[-1]["status"] = "bad"
                write_index(index, rows)
                print(f"  run {rows[-1]['run']} marked bad; it won't count")
            continue
        if key:
            print("  Enter, b or q")
            continue

        out = folder / f"straight_{number:02d}.csv"
        say(f"run {number}", a.quiet)
        started = dt.datetime.now().strftime("%H:%M:%S")
        try:
            log = remote.run(a.host, exp, out, countdown=a.countdown)
        except KeyboardInterrupt:
            print("\n  stopped; motors off. This run was not saved.")
            out.unlink(missing_ok=True)
            return 1
        except (remote.RemoteError, subprocess.SubprocessError, OSError) as e:
            print(f"  could not reach the robot, nothing was driven:\n  {e}\n"
                  "  check it is on and on the Wi-Fi, then press Enter to try again")
            continue

        rest, sag, on_s = summarise(log)
        rows.append({"run": number, "batch": a.batch, "file": out.name, "started": started,
                     "rest_v": f"{rest:.3f}", "sag_mv": f"{sag:.0f}",
                     "motors_on_s": f"{on_s:.3f}", "status": "ok"})
        write_index(index, rows)
        print(f"  done: battery {rest:.2f} V at rest, sagged {sag:.0f} mV, "
              f"motors on {on_s:.2f} s")
        if rest < STOP_V:
            print(f"  battery below {STOP_V} V: charge it before going on.")
            say("Battery low. Stopping.", a.quiet)
            return 0
        if rest < WARN_V:
            print(f"  battery below {WARN_V} V: worth charging soon.")
        print("  put it back on the start mark, then Enter for the next run\n")
        sys.stdout.write("\a")


if __name__ == "__main__":
    sys.exit(main())
