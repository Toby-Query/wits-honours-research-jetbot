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
import threading
import sys
import time

BUS = 1
PCA_ADDR = 0x60
INA_ADDR = 0x41
ADS_ADDR = 0x48

# PCA9685 channel map, from the Waveshare schematic and Adafruit_MotorHAT
HAT = {"left": dict(pwm=8, in2=9, in1=10), "right": dict(pwm=13, in2=12, in1=11)}
DRV = {"left": dict(in1=1, in2=0), "right": dict(in1=2, in2=3)}


class RawBus(object):
    """The few SMBus calls this file uses, straight on /dev/i2c-N.

    The stock JetBot image keeps its Python I2C libraries inside a Docker
    container; the host has none. The device node is all that is needed.
    Words are little-endian, as SMBus has them.
    """
    I2C_SLAVE = 0x0703

    def __init__(self, n):
        import fcntl
        self._ioctl = fcntl.ioctl
        self.fd = os.open("/dev/i2c-%d" % n, os.O_RDWR)
        self._addr = None

    def _to(self, addr):
        # Set the address on every transaction, never from a cache: the
        # address lives in the open file, which forked processes share, so a
        # child talking to another chip silently redirects the parent's reads
        # (it did: jetson_load read the PCA9685 as the battery for 10 s).
        self._ioctl(self.fd, self.I2C_SLAVE, addr)
        self._addr = addr

    def read_byte(self, addr):
        self._to(addr)
        return os.read(self.fd, 1)[0]

    def read_byte_data(self, addr, reg):
        self._to(addr)
        os.write(self.fd, bytes(bytearray([reg])))
        return bytearray(os.read(self.fd, 1))[0]

    def write_byte_data(self, addr, reg, value):
        self._to(addr)
        os.write(self.fd, bytes(bytearray([reg, value & 0xFF])))

    def write_i2c_block_data(self, addr, reg, values):
        self._to(addr)
        os.write(self.fd, bytes(bytearray([reg] + [v & 0xFF for v in values])))

    def read_word_data(self, addr, reg):
        self._to(addr)
        os.write(self.fd, bytes(bytearray([reg])))
        b = bytearray(os.read(self.fd, 2))
        return b[0] | (b[1] << 8)

    def write_word_data(self, addr, reg, value):
        self._to(addr)
        os.write(self.fd, bytes(bytearray([reg, value & 0xFF, (value >> 8) & 0xFF])))


# One I2C bus, two threads (one timing the motors, one reading sensors): every
# transaction takes this first.
BUS_LOCK = threading.RLock()


def open_bus():
    for name in ("smbus", "smbus2"):
        try:
            return __import__(name).SMBus(BUS)
        except ImportError:
            pass
    return RawBus(BUS)


# --------------------------------------------------------------------- PCA9685
class Pca9685(object):
    MODE1, MODE2, PRESCALE, LED0 = 0x00, 0x01, 0xFE, 0x06

    def __init__(self, bus, addr=PCA_ADDR, freq=1600, dry=False):
        # dry: every write goes to channel 15's registers (0x42..0x45), which
        # nothing is connected to. Same bus traffic, no motion: for timing.
        self.bus, self.addr, self.dry, self.shadow = bus, addr, dry, {}
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

    # One register per write, as Adafruit_PCA9685 does. A single four-byte
    # write only lands on four registers if MODE1's auto-increment bit is
    # set, and nothing here sets it: sent as a block, all four bytes pile
    # into the first register and the output never changes.
    def _write4(self, base, on, off):
        # Only the bytes that changed are sent. A speed change touches two of
        # the four, which halves the time the command takes to land.
        with BUS_LOCK:
            for k, v in enumerate((on & 0xFF, on >> 8, off & 0xFF, off >> 8)):
                if self.shadow.get(base + k) != v or self.dry:
                    self.bus.write_byte_data(self.addr, (0x42 + k) if self.dry else base + k, v)
                    self.shadow[base + k] = v

    def set_pwm(self, ch, on, off):
        self._write4(self.LED0 + 4 * ch, on, off)

    def get_pwm(self, ch):
        b = [self.bus.read_byte_data(self.addr, self.LED0 + 4 * ch + k)
             for k in range(4)]
        return b[0] | (b[1] << 8), b[2] | (b[3] << 8)

    def set_all(self, on, off):
        self._write4(0xFA, on, off)


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


def set_direction(pca, side, value, alpha=1.0, beta=0.0):
    """The part of a motor command that does not by itself move anything on
    the TB6612FNG: which way to turn. Sent ahead of time."""
    mapped = int(255.0 * (alpha * value + beta))
    hat = HAT[side]
    if mapped < 0:
        pca.set_pwm(hat["in2"], 0, 4096)
        pca.set_pwm(hat["in1"], 4096, 0)
    else:
        pca.set_pwm(hat["in1"], 0, 4096)
        pca.set_pwm(hat["in2"], 4096, 0)


def set_power(pca, side, value, alpha=1.0, beta=0.0):
    """The part that starts and stops the wheel: the PWM duty. Two bytes on
    the TB6612FNG's channel. Then the DRV8870 channels, which on that board
    are the ones that matter and on this one are not connected."""
    mapped = int(255.0 * (alpha * value + beta))
    speed = min(max(abs(mapped), 0), 255)
    hat, drv = HAT[side], DRV[side]
    pca.set_pwm(hat["pwm"], 0, speed * 16)
    return mapped, speed, drv


def set_drv(pca, mapped, speed, drv):
    if mapped < 0:
        pca.set_pwm(drv["in1"], 0, 0)
        pca.set_pwm(drv["in2"], 0, speed * 16)
    else:
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

    last = None

    def read(self):
        # Now and then a read comes back as nonsense (4.096 V on a 10 V
        # rail). A real rail does not move a volt in 20 ms: read it again.
        for _ in range(3):
            with BUS_LOCK:
                raw = _swap(self.bus.read_word_data(self.addr, 0x02))
            bus_v = (raw >> 3) * 0.004
            if self.last is None or abs(bus_v - self.last) < 1.0:
                break
        self.last = bus_v
        with BUS_LOCK:
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

    def _rail(self, name):
        f = self.files[name]
        v, i = self._read(f["v"]), self._read(f["i"])
        return (v, i, v * i)

    def read_one(self, name):
        return {name: self._rail(name)} if name in self.files else {}

    def read_others(self, skip):
        return {n: self._rail(n) for n in self.files if n != skip}

    def read(self):
        return {n: self._rail(n) for n in self.files}


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
    # A burner is forked from run(), so it inherits run()'s SIGTERM/SIGHUP
    # handlers, which release the motors over the shared I2C file. Being
    # stopped must just end it.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGHUP, signal.SIG_DFL)
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
            ina = Ina219.__new__(Ina219)            # read only: do not configure
            ina.bus, ina.addr, ina.r = bus, INA_ADDR, 0.1
            info["ina_config"] = "0x%04x" % _swap(bus.read_word_data(INA_ADDR, 0x00))
            v, i = ina.read()
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
def run(exp, out_path, countdown, dry=False):
    bus = open_bus()
    pca = Pca9685(bus, dry=dry)
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
    slow_every = max(1, int(round(hz / 5.0)))
    r, th, load = {}, {}, 0.0
    events = []                       # (seconds, left, right): when each command was sent
    state = {"cmd": (0.0, 0.0), "seg": 0, "done": False}

    def wait_until(t0, when):
        """Sleep to within 3 ms of `when`, then spin: sleep alone is good to
        a few milliseconds on this kernel, and a turn is only 290 of them."""
        while True:
            left = when - (time.time() - t0)
            if left <= 0:
                return
            if left > 0.004:
                time.sleep(left - 0.003)

    def commander(t0):
        """Runs the profile on the clock and nothing else. The sensors are
        read on another thread: some take 30 ms, some 200, and a 0.29 s turn
        cannot wait for them.

        A command is sent in two parts. The direction pins go first, a few
        milliseconds early: they move nothing by themselves. Then, exactly
        on time and with the bus already held, the two PWM duties, which are
        what start and stop the wheels. The time logged is when those landed.
        """
        t_next = 0.0
        try:
            for k, (seconds, left, right) in enumerate(profile):
                moving = bool(left or right)
                if moving:                       # which way, ahead of time
                    wait_until(t0, t_next - 0.012)
                    with BUS_LOCK:
                        set_direction(pca, "left", left)
                        set_direction(pca, "right", right)
                wait_until(t0, t_next - 0.004)
                with BUS_LOCK:                   # hold the bus, then go on time
                    wait_until(t0, t_next)
                    l = set_power(pca, "left", left)
                    r_ = set_power(pca, "right", right)
                    events.append((time.time() - t0, left, right))
                    set_drv(pca, *l)
                    set_drv(pca, *r_)
                state["cmd"], state["seg"] = (left, right), k
                t_next += seconds
            wait_until(t0, t_next)
        finally:
            with BUS_LOCK:
                release(pca)
            events.append((time.time() - t0, 0.0, 0.0))
            state["cmd"], state["done"] = (0.0, 0.0), True

    try:
        t0 = time.time()
        worker = threading.Thread(target=commander, args=(t0,))
        worker.daemon = True
        worker.start()
        while not state["done"]:
            now = time.time() - t0
            k = state["seg"]
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
            left, right = state["cmd"]
            bv, ci = ina.read() if ina else (float("nan"), float("nan"))
            n = len(rows)
            if n % slow_every == 0:
                r = rails.read_one("POM_5V_IN")
            if n % (5 * slow_every) == 0:
                r.update(rails.read_others("POM_5V_IN"))
                th = thermal()
                load = cpu.read()

            def rail(name, j):
                return r[name][j] if name in r else float("nan")
            row = [now, left, right, bv, ci,
                   rail("POM_5V_IN", 0), rail("POM_5V_IN", 1), rail("POM_5V_IN", 2),
                   rail("POM_5V_GPU", 2), rail("POM_5V_CPU", 2),
                   th.get("CPU-therm", float("nan")),
                   th.get("GPU-therm", float("nan")), load]
            if ads:
                row += [ads.read(j) for j in range(4)]
            rows.append(row)
            rest = len(rows) / hz - (time.time() - t0)
            if rest > 0:
                time.sleep(rest)
        worker.join(2.0)
    finally:
        stop_all()

    with open(out_path, "w") as f:
        f.write("# experiment = %s\n# source = robot\n# started = %f\n"
                % (exp.get("name", "?"), t0))
        f.write("# pca_prescale = %d\n# has_ina219 = %s\n# has_ads1115 = %s\n"
                % (pca.prescale, bool(ina), bool(ads)))
        f.write("# rails = %s\n" % " ".join(rail_names))
        f.write("# commands_sent_at = %s\n" % " ".join(
            "%.4f:%g:%g" % e for e in events))
        f.write(",".join(cols) + "\n")
        for row in rows:
            f.write(",".join("%.6g" % x for x in row) + "\n")
    print("wrote %s: %d samples over %.1f s" % (out_path, len(rows), total))


def identify(duty=0.5, seconds=1.0):
    """Which motor driver is fitted? Wheels must be OFF THE GROUND.

    The two possible chips hang off different PCA9685 channels. The normal
    software writes both sets; this writes one set at a time and watches the
    battery rail for the dip that running motors cause.
    """
    bus = open_bus()
    pca = Pca9685(bus)
    ina = Ina219(bus)
    counts = int(duty * 4095)

    def watch(t):
        v, end = [], time.time() + t
        while time.time() < end:
            v.append(ina.read()[0])
            time.sleep(0.01)
        v.sort()
        return v[len(v) // 2], v

    out = {}
    try:
        release(pca)
        time.sleep(0.5)
        base, _ = watch(0.6)
        # 1: the DRV8870 inputs only (channels 0..3); 8..13 stay released
        for side in ("left", "right"):
            pca.set_pwm(DRV[side]["in1"], 0, counts)
            pca.set_pwm(DRV[side]["in2"], 0, 0)
        out["readback_ch1"] = pca.get_pwm(1)     # did the write land?
        time.sleep(0.25)
        v_drv, _ = watch(seconds)
        release(pca)
        time.sleep(1.2)
        base2, _ = watch(0.6)
        # 2: the TB6612FNG pins only (channels 8..13); 0..3 stay at zero
        for side in ("left", "right"):
            pca.set_pwm(HAT[side]["in1"], 0, 4096)
            pca.set_pwm(HAT[side]["in2"], 4096, 0)
            pca.set_pwm(HAT[side]["pwm"], 0, counts)
        time.sleep(0.25)
        v_tb, _ = watch(seconds)
    finally:
        release(pca)
    out.update({"rest_v": base, "rest_v_2": base2,
           "drv8870_channels_v": v_drv, "tb6612_channels_v": v_tb,
           "sag_drv8870_mV": round((base - v_drv) * 1000),
           "sag_tb6612_mV": round((base2 - v_tb) * 1000),
           "charge_i": ina.read()[1], "pwm_hz": 25e6 / 4096 / (pca.prescale + 1)})
    a, b = out["sag_drv8870_mV"], out["sag_tb6612_mV"]
    out["verdict"] = ("drv8870" if a > 40 and a > 3 * max(b, 1) else
                      "tb6612" if b > 40 and b > 3 * max(a, 1) else "unclear")
    print(json.dumps(out, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.required = True
    sub.add_parser("probe")
    sub.add_parser("stop")
    sub.add_parser("identify")
    r = sub.add_parser("run")
    r.add_argument("experiment", help="JSON file from the twin")
    r.add_argument("-o", "--out", default="log.csv")
    r.add_argument("--countdown", type=float, default=3)
    r.add_argument("--dry", action="store_true",
                   help="send every write to an unconnected channel: the bus "
                        "is exercised and timed, the motors do not move")
    a = ap.parse_args()
    if a.cmd == "probe":
        probe()
    elif a.cmd == "identify":
        identify()
    elif a.cmd == "stop":
        release(Pca9685(open_bus()))
        print("motors released")
    else:
        with open(a.experiment) as f:
            run(json.load(f), a.out, a.countdown, a.dry)


if __name__ == "__main__":
    main()
