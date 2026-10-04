"""The live twin: the simulation running in real time, watched from a browser.

    python -m jetbot_twin serve

Standard library only. One thread steps the twin against the wall clock and
publishes a snapshot thirty times a second; the page listens on a
server-sent-event stream and sends commands back as small POSTs. There is no
second copy of the model in the browser: every number on the page was
computed here, by the same code the tests and the experiments run.

    GET  /            the page
    GET  /stream      snapshots, as server-sent events
    GET  /design      the circuit: components, nets, parameters, provenance
    POST /cmd         {"drive": [l, r]} | {"action": ..., ...}
"""
from __future__ import annotations

import json
import math
import queue
import threading
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .experiments import EXPERIMENTS
from .hardware import DEFAULT
from .twin import JetBotTwin

WEB = Path(__file__).resolve().parents[1] / "web"
DT = 2e-3
FRAME = 1 / 30.0

# the unverified numbers worth putting a slider on
TUNABLE = ["motor_r", "motor_rpm_noload", "motor_i_noload", "motor_l", "gear_eff",
           "cell_r0", "holder_r", "mu_tyre", "mu_caster", "c_rr", "wheel_radius",
           "rotor_inertia", "mass_override"]


class Live:
    """Owns the twin. Everything that touches it happens on its thread."""

    def __init__(self, board=None):
        self.overrides = {"board": board} if board else {}
        self.commands = queue.Queue()
        self.listeners = []
        self.lock = threading.Lock()
        self.cmd = [0.0, 0.0]
        self.last_drive = 0.0
        self.script = None                  # (experiment, started_at_twin_t)
        self.speed = 1.0
        self.flags = {"lifted": False, "held": False, "charger": False,
                      "jetson": "idle"}
        self.fault = None
        self.build()

    # -- building ------------------------------------------------------------
    def build(self, keep_pose=False):
        old = getattr(self, "twin", None)
        self.hw = DEFAULT.with_(**self.overrides) if self.overrides else DEFAULT
        self.twin = JetBotTwin(self.hw, dt=DT)
        if keep_pose and old is not None:
            s, o = self.twin.dyn.s, old.dyn.s
            s.x, s.y, s.th = o.x, o.y, o.th
        self.apply_flags()
        self.trail = []

    def apply_flags(self):
        t, f = self.twin, self.flags
        t.lift(f["lifted"])
        t.dyn.held = f["held"]
        t.plug_charger(f["charger"])
        t.set_jetson_power(getattr(self.hw, "jetson_p_" + {
            "idle": "idle", "teleop": "teleop", "inference": "inference",
            "max": "max"}[f["jetson"]]))
        for name in ("drive_left", "drive_right", "S1", "holder"):
            self.twin.circuit[name].fault = None
        if self.fault:
            comp, mode = self.fault
            self.twin.circuit[comp].fault = mode

    # -- commands from the page -------------------------------------------------
    def handle(self, msg):
        if "drive" in msg:
            l, r = msg["drive"]
            self.cmd = [max(-1.0, min(1.0, float(l))), max(-1.0, min(1.0, float(r)))]
            self.last_drive = time.time()
            self.script = None
            return
        a = msg.get("action")
        if a == "reset":
            self.cmd = [0.0, 0.0]
            self.script = None
            self.build()
        elif a == "flag":
            self.flags[msg["name"]] = msg["value"]
            self.apply_flags()
        elif a == "set":
            name = msg["name"]
            if name == "board" or name in TUNABLE or name == "soc0":
                self.overrides[name] = msg["value"]
                self.build(keep_pose=True)
        elif a == "defaults":
            board = self.overrides.get("board")
            self.overrides = {"board": board} if board else {}
            self.build(keep_pose=True)
        elif a == "fault":
            self.fault = tuple(msg["value"]) if msg.get("value") else None
            self.apply_flags()
        elif a == "experiment":
            exp = EXPERIMENTS.get(msg["name"])
            if exp:
                self.flags["lifted"], self.flags["held"] = exp.lifted, exp.held
                self.build()
                self.script = (exp, self.twin.t)
        elif a == "speed":
            self.speed = float(msg["value"])

    def scripted(self):
        exp, t0 = self.script
        t = self.twin.t - t0
        for seconds, left, right in exp.profile:
            if t < seconds:
                return [left, right]
            t -= seconds
        self.script = None
        return [0.0, 0.0]

    # -- the loop --------------------------------------------------------------------
    def run(self):
        next_frame = time.time()
        while True:
            while True:
                try:
                    self.handle(self.commands.get_nowait())
                except queue.Empty:
                    break
                except Exception as e:                  # a bad message is not fatal
                    print("bad command:", e)
            if self.script:
                self.cmd = self.scripted()
            elif time.time() - self.last_drive > 0.6:   # dead-man: keys released
                self.cmd = [0.0, 0.0]
            try:
                self.twin.robot.set_motors(*self.cmd)
                for _ in range(max(1, int(round(FRAME * self.speed / DT)))):
                    self.twin.step()
                snap = self.snapshot()
            except Exception as e:
                snap = {"error": f"{type(e).__name__}: {e}"}
                self.build()
            data = json.dumps(snap, default=float)
            with self.lock:
                for q in self.listeners:
                    if q.full():
                        try:
                            q.get_nowait()
                        except queue.Empty:
                            pass
                    q.put(data)
            next_frame += FRAME
            pause = next_frame - time.time()
            if pause > 0:
                time.sleep(pause)
            else:
                next_frame = time.time()

    def snapshot(self):
        t = self.twin
        tel = t.telemetry()
        s = t.dyn.s
        if not self.trail or math.hypot(s.x - self.trail[-1][0],
                                        s.y - self.trail[-1][1]) > 0.01:
            self.trail.append((round(s.x, 4), round(s.y, 4)))
            self.trail = self.trail[-1500:]
        sig = {k: v for k, v in t.circuit.signals().items()
               if isinstance(v, (int, float)) and math.isfinite(v)}
        limits = [{"label": l.label, "worst": l.worst, "bound": l.bound,
                   "margin": l.margin, "violated": l.violated,
                   "severity": l.severity, "why": l.why}
                  for l in t.sim.monitor.results()]
        return {"t": t.t, "tel": tel, "sig": sig, "limits": limits,
                "flags": self.flags, "board": self.hw.board,
                "script": self.script[0].name if self.script else None,
                "fault": list(self.fault) if self.fault else None,
                "trail": self.trail[-400:],
                "protector": t.circuit["protector"].state,
                "buck": t.circuit["U3"].mode, "pwm_hz": self.hw.pwm_hz,
                "forces": t.dyn.forces}

    def design(self):
        c = self.twin.circuit
        comps = []
        for comp in c:
            params = {}
            for k, v in comp._values.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    params[k] = {"value": float(v),
                                 "unit": comp._params[k].unit,
                                 "assumed": bool(getattr(v, "assumed", False)),
                                 "src": getattr(v, "src", "")}
            comps.append({"name": comp.name, "type": type(comp).__name__,
                          "note": comp.note,
                          "pins": {p.name: (p.net.name if p.net else None)
                                   for p in comp.pins},
                          "params": params})
        hw = self.hw
        tun = []
        for n in TUNABLE:
            v = getattr(hw, n)
            base = getattr(DEFAULT, n)
            if n == "mass_override":
                v, base = hw.mass, DEFAULT.mass
            tun.append({"name": n, "value": float(v), "default": float(base),
                        "src": getattr(getattr(DEFAULT, n), "src", "") or
                        ("total mass, kg" if n == "mass_override" else "")})
        return {"name": c.name, "components": comps,
                "nets": {n.name: [repr(p) for p in n.pins] for n in c.nets.values()},
                "tunable": tun, "summary": hw.summary(),
                "experiments": [{"name": e.name, "doc": e.doc,
                                 "duration": e.duration}
                                for e in EXPERIMENTS.values()],
                "assumptions": [{"name": a, "value": b, "why": w}
                                for a, b, w in hw.assumptions()]}


def make_handler(live):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(WEB), **kw)

        def log_message(self, *a):
            pass

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def do_GET(self):
            if self.path == "/stream":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                q = queue.Queue(maxsize=2)
                with live.lock:
                    live.listeners.append(q)
                try:
                    while True:
                        self.wfile.write(b"data: " + q.get().encode() + b"\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    with live.lock:
                        live.listeners.remove(q)
                return
            if self.path.startswith("/delay"):
                # Holds the page's load event open, so a headless screenshot
                # (tools/screenshot.sh) is taken after the scene has drawn.
                ms = int(self.path.split("ms=")[-1]) if "ms=" in self.path else 0
                time.sleep(min(ms, 20000) / 1000.0)
                self.send_response(204)
                self.end_headers()
                return
            if self.path == "/design":
                body = json.dumps(live.design(), default=float).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            try:
                live.commands.put(json.loads(self.rfile.read(n) or b"{}"))
                self.send_response(204)
            except ValueError:
                self.send_response(400)
            self.end_headers()
    return Handler


def main(port=8765, board=None, open_browser=True):
    if not (WEB / "jetbot.glb").exists():
        print("web/jetbot.glb is missing: run  python tools/build_model.py  "
              "(the page still works, with a block in place of the robot)")
    live = Live(board)
    threading.Thread(target=live.run, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(live))
    url = f"http://127.0.0.1:{port}/"
    print(f"JetBot twin at {url}   (Ctrl-C to stop)")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print()
    return 0
