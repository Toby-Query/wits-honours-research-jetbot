"""The twin from the command line.

    python -m jetbot_twin info                 the robot as the twin knows it
    python -m jetbot_twin assumptions          what has not been measured yet
    python -m jetbot_twin check                the board against its ratings
    python -m jetbot_twin experiments          the lab sheet, in brief
    python -m jetbot_twin run NAME             one experiment on the twin
    python -m jetbot_twin predict              predictions with error bands
    python -m jetbot_twin rehearse             the validation, on a stand-in
    python -m jetbot_twin serve                the live 3D twin in a browser
    python -m jetbot_twin export               URDF and SDF for other simulators

  with the robot switched on:
    python -m jetbot_twin real probe  --host jetbot@IP
    python -m jetbot_twin real run NAME --host jetbot@IP --session 2026-10-05
    python -m jetbot_twin compare  --session 2026-10-05
    python -m jetbot_twin calibrate --session 2026-10-05
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from .hardware import DEFAULT

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
DATA = ROOT / "data" / "real"


def _hw(args):
    kw = {}
    if getattr(args, "board", None):
        kw["board"] = args.board
    cal = RESULTS / "calibrated.json"
    if getattr(args, "calibrated", False) and cal.exists():
        kw.update(json.loads(cal.read_text()))
    if getattr(args, "calibration", None):
        # a named calibration file, e.g. results/calibrated_robot2.json;
        # keys starting with "_" are notes
        kw.update({k: v for k, v in json.loads(Path(args.calibration).read_text()).items()
                   if not k.startswith("_")})
    return DEFAULT.with_(**kw) if kw else DEFAULT


def cmd_info(args):
    hw = _hw(args)
    print(hw.summary())
    print("\nmass budget")
    for i in hw.mass_items:
        print(f"  {i.mass * 1e3:6.0f} g  {i.name:<44}"
              + ("  (estimate)" if i.assumed else ""))
    print(f"  {hw.mass_sum * 1e3:6.0f} g  total")
    return 0


def cmd_assumptions(args):
    hw = _hw(args)
    rows = hw.assumptions()
    print(f"{len(rows)} numbers in hardware.py are estimates.\n")
    sens = RESULTS / f"sensitivity_{hw.board}.json"
    rank = {}
    if sens.exists():
        for metric, lst in json.loads(sens.read_text()).items():
            if metric.startswith("square"):
                continue          # a pose near zero has no meaningful elasticity
            for name, e in lst:
                rank[name] = max(rank.get(name, 0.0), abs(e))
    for name, v, why in sorted(rows, key=lambda r: -rank.get(r[0], 0.0)):
        tag = f"{rank[name]:5.2f}" if name in rank else "     "
        print(f"  {tag}  {name:<30} {v:<10.4g} {why}")
    if rank:
        print("\nThe first column is the largest elasticity of any prediction "
              "to that number:\n1.00 means a 10 % error there is a 10 % error "
              "in something you would measure.\n(The open-loop square is left "
              "out: it is sensitive to everything.)")
    else:
        print("\nRun `python -m jetbot_twin predict` to rank these by how much "
              "each one matters.")
    return 0


def cmd_check(args):
    from elecsim.design import evaluate

    from . import electrical
    hw = _hw(args)
    rep = evaluate(lambda **kw: electrical.build(hw, **kw), electrical.SCENARIOS)
    print(rep.text(verbose=args.verbose))
    return min(rep.failures, 100)


def cmd_experiments(args):
    from .experiments import EXPERIMENTS
    for e in EXPERIMENTS.values():
        where = "wheels held" if e.held else "on a stand" if e.lifted else "on the floor"
        print(f"{e.name}  ({e.duration:.0f} s, {where})\n  {e.doc}")
        if e.settles:
            print("  settles: " + "; ".join(e.settles))
        if e.measure:
            print("  write down: " + "; ".join(e.measure))
        print()
    return 0


def cmd_run(args):
    from . import compare
    from .experiments import EXPERIMENTS, predict_manual, run_on_twin
    if args.name not in EXPERIMENTS:
        print("no such experiment; there are: " + ", ".join(EXPERIMENTS))
        return 2
    exp = EXPERIMENTS[args.name]
    hw = _hw(args)
    log = run_on_twin(exp, hw)
    out = RESULTS / "twin"
    out.mkdir(parents=True, exist_ok=True)
    log.write_csv(out / f"{exp.name}_{hw.board}.csv")
    compare.plot(exp, None, log, out / f"{exp.name}_{hw.board}.png",
                 f"{exp.name} on the twin ({hw.board})")
    print(f"{exp.name} on the twin ({hw.board})")
    for k, v in predict_manual(exp, log).items():
        print(f"  {k:<20} " + (" ".join(f"{x:.2f}" for x in v)
                               if isinstance(v, list) else f"{v:.3f}"))
    print(f"  bus voltage          {log['bus_v'].min():.2f} .. {log['bus_v'].max():.2f} V")
    print(f"  pack current, peak   {log['i_pack'].max():.2f} A")
    print(f"wrote {out / (exp.name + '_' + hw.board)}.csv and .png")
    return 0


def cmd_predict(args):
    from . import predict
    boards = [args.board] if args.board else ["drv8870", "tb6612"]
    for board in boards:
        print(f"\n{board}: {args.n} robots drawn from the uncertainty in hardware.py")
        mc = predict.monte_carlo(board, n=args.n)
        predict.write(RESULTS / f"predictions_{board}.json", mc)
        w = max(len(k) for k in mc)
        print(f"  {'':<{w}}  {'nominal':>9}  {'5 %':>9}  {'median':>9}  {'95 %':>9}")
        for k, v in mc.items():
            print(f"  {k:<{w}}  {v['nominal']:9.3g}  {v['p05']:9.3g}  "
                  f"{v['p50']:9.3g}  {v['p95']:9.3g}")
        if args.sensitivity:
            s = predict.sensitivity(board)
            predict.write(RESULTS / f"sensitivity_{board}.json", s)
            print("\n  what each prediction depends on most")
            for k, lst in s.items():
                top = ", ".join(f"{n} ({e:+.2f})" for n, e in lst[:3] if abs(e) > 0.05)
                print(f"  {k:<{w}}  {top}")
    print(f"\nwrote {RESULTS}/predictions_*.json")
    return 0


def _load_session(session):
    import tomllib

    from .calibrate import Case
    from .experiments import EXPERIMENTS
    from .twin import Log
    d = DATA / session
    if not d.exists():
        raise SystemExit(f"no session at {d}")
    manual = {}
    m = d / "measurements.toml"
    if m.exists():
        manual = tomllib.loads(m.read_text())
    data = {}
    for name, exp in EXPERIMENTS.items():
        f = d / f"{name}.csv"
        if f.exists() or name in manual:
            data[name] = Case(exp, Log.read_csv(f) if f.exists() else None,
                              manual.get(name, {}))
    return d, data


def _compare_all(hw, data, out_dir, tag):
    from . import compare
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for name, case in data.items():
        if case.real is None:
            continue
        twin_log, hw_used = compare.replay(case.exp, case.real, hw)
        c = compare.compare(case.exp, case.real, twin_log, case.manual, hw_used.soc0)
        compare.plot(case.exp, case.real, twin_log, out_dir / f"{name}_{tag}.png",
                     f"{name}: robot against twin ({tag})")
        lines.append(c.text())
    return "\n\n".join(lines)


def cmd_compare(args):
    d, data = _load_session(args.session)
    hw = _hw(args)
    txt = _compare_all(hw, data, RESULTS / args.session,
                       "calibrated" if args.calibrated else "as-designed")
    print(txt)
    return 0


def cmd_calibrate(args):
    from . import calibrate
    d, data = _load_session(args.session)
    hw, history = calibrate.staged(_hw(args), data, board=args.board)
    changed = {}
    for h in history:
        if "names" in h:
            changed.update({n: float(v) for n, v in zip(h["names"], h["after"])})
        elif "picked" in h:
            changed["board"] = h["picked"]
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "calibrated.json").write_text(json.dumps(changed, indent=1))
    print(f"\nwrote {RESULTS / 'calibrated.json'}; use --calibrated to apply it."
          "\nWhen you are satisfied, move the numbers into hardware.py as "
          "measured(...).")
    return 0


def cmd_rehearse(args):
    """The whole validation procedure, run against a stand-in robot."""
    from . import calibrate, compare, synthetic
    from .experiments import EXPERIMENTS, predict_manual, run_on_twin
    out = RESULTS / "rehearsal"
    out.mkdir(parents=True, exist_ok=True)
    print("REHEARSAL. The 'robot' below is a second twin with altered numbers,\n"
          "noisy sensors and a rounding tape measure. None of this is data from\n"
          "the real JetBot; it shows that the procedure recovers known answers.\n")
    names = ["jetson_load", "wheels_up_sweep", "stall", "straight", "spin", "square"]
    data = {}
    for n in names:
        real, manual = synthetic.observe(EXPERIMENTS[n])
        data[n] = calibrate.Case(EXPERIMENTS[n], real, manual)
    held_out = data.pop("square")
    # what a ruler and a kitchen scale give you directly
    start = DEFAULT.with_(wheel_radius=synthetic.TRUTH["wheel_radius"],
                          mass_override=synthetic.TRUTH["mass_override"])

    def square_error(hw):
        tw, _ = compare.replay(held_out.exp, held_out.real, hw)
        p = predict_manual(held_out.exp, tw)
        m = held_out.manual
        dh = (p["final_heading_deg"] - m["final_heading_deg"] + 180) % 360 - 180
        return (math.hypot(p["final_x_m"] - m["final_x_m"],
                           p["final_y_m"] - m["final_y_m"]), dh)

    e0 = square_error(start)
    print(f"before: the twin misses the end of the square by "
          f"{e0[0] * 100:.0f} cm and {e0[1]:+.0f} degrees\n")
    hw, history = calibrate.staged(start, data, board=args.board)
    e1 = square_error(hw)
    print(f"\nafter:  it misses by {e1[0] * 100:.0f} cm and {e1[1]:+.0f} degrees "
          "(the square was not used in the fit)\n")
    print("recovered against the stand-in's true values")
    for h in history:
        for n, b in zip(h.get("names", []), h.get("after", [])):
            t = synthetic.TRUTH.get(n)
            if t is not None:
                print(f"  {n:<20} fitted {b:.4g}   true {t:.4g}   "
                      f"({(b / t - 1) * 100:+.0f} %)")
    (out / "history.json").write_text(json.dumps(history, indent=1, default=float))
    return 0


def cmd_real(args):
    from . import remote
    from .experiments import EXPERIMENTS
    if args.action == "probe":
        info = remote.probe(args.host)
        DATA.mkdir(parents=True, exist_ok=True)
        (DATA / "probe.json").write_text(json.dumps(info, indent=1))
        src = info.pop("jetbot_motor_py", "")
        print(json.dumps(info, indent=1))
        chip = ("INA219 present: the newer board revision"
                if info.get("has_ina219") else
                "ADS1115 present: the older board revision"
                if info.get("has_ads1115") else "no battery monitor answered")
        print(f"\n{chip}\nsaved to {DATA / 'probe.json'} "
              f"(with the robot's motor.py, {len(src.splitlines())} lines)")
        return 0
    if args.action == "stop":
        print(remote.stop(args.host))
        return 0
    if args.name not in EXPERIMENTS:
        print("no such experiment; there are: " + ", ".join(EXPERIMENTS))
        return 2
    exp = EXPERIMENTS[args.name]
    where = ("HOLD BOTH WHEELS" if exp.held else "wheels OFF the ground"
             if exp.lifted else "on the floor with clear space")
    print(f"{exp.name}: {exp.doc}\n  {where}; {exp.duration:.0f} s.")
    if not args.yes and input("  ready? [y/N] ").strip().lower() != "y":
        return 1
    out = DATA / args.session / f"{exp.name}.csv"
    log = remote.run(args.host, exp, out)
    print(f"saved {out} ({len(log)} samples)")
    if exp.measure:
        print(f"now write down, in {out.parent / 'measurements.toml'}:")
        for m in exp.measure:
            print(f"  - {m}")
    return 0


def cmd_serve(args):
    from . import server
    return server.main(port=args.port, board=args.board, open_browser=not args.no_open)


def cmd_export(args):
    from . import export
    out = Path(args.out)
    for p in export.write_all(_hw(args), out):
        print(f"wrote {p}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="jetbot-twin", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", choices=["drv8870", "tb6612"])
    ap.add_argument("--calibrated", action="store_true",
                    help="apply results/calibrated.json")
    ap.add_argument("--calibration", metavar="FILE",
                    help="apply this calibration file instead, e.g. "
                         "results/calibrated_robot2.json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info").set_defaults(fn=cmd_info)
    sub.add_parser("assumptions").set_defaults(fn=cmd_assumptions)
    p = sub.add_parser("check")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(fn=cmd_check)
    sub.add_parser("experiments").set_defaults(fn=cmd_experiments)
    p = sub.add_parser("run")
    p.add_argument("name")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("predict")
    p.add_argument("-n", type=int, default=48)
    p.add_argument("--sensitivity", action="store_true")
    p.set_defaults(fn=cmd_predict)
    sub.add_parser("rehearse").set_defaults(fn=cmd_rehearse)
    p = sub.add_parser("real")
    p.add_argument("action", choices=["probe", "run", "stop"])
    p.add_argument("name", nargs="?")
    p.add_argument("--host", required=True, help="e.g. jetbot@192.168.1.42")
    p.add_argument("--session", default="session1")
    p.add_argument("-y", "--yes", action="store_true")
    p.set_defaults(fn=cmd_real)
    for name, fn in (("compare", cmd_compare), ("calibrate", cmd_calibrate)):
        p = sub.add_parser(name)
        p.add_argument("--session", required=True)
        p.set_defaults(fn=fn)
    p = sub.add_parser("serve")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-open", action="store_true")
    p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("export")
    p.add_argument("--out", default=str(ROOT / "export"))
    p.set_defaults(fn=cmd_export)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
