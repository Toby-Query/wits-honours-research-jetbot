"""elecsim against ngspice, circuit by circuit.

    .venv/bin/python tools/spice_check.py          # needs ngspice on the PATH

Five circuits, each built twice (once with elecsim's parts, once as a SPICE
netlist) and solved by both:

    bridge     DC: an unbalanced Wheatstone bridge
    rc         transient: an RC charge from a step
    rlc        transient: an underdamped series RLC from a step
    diode      DC sweep: a diode and resistor, 0 to 5 V
    powerpath  transient: the JetBot's power path (pack resistance, holder,
               shunt, switch, bulk capacitance) as the two stalled motors
               (winding R and L) are switched on, with robot 2's numbers

The transient ones are run at several elecsim step sizes against a SPICE run
with a very small step, which shows the error falling in proportion to the
step, as backward Euler should. Writes results/spice_check.json.
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from elecsim import Circuit, Simulation  # noqa: E402
from elecsim.lib import Capacitor, Diode, Inductor, Resistor, Switch, VoltageSource  # noqa: E402
from elecsim.lib.semis import VT, diode_fit  # noqa: E402

OUT = ROOT / "results" / "spice_check.json"


def spice(netlist, analysis, probes):
    """Run ngspice in batch mode; return (columns, seconds)."""
    with tempfile.TemporaryDirectory() as d:
        data = Path(d) / "out.txt"
        cir = Path(d) / "c.cir"
        cir.write_text(
            "* elecsim check\n.options TEMP=26.85 TNOM=26.85 RELTOL=1e-6 ABSTOL=1e-12 VNTOL=1e-9\n"
            + netlist + "\n.control\nset wr_singlescale\nset wr_vecnames\n"
            + f"{analysis}\nwrdata {data} {' '.join(probes)}\nquit\n.endc\n.end\n")
        t = time.perf_counter()
        subprocess.run(["ngspice", "-b", str(cir)], check=True, capture_output=True, text=True)
        dt = time.perf_counter() - t
        rows = [ln.split() for ln in data.read_text().splitlines()]
        arr = np.array([[float(x) for x in r] for r in rows[1:]])
    return arr, dt


def timed(fn):
    t = time.perf_counter()
    out = fn()
    return out, time.perf_counter() - t


# --------------------------------------------------------------------- bridge
def bridge():
    V, R = 12.0, dict(R1=1e3, R2=2.2e3, R3=3.3e3, R4=4.7e3, R5=1.5e3)

    def build():
        c = Circuit("bridge")
        src = c.add(VoltageSource("V1", v=V))
        r = {k: c.add(Resistor(k, r=v)) for k, v in R.items()}
        c.connect("top", src.p, r["R1"].a, r["R2"].a)
        c.connect("a", r["R1"].b, r["R3"].a, r["R5"].a)
        c.connect("b", r["R2"].b, r["R4"].a, r["R5"].b)
        c.ground(src.n, r["R3"].b, r["R4"].b)
        sim = Simulation(c)
        return [c["R3"].sig["v"], c["R4"].sig["v"], c["R5"].sig["i"]]
    es, t_es = timed(build)
    net = (f"V1 top 0 {V}\nR1 top a {R['R1']}\nR2 top b {R['R2']}\nR3 a 0 {R['R3']}\n"
           f"R4 b 0 {R['R4']}\nR5 a b {R['R5']}")
    sp, t_sp = spice(net, "op", ["v(a)", "v(b)", "i(V1)"])
    vals = sp[0][1:]                      # one row: scale, then each probe
    va, vb = float(vals[0]), float(vals[1])
    sp_vals = [va, vb, (va - vb) / R["R5"]]
    err = [abs(a - b) for a, b in zip(es, sp_vals)]
    return dict(circuit="bridge", elecsim=es, spice=sp_vals, max_abs_err=max(err),
                max_rel_err=max(e / abs(b) for e, b in zip(err, sp_vals)),
                elecsim_s=t_es, spice_s=t_sp)


# -------------------------------------------------------------- transients
def step_source(c, name, V):
    return c.add(VoltageSource(name, v=lambda t, V=V: V if t > 0 else 0.0))


def rc_build(dt, T):
    R, C, V = 1e3, 1e-6, 5.0
    c = Circuit("rc")
    src = step_source(c, "V1", V)
    r = c.add(Resistor("R1", r=R))
    cap = c.add(Capacitor("C1", c=C, ic=0.0))
    c.connect("in", src.p, r.a)
    c.connect("out", r.b, cap.a)
    c.ground(src.n, cap.b)
    res = Simulation(c, dt=dt).run(T, record=["C1.v"])
    return res.t, res["C1.v"]


RC_NET = "V1 in 0 PULSE(0 5 0 1n 1n 1 2)\nR1 in out 1k\nC1 out 0 1u IC=0"


def rlc_build(dt, T):
    R, L, C, V = 20.0, 10e-3, 10e-6, 5.0
    c = Circuit("rlc")
    src = step_source(c, "V1", V)
    r = c.add(Resistor("R1", r=R))
    ind = c.add(Inductor("L1", l=L))
    cap = c.add(Capacitor("C1", c=C, ic=0.0))
    c.connect("in", src.p, r.a)
    c.connect("m", r.b, ind.a)
    c.connect("out", ind.b, cap.a)
    c.ground(src.n, cap.b)
    res = Simulation(c, dt=dt).run(T, record=["C1.v"])
    return res.t, res["C1.v"]


RLC_NET = ("V1 in 0 PULSE(0 5 0 1n 1n 1 2)\nR1 in m 20\nL1 m out 10m IC=0\n"
           "C1 out 0 10u IC=0")

# robot 2: three cells 0.0742 ohm each, polarisation 0.03 ohm || 1500 F per
# cell, holder 0.12, protector and shunt and switch, 330 uF bulk, two stalled
# motors of 3.15 ohm and 2 mH each, closed at t = 0
PP = dict(v=12.3, r0=3 * 0.0742, r1=3 * 0.030, c1=1500.0 / 3, holder=0.12, prot=0.02,
          shunt=0.010, sw=0.03, cbulk=330e-6, rm=3.153, lm=2e-3)


def pp_build(dt, T):
    p = PP
    c = Circuit("powerpath")
    src = c.add(VoltageSource("OCV", v=p["v"]))
    parts = [c.add(Resistor("R0", r=p["r0"])), c.add(Resistor("R1", r=p["r1"])),
             c.add(Capacitor("C1", c=p["c1"])), c.add(Resistor("HOLD", r=p["holder"])),
             c.add(Resistor("PROT", r=p["prot"])), c.add(Resistor("SHUNT", r=p["shunt"])),
             c.add(Resistor("SW", r=p["sw"]))]
    r0, r1, c1, hold, prot, shunt, sw = parts
    cb = c.add(Capacitor("CB", c=p["cbulk"]))
    s = c.add(Switch("S", r_on=1e-3, r_off=1e9, closed=False))
    ml = [c.add(Resistor(f"RM{k}", r=p["rm"])) for k in (1, 2)]
    ll = [c.add(Inductor(f"LM{k}", l=p["lm"])) for k in (1, 2)]
    c.connect("ocv", src.p, r0.a)
    c.connect("x1", r0.b, r1.a, c1.a)
    c.connect("x2", r1.b, c1.b, hold.a)
    c.connect("x3", hold.b, prot.a)
    c.connect("x4", prot.b, shunt.a)
    c.connect("x5", shunt.b, sw.a)
    c.connect("bus", sw.b, cb.a, s.a)
    c.connect("mot", s.b, ml[0].a, ml[1].a)
    c.connect("m1", ml[0].b, ll[0].a)
    c.connect("m2", ml[1].b, ll[1].a)
    c.ground(src.n, cb.b, ll[0].b, ll[1].b)

    def close(t, circ):
        circ["S"].inputs["closed"] = t >= 0.0
    res = Simulation(c, dt=dt).run(T, stimulus=close, record=["CB.v", "LM1.i"])
    return res.t, res["CB.v"]


PP_NET = (f"VOCV ocv 0 {PP['v']}\nR0 ocv x1 {PP['r0']}\nR1 x1 x2 {PP['r1']}\nC1 x1 x2 {PP['c1']}\n"
          f"RH x2 x3 {PP['holder']}\nRP x3 x4 {PP['prot']}\nRS x4 x5 {PP['shunt']}\n"
          f"RSW x5 bus {PP['sw']}\nCB bus 0 {PP['cbulk']}\n"
          "VCTL ctl 0 PULSE(0 1 0 1n 1n 10 20)\nS1 bus mot ctl 0 SMOD\n"
          ".model SMOD SW(VT=0.5 VH=0 RON=1e-3 ROFF=1e9)\n"
          f"RM1 mot m1 {PP['rm']}\nLM1 m1 0 {PP['lm']}\nRM2 mot m2 {PP['rm']}\nLM2 m2 0 {PP['lm']}")


def transient(name, build, net, T, probe, dts, uic=True):
    sp, t_sp = spice(net, f"tran {T / 20000:.3g} {T} {'uic' if uic else ''}", [probe])
    ts, ys = sp[:, 0], sp[:, 1]
    rows = []
    for dt in dts:
        (t, y), t_es = timed(lambda: build(dt, T))
        ref = np.interp(t, ts, ys)
        err = np.abs(y - ref)
        rows.append(dict(dt=dt, max_abs_err=float(err.max()),
                         rms_err=float(np.sqrt(np.mean(err ** 2))),
                         rel_to_swing=float(err.max() / (ys.max() - ys.min())),
                         max_abs_err_after_quarter=float(err[t > T / 4].max()),
                         final_err=float(err[-1]), elecsim_s=t_es))
    order = float(np.polyfit(np.log([r["dt"] for r in rows]),
                             np.log([r["max_abs_err"] for r in rows]), 1)[0])
    return dict(circuit=name, spice_s=t_sp, steps=rows, convergence_order=order)


# ----------------------------------------------------------------------- diode
def diode():
    vf, i_f, n, R = 0.7, 1.0, 1.8, 100.0
    i_s = diode_fit(vf, i_f, n)
    vs = np.linspace(0.0, 5.0, 51)

    def run():
        out = []
        for v in vs:
            c = Circuit("d")
            src = c.add(VoltageSource("V1", v=float(v)))
            r = c.add(Resistor("R1", r=R))
            d = c.add(Diode("D1", vf=vf, i_f=i_f, n=n))
            c.connect("in", src.p, r.a)
            c.connect("a", r.b, d.a)
            c.ground(src.n, d.k)
            Simulation(c)
            out.append(c["D1"].sig["i"])
        return np.array(out)
    es, t_es = timed(run)
    net = f"V1 in 0 0\nR1 in a {R}\nD1 a 0 DMOD\n.model DMOD D(IS={i_s:.6e} N={n})"
    sp, t_sp = spice(net, "dc V1 0 5 0.1", ["i(V1)"])
    ref = -sp[:, 1]
    err = np.abs(es - ref)
    on = ref > 1e-4
    return dict(circuit="diode", vt_elecsim=VT, max_abs_err=float(err.max()),
                max_rel_err_conducting=float((err[on] / ref[on]).max()),
                elecsim_s=t_es, spice_s=t_sp)


def main():
    out = [bridge(), diode(),
           transient("rc", rc_build, RC_NET, 5e-3, "v(out)", [1e-4, 5e-5, 2.5e-5, 1e-5]),
           transient("rlc", rlc_build, RLC_NET, 20e-3, "v(out)", [2e-4, 1e-4, 5e-5, 2.5e-5]),
           transient("powerpath", pp_build, PP_NET, 20e-3, "v(bus)", [1e-3, 5e-4, 2.5e-4, 1e-4],
                     uic=False)]
    for r in out:
        print(json.dumps(r, default=float))
    OUT.write_text(json.dumps(out, indent=1, default=float))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
