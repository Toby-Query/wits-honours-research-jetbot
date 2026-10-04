#!/usr/bin/env python3
"""Runs ON the JetBot. Executes an experiment and logs what the robot can
measure about itself.

    python3 agent.py probe                      what hardware is this?
    python3 agent.py run exp.json -o log.csv    run a command profile
    python3 agent.py stop                       make sure the motors are off

One file, standard library plus `smbus` (already on the JetBot image). It
talks to the PCA9685 directly, writing exactly the registers that
`jetbot.Motor._write_value` writes, so what the twin simulates and what the
robot executes are the same sequence of register writes -- and it does not
matter which version of the jetbot package is installed.

Written for Python 3.6, which is what JetPack 4 ships.

Log columns
    t          seconds since the profile started
    cmd_l/r    the commanded values
    bus_v      INA219 bus voltage: the EB+ rail, after the protection FETs
    charge_i   INA219 current through R16: CHARGER current, not load current
    v_5v, i_5v, p_5v   the Jetson's own monitor on its 5 V input (INA3221)
    p_cpu, p_gpu       the other two INA3221 rails
    temp_*     thermal zones, C
    cpu        CPU utilisation, 0..1
"""
from __future__ import print_function

import argparse
import glob
import json
import multiprocessing
import os
import signal
import subprocess
import sys
import time

BUS = 1
PCA_ADDR = 0x60
INA_ADDR = 0x41
ADS_ADDR = 0x48

# PCA9685 channel map, from the Waveshare schematic and Adafruit_MotorHAT
HAT = {"left": dict(pwm=8, in2=9, in1=10), "right": dict(pwm=13, in2=12, in1=11)}
DRV = {"left": dict(in1=1, in2=0), "right": dict(in1=2, in2=3)}


def open_bus():
    try:
        import smbus
    except ImportError:
        import smbus2 as smbus
    return smbus.SMBus(BUS)


# --------------------------------------------------------------------- PCA9685
class Pca9685(object):
    MODE1, MODE2, PRESCALE, LED0 = 0x00, 0x01, 0xFE, 0x06

    def __init__(self, bus, addr=PCA_ADDR, freq=1600):
        self.bus, self.addr = bus, addr
        self.set_all(0, 0)
        bus.write_byte_data(addr, self.MODE2, 0x04)          # totem pole
        bus.write_byte_data(addr, self.MODE1, 0x01)          # ALLCALL
        time.sleep(0.005)
        mode1 = bus.read_byte_data(addr, self.MODE1) & ~0x10  # wake
        bus.write_byte_data(addr, self.MODE1, mode1)
        time.sleep(0.005)
        self.set_freq(freq)

    def set_freq(self, hz):
        # identical arithmetic to Adafruit_PCA9685, 0.9 fudge factor included
        prescale = int((25000000.0 / 4096.0 / (hz * 0.9) - 1.0) + 0.5)
        old = self.bus.read_byte_data(self.addr, self.MODE1)
        self.bus.write_byte_data(self.addr, self.MODE1, (old & 0x7F) | 0x10)
        self.bus.write_byte_data(self.addr, self.PRESCALE, prescale)
        self.bus.write_byte_data(self.addr, self.MODE1, old)
        time.sleep(0.005)
        self.bus.write_byte_data(self.addr, self.MODE1, old | 0x80)
        self.prescale = prescale

    def set_pwm(self, ch, on, off):
        base = self.LED0 + 4 * ch
        self.bus.write_i2c_block_data(
            self.addr, base, [on & 0xFF, on >> 8, off & 0xFF, off >> 8])

    def set_all(self, on, off):
        self.bus.write_i2c_block_data(
            self.addr, 0xFA, [on & 0xFF, on >> 8, off & 0xFF, off >> 8])


def write_motor(pca, side, value, alpha=1.0, beta=0.0):
    """jetbot.Motor._write_value, register for register."""
    mapped = int(255.0 * (alpha * value + beta))
    speed = min(max(abs(mapped), 0), 255)
    hat, drv = HAT[side], DRV[side]
    pca.set_pwm(hat["pwm"], 0, speed * 16)
    if mapped < 0:
        pca.set_pwm(hat["in2"], 0, 4096)
        pca.set_pwm(hat["in1"], 4096, 0)
        pca.set_pwm(drv["in1"], 0, 0)
        pca.set_pwm(drv["in2"], 0, speed * 16)
    else:
        pca.set_pwm(hat["in1"], 0, 4096)
        pca.set_pwm(hat["in2"], 4096, 0)
        pca.set_pwm(drv["in1"], 0, speed * 16)
        pca.set_pwm(drv["in2"], 0, 0)


def release(pca):
    for side in ("left", "right"):
        hat, drv = HAT[side], DRV[side]
        pca.set_pwm(hat["pwm"], 0, 0)
        pca.set_pwm(hat["in1"], 0, 4096)
        pca.set_pwm(hat["in2"], 0, 4096)
        pca.set_pwm(drv["in1"], 0, 0)
        pca.set_pwm(drv["in2"], 0, 0)


# --------------------------------------------------------------------- sensors
def _swap(w):
    return ((w & 0xFF) << 8) | (w >> 8)


class Ina219(object):
    """Raw registers: no calibration needed for bus and shunt voltage."""

    def __init__(self, bus, addr=INA_ADDR, r_shunt=0.1):
        self.bus, self.addr, self.r = bus, addr, r_shunt
        # 32 V range, PGA /8 (+/-320 mV), 12-bit, continuous
        bus.write_word_data(addr, 0x00, _swap(0x399F))

    def read(self):
        raw = _swap(self.bus.read_word_data(self.addr, 0x02))
        bus_v = (raw >> 3) * 0.004
        sh = _swap(self.bus.read_word_data(self.addr, 0x01))
        if sh > 32767:
            sh -= 65536
        return bus_v, sh * 10e-6 / self.r


class Ads1115(object):
    """Older boards. Returns the volts AT THE PIN on AIN0..3; the divider
    ratio between the pack and the pin has to be found separately."""

    def __init__(self, bus, addr=ADS_ADDR):
        self.bus, self.addr = bus, addr

    def read(self, ch):
        cfg = 0x8000 | ((0x04 + ch) << 12) | (0x01 << 9) | 0x0100 | 0x00E3
        self.bus.write_word_data(self.addr, 0x01, _swap(cfg))
        time.sleep(0.0015)
        raw = _swap(self.bus.read_word_data(self.addr, 0x00))
        if raw > 32767:
            raw -= 65536
        return raw * 4.096 / 32768.0


class Rails(object):
    """The Jetson's INA3221, through sysfs."""

    def __init__(self):
        self.files = {}
        for name_file in glob.glob(
                "/sys/bus/i2c/drivers/ina3221x/*/iio:device*/rail_name_*"):
            try:
                name = open(name_file).read().strip()
            except IOError:
                continue
            d, idx = os.path.dirname(name_file), name_file.rsplit("_", 1)[1]
            self.files[name] = dict(
                v=os.path.join(d, "in_voltage%s_input" % idx),
                i=os.path.join(d, "in_current%s_input" % idx),
                p=os.path.join(d, "in_power%s_input" % idx))

    @staticmethod
    def _read(path):
        try:
            with open(path) as f:
                return float(f.read()) / 1000.0
        except (IOError, ValueError):
            return float("nan")

    def read(self):
        out = {}
        for name, f in self.files.items():
            out[name] = (self._read(f["v"]), self._read(f["i"]), self._read(f["p"]))
        return out


def thermal():
    out = {}
    for z in glob.glob("/sys/devices/virtual/thermal/thermal_zone*"):
        try:
            name = open(os.path.join(z, "type")).read().strip()
            out[name] = float(open(os.path.join(z, "temp")).read()) / 1000.0
        except (IOError, ValueError):
            pass
    return out


class Cpu(object):
    def __init__(self):
        self.last = self._snap()

    @staticmethod
    def _snap():
        try:
            with open("/proc/stat") as f:
                v = [float(x) for x in f.readline().split()[1:8]]
        except (IOError, ValueError):
            return 0.0, 0.0
        return sum(v), v[3] + v[4]

    def read(self):
        total, idle = self._snap()
        dt, di = total - self.last[0], idle - self.last[1]
        self.last = (total, idle)
        return 1.0 - di / dt if dt > 0 else 0.0


def _burn():
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    x = 1.0001
    while True:
        x = (x * x) % 1.7 + 1.0


# ----------------------------------------------------------------------- probe
def scan(bus):
    found = []
    for addr in range(0x03, 0x78):
        try:
            bus.read_byte(addr)
            found.append(addr)
        except (IOError, OSError):
            pass
    return found


def _sh(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT,
                                       universal_newlines=True, timeout=10).strip()
    except Exception as e:
        return "(failed: %s)" % e


def probe():
    info = {"time": time.time(), "python": sys.version.split()[0]}
    info["model"] = _sh("tr -d '\\0' < /proc/device-tree/model")
    info["l4t"] = _sh("head -1 /etc/nv_tegra_release")
    info["nvpmodel"] = _sh("nvpmodel -q 2>/dev/null | tail -2")
    info["uptime"] = _sh("uptime")
    info["camera"] = _sh("ls /dev/video* 2>/dev/null")
    info["jetbot_package"] = _sh(
        "python3 -c 'import jetbot,os;print(os.path.dirname(jetbot.__file__))'")
    info["jetbot_motor_py"] = _sh(
        "python3 -c 'import jetbot.motor,inspect;"
        "print(inspect.getsource(jetbot.motor))'")
    info["services"] = _sh("systemctl is-active jetbot_stats jetbot_jupyter")
    info["wifi"] = _sh("iwconfig 2>/dev/null | grep -E 'ESSID|Bit Rate|Signal'")
    try:
        bus = open_bus()
        addrs = scan(bus)
        info["i2c"] = ["0x%02x" % a for a in addrs]
        info["has_pca9685"] = PCA_ADDR in addrs
        info["has_ina219"] = INA_ADDR in addrs
        info["has_ads1115"] = ADS_ADDR in addrs
        info["has_oled"] = 0x3C in addrs
        if PCA_ADDR in addrs:
            info["pca_prescale"] = bus.read_byte_data(PCA_ADDR, 0xFE)
            info["pca_mode1"] = bus.read_byte_data(PCA_ADDR, 0x00)
        if INA_ADDR in addrs:
            v, i = Ina219(bus).read()
            info["bus_v"], info["charge_i"] = v, i
        if ADS_ADDR in addrs:
            a = Ads1115(bus)
            info["ads_pins_v"] = [a.read(k) for k in range(4)]
    except Exception as e:
        info["i2c_error"] = str(e)
    info["rails"] = Rails().read()
    info["thermal"] = thermal()
    print(json.dumps(info, indent=1))


# ------------------------------------------------------------------------- run
def run(exp, out_path, countdown):
    bus = open_bus()
    pca = Pca9685(bus)
    addrs = scan(bus)
    ina = Ina219(bus) if INA_ADDR in addrs else None
    ads = Ads1115(bus) if ADS_ADDR in addrs else None
    rails, cpu = Rails(), Cpu()
    rail_names = sorted(rails.files)
    burners = []

    def stop_all(*_):
        try:
            release(pca)
        finally:
            for b in burners:
                b.terminate()

    signal.signal(signal.SIGTERM, lambda *a: (stop_all(), sys.exit(1)))
    signal.signal(signal.SIGHUP, lambda *a: (stop_all(), sys.exit(1)))

    profile = exp["profile"]
    hz = float(exp.get("log_hz", 50.0))
    total = sum(p[0] for p in profile)
    if total > 180:
        raise SystemExit("profile is %.0f s long; the agent refuses over 180 s" % total)
    for k in range(int(countdown), 0, -1):
        print("starting in %d..." % k)
        sys.stdout.flush()
        time.sleep(1.0)

    cols = ["t", "cmd_l", "cmd_r", "bus_v", "charge_i", "v_5v", "i_5v", "p_5v",
            "p_gpu", "p_cpu", "temp_cpu", "temp_gpu", "cpu"]
    if ads:
        cols += ["ads0", "ads1", "ads2", "ads3"]
    rows = []
    try:
        t0 = time.time()
        t_seg = 0.0
        for k, (seconds, left, right) in enumerate(profile):
            if exp.get("jetson") == "stress":
                if k == 1 and not burners:
                    for _ in range(multiprocessing.cpu_count()):
                        b = multiprocessing.Process(target=_burn)
                        b.daemon = True
                        b.start()
                        burners.append(b)
                elif k != 1 and burners:
                    for b in burners:
                        b.terminate()
                    burners = []
            write_motor(pca, "left", left)
            write_motor(pca, "right", right)
            t_seg += seconds
            while True:
                now = time.time() - t0
                if now >= t_seg:
                    break
                bv, ci = ina.read() if ina else (float("nan"), float("nan"))
                r = rails.read()
                th = thermal()

                def rail(name, j):
                    return r[name][j] if name in r else float("nan")
                row = [now, left, right, bv, ci,
                       rail("POM_5V_IN", 0), rail("POM_5V_IN", 1), rail("POM_5V_IN", 2),
                       rail("POM_5V_GPU", 2), rail("POM_5V_CPU", 2),
                       th.get("CPU-therm", float("nan")),
                       th.get("GPU-therm", float("nan")), cpu.read()]
                if ads:
                    row += [ads.read(j) for j in range(4)]
                rows.append(row)
                rest = (len(rows) / hz) - (time.time() - t0)
                if rest > 0:
                    time.sleep(rest)
    finally:
        stop_all()

    with open(out_path, "w") as f:
        f.write("# experiment = %s\n# source = robot\n# started = %f\n"
                % (exp.get("name", "?"), t0))
        f.write("# pca_prescale = %d\n# has_ina219 = %s\n# has_ads1115 = %s\n"
                % (pca.prescale, bool(ina), bool(ads)))
        f.write("# rails = %s\n" % " ".join(rail_names))
        f.write(",".join(cols) + "\n")
        for row in rows:
            f.write(",".join("%.6g" % x for x in row) + "\n")
    print("wrote %s: %d samples over %.1f s" % (out_path, len(rows), total))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.required = True
    sub.add_parser("probe")
    sub.add_parser("stop")
    r = sub.add_parser("run")
    r.add_argument("experiment", help="JSON file from the twin")
    r.add_argument("-o", "--out", default="log.csv")
    r.add_argument("--countdown", type=float, default=3)
    a = ap.parse_args()
    if a.cmd == "probe":
        probe()
    elif a.cmd == "stop":
        release(Pca9685(open_bus()))
        print("motors released")
    else:
        with open(a.experiment) as f:
            run(json.load(f), a.out, a.countdown)


if __name__ == "__main__":
    main()
