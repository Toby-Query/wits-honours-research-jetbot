"""Reach the real robot over SSH: copy the agent across, run it, bring the
log back.

Uses the system `ssh` and `scp`, so it uses your keys and your config. Set
up key login once (`ssh-copy-id jetbot@<ip>`; the image's default password
is `jetbot`) and nothing here will ask for a password again.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / "robot" / "agent.py"
REMOTE_DIR = "~/jetbot_twin_agent"
SSH = ["ssh", "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=5"]


class RemoteError(RuntimeError):
    pass


def _ssh(host, command, timeout=240, check=True):
    p = subprocess.run(SSH + [host, command], capture_output=True, text=True,
                       timeout=timeout)
    if check and p.returncode != 0:
        raise RemoteError(f"`{command}` on {host} failed "
                          f"({p.returncode}):\n{p.stderr.strip() or p.stdout.strip()}")
    return p.stdout


def deploy(host):
    _ssh(host, f"mkdir -p {REMOTE_DIR}")
    p = subprocess.run(["scp", "-q", "-o", "ConnectTimeout=8", str(AGENT),
                        f"{host}:{REMOTE_DIR}/agent.py"],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise RemoteError(p.stderr.strip())


def probe(host):
    deploy(host)
    out = _ssh(host, f"cd {REMOTE_DIR} && python3 agent.py probe")
    return json.loads(out[out.index("{"):])


def stop(host):
    return _ssh(host, f"cd {REMOTE_DIR} && python3 agent.py stop", check=False)


def run(host, exp, out_path, countdown=3):
    """Run an Experiment on the robot; returns the Log it produced."""
    from .twin import Log
    deploy(host)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        f.write(exp.to_json())
        local_json = f.name
    remote_json = f"{REMOTE_DIR}/{exp.name}.json"
    remote_csv = f"{REMOTE_DIR}/{exp.name}.csv"
    subprocess.run(["scp", "-q", local_json, f"{host}:{remote_json}"], check=True)
    try:
        print(_ssh(host, f"cd {REMOTE_DIR} && python3 agent.py run {exp.name}.json "
                         f"-o {exp.name}.csv --countdown {countdown}",
                   timeout=exp.duration + countdown + 60).strip())
    except BaseException:
        stop(host)                       # whatever went wrong, motors off
        raise
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["scp", "-q", f"{host}:{remote_csv}", str(out_path)], check=True)
    return Log.read_csv(out_path)
