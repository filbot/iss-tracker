#!/usr/bin/env python3
"""Display performance benchmark harness for the ISS tracker.

Measures, on the real hardware, every layer of the display pipeline so that
proposed optimizations can be evaluated with numbers instead of guesses:

  cpu   CPU-side render-path costs (numpy copies, RGB565 region extraction,
        marker composite) plus GPIO/SPI syscall overheads. Safe to run any
        time — does not touch the display contents.

  spi   Effective SPI throughput and end-to-end achievable FPS at a range of
        requested clock speeds, streaming the REAL committed globe frames through
        the REAL ST7796S init sequence. This doubles as a visual integrity
        check: watch the panel — if the globe is corrupted at a given speed,
        that speed fails on this wiring. Requires exclusive display access:
        stop iss-display.service first.

Usage:
    python bench/display_bench.py cpu
    python bench/display_bench.py spi --speeds 32,48,62.5,80 --frames 120
    python bench/display_bench.py spi --speeds 48 --frames 240 --hold 5

The tool prints a machine-readable summary block at the end of each section
(lines prefixed with RESULT) for easy collection.
"""

from __future__ import annotations

import argparse
import statistics
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from iss_display.display.globe_frames import GlobeFramesError, load_frames  # noqa: E402
from iss_display.display.lcd_driver import _rgb_to_rgb565  # noqa: E402
from iss_display.theme import THEME  # noqa: E402

WIDTH, HEIGHT = 320, 480
FRAME_BYTES = WIDTH * HEIGHT * 2
NUM_FRAMES = THEME.globe.num_frames

# Globe disc geometry, from theme.toml globe.scale like the app
GLOBE_SIZE = int(min(WIDTH, HEIGHT) * THEME.globe.scale)
GX0 = (WIDTH - GLOBE_SIZE) // 2
GY0 = (HEIGHT - GLOBE_SIZE) // 2
DISC_BYTES = GLOBE_SIZE * GLOBE_SIZE * 2

# GPIO pins (defaults from .env.example)
GPIO_DC, GPIO_RST, GPIO_BL = 22, 27, 18

# ST7796S commands
SWRESET, SLPOUT, COLMOD, MADCTL = 0x01, 0x11, 0x3A, 0x36
INVON, NORON, DISPON = 0x21, 0x13, 0x29
CASET, RASET, RAMWR = 0x2A, 0x2B, 0x2C


def vcgencmd(*args: str) -> str:
    try:
        return subprocess.run(["vcgencmd", *args], capture_output=True,
                              text=True, timeout=5).stdout.strip()
    except Exception:
        return "n/a"


def load_full_frames() -> np.ndarray:
    """The app's globe frames composited onto full-screen background, or
    synthetic noise frames if they're missing or stale."""
    try:
        disc = load_frames(WIDTH, HEIGHT)
    except GlobeFramesError as e:
        print(f"  {e}\n  using synthetic frames")
        return np.random.randint(0, 65535, (NUM_FRAMES, HEIGHT, WIDTH),
                                 dtype=np.uint16).astype(">u2")
    full = np.empty((disc.shape[0], HEIGHT, WIDTH), dtype=">u2")
    full[:] = _rgb_to_rgb565(*THEME.globe.background)
    full[:, GY0:GY0 + GLOBE_SIZE, GX0:GX0 + GLOBE_SIZE] = disc
    print(f"  loaded real globe frames: {full.shape}")
    return full


def env_snapshot() -> None:
    print(f"  core clock : {vcgencmd('measure_clock', 'core')}")
    print(f"  arm clock  : {vcgencmd('measure_clock', 'arm')}")
    print(f"  throttled  : {vcgencmd('get_throttled')}")
    print(f"  temp       : {vcgencmd('measure_temp')}")


def pct(sorted_samples, p):
    return sorted_samples[min(len(sorted_samples) - 1, int(len(sorted_samples) * p))]


def summarize(name: str, samples_ms: list[float]) -> None:
    s = sorted(samples_ms)
    print(f"RESULT {name}: n={len(s)} p50={pct(s, .5):.3f}ms "
          f"p95={pct(s, .95):.3f}ms p99={pct(s, .99):.3f}ms max={s[-1]:.3f}ms")


# ─── CPU benchmarks ──────────────────────────────────────────────────────────

def bench_cpu() -> None:
    print("=== CPU render-path benchmarks ===")
    env_snapshot()

    frames = load_full_frames()

    frame_buf = bytearray(FRAME_BYTES)
    fb = np.frombuffer(frame_buf, dtype=">u2").reshape(HEIGHT, WIDTH)

    def timeit(name, fn, n=200):
        # warmup
        for _ in range(5):
            fn(0)
        t = []
        for i in range(n):
            t0 = time.perf_counter()
            fn(i)
            t.append((time.perf_counter() - t0) * 1000)
        summarize(name, t)

    nf = frames.shape[0]
    timeit("full_frame_copyto", lambda i: np.copyto(fb, frames[i % nf]))
    timeit("disc_region_copy", lambda i: fb.__setitem__(
        (slice(GY0, GY0 + GLOBE_SIZE), slice(GX0, GX0 + GLOBE_SIZE)),
        frames[i % nf][GY0:GY0 + GLOBE_SIZE, GX0:GX0 + GLOBE_SIZE]))
    timeit("disc_ascontig_tobytes", lambda i: np.ascontiguousarray(
        fb[GY0:GY0 + GLOBE_SIZE, GX0:GX0 + GLOBE_SIZE]).tobytes())

    # Marker composite (mirrors _draw_iss_marker_rgb565 array ops)
    r = 8
    dim = 2 * r + 1
    dy = np.arange(-r, r + 1, dtype=np.int32)
    dist_sq = dy[:, None] ** 2 + dy[None, :] ** 2
    color_buf = np.zeros((dim, dim), dtype=np.uint16)
    mask = np.zeros((dim, dim), dtype=np.bool_)

    def marker(i):
        color_buf[:] = 0
        for rr in (49, 25, 9):
            color_buf[dist_sq <= rr] = 0xF81F
        np.not_equal(color_buf, 0, out=mask)
        fb[200:200 + dim, 150:150 + dim][mask] = color_buf[mask]

    timeit("marker_composite", marker, n=500)

    # GPIO toggle latency through rpi-lgpio
    try:
        import RPi.GPIO as GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(GPIO_DC, GPIO.OUT)
        t = []
        for _ in range(1000):
            t0 = time.perf_counter()
            GPIO.output(GPIO_DC, GPIO.HIGH)
            t.append((time.perf_counter() - t0) * 1000)
        summarize("gpio_output_call", t)
    except Exception as e:
        print(f"  gpio bench skipped: {e}")


# ─── SPI benchmarks ──────────────────────────────────────────────────────────

class BenchDisplay:
    """Minimal ST7796S driver mirroring lcd_driver.py's init and windowing."""

    def __init__(self, speed_hz: int):
        import spidev
        import RPi.GPIO as GPIO
        self.GPIO = GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        for pin in (GPIO_DC, GPIO_RST, GPIO_BL):
            GPIO.setup(pin, GPIO.OUT)
        self.spi = spidev.SpiDev()
        self.spi.open(0, 0)
        self.spi.max_speed_hz = speed_hz
        self.spi.mode = 0
        self._cmd = bytearray(1)
        self._win = bytearray(4)

    def set_speed(self, hz: int):
        self.spi.max_speed_hz = hz

    def command(self, c: int):
        self.GPIO.output(GPIO_DC, 0)
        self._cmd[0] = c
        self.spi.writebytes2(self._cmd)

    def data(self, d: int):
        self.GPIO.output(GPIO_DC, 1)
        self._cmd[0] = d
        self.spi.writebytes2(self._cmd)

    def init_display(self):
        g = self.GPIO
        g.output(GPIO_RST, 1); time.sleep(0.02)
        g.output(GPIO_RST, 0); time.sleep(0.02)
        g.output(GPIO_RST, 1); time.sleep(0.20)
        self.command(SWRESET); time.sleep(0.15)
        self.command(SLPOUT); time.sleep(0.15)
        self.command(COLMOD); self.data(0x55)
        self.command(MADCTL); self.data(0x48)
        self.command(INVON)
        self.command(NORON); time.sleep(0.01)
        self.command(DISPON); time.sleep(0.12)
        g.output(GPIO_BL, 1)

    def set_window(self, x0, y0, x1, y1):
        g = self.GPIO
        self._cmd[0] = CASET
        g.output(GPIO_DC, 0); self.spi.writebytes2(self._cmd)
        g.output(GPIO_DC, 1)
        w = self._win
        w[0] = x0 >> 8; w[1] = x0 & 0xFF; w[2] = x1 >> 8; w[3] = x1 & 0xFF
        self.spi.writebytes2(w)
        self._cmd[0] = RASET
        g.output(GPIO_DC, 0); self.spi.writebytes2(self._cmd)
        g.output(GPIO_DC, 1)
        w[0] = y0 >> 8; w[1] = y0 & 0xFF; w[2] = y1 >> 8; w[3] = y1 & 0xFF
        self.spi.writebytes2(w)
        self._cmd[0] = RAMWR
        g.output(GPIO_DC, 0); self.spi.writebytes2(self._cmd)
        g.output(GPIO_DC, 1)

    def write_full(self, buf):
        self.set_window(0, 0, WIDTH - 1, HEIGHT - 1)
        self.spi.writebytes2(buf)

    def write_region(self, x0, y0, x1, y1, buf):
        self.set_window(x0, y0, x1, y1)
        self.spi.writebytes2(buf)

    def close(self):
        try:
            self.spi.close()
        except Exception:
            pass


def bench_spi(speeds_mhz: list[float], n_frames: int, hold_sec: float) -> None:
    if subprocess.run(["systemctl", "--user", "is-active", "--quiet",
                       "iss-display"]).returncode == 0:
        print("ERROR: iss-display.service is active. Stop it first:\n"
              "  systemctl --user stop iss-display", file=sys.stderr)
        sys.exit(1)

    print("=== SPI throughput / end-to-end FPS benchmarks ===")
    env_snapshot()

    frames = load_full_frames()
    nf = frames.shape[0]
    full_bufs = [np.ascontiguousarray(frames[i]).tobytes() for i in range(0, nf, nf // 24)]

    disp = BenchDisplay(int(speeds_mhz[0] * 1e6))
    disp.init_display()

    frame_buf = bytearray(FRAME_BYTES)
    fb = np.frombuffer(frame_buf, dtype=">u2").reshape(HEIGHT, WIDTH)

    for mhz in speeds_mhz:
        hz = int(mhz * 1e6)
        disp.set_speed(hz)
        print(f"\n--- requested SPI clock: {mhz} MHz ---")

        # 1. Raw full-frame throughput (24 distinct frames, cycled)
        t = []
        for i in range(24):
            t0 = time.perf_counter()
            disp.write_full(full_bufs[i % len(full_bufs)])
            t.append((time.perf_counter() - t0) * 1000)
        s = sorted(t)
        eff_mbps = FRAME_BYTES * 8 / (pct(s, .5) / 1000) / 1e6
        print(f"RESULT spi{mhz}_full_frame: p50={pct(s, .5):.2f}ms "
              f"p95={pct(s, .95):.2f}ms -> effective {eff_mbps:.1f} Mbit/s "
              f"({1000 / pct(s, .5):.1f} fps ceiling)")

        # 2. set_window overhead (window commands only, no payload)
        t = []
        for _ in range(200):
            t0 = time.perf_counter()
            disp.set_window(0, 0, WIDTH - 1, HEIGHT - 1)
            t.append((time.perf_counter() - t0) * 1000)
        summarize(f"spi{mhz}_set_window", t)

        # 3. End-to-end globe-region frame loop: replicate the app's
        #    per-frame work (disc copy from frames -> extract -> window -> SPI)
        t = []
        for i in range(n_frames):
            t0 = time.perf_counter()
            src = frames[i % nf]
            fb[GY0:GY0 + GLOBE_SIZE, GX0:GX0 + GLOBE_SIZE] = \
                src[GY0:GY0 + GLOBE_SIZE, GX0:GX0 + GLOBE_SIZE]
            region = np.ascontiguousarray(
                fb[GY0:GY0 + GLOBE_SIZE, GX0:GX0 + GLOBE_SIZE]).tobytes()
            disp.write_region(GX0, GY0, GX0 + GLOBE_SIZE - 1,
                              GY0 + GLOBE_SIZE - 1, region)
            t.append((time.perf_counter() - t0) * 1000)
        s = sorted(t)
        print(f"RESULT spi{mhz}_globe_loop: p50={pct(s, .5):.2f}ms "
              f"p95={pct(s, .95):.2f}ms p99={pct(s, .99):.2f}ms "
              f"-> {1000 / pct(s, .5):.1f} fps sustained")
        print(f"  core during test: {vcgencmd('measure_clock', 'core')} "
              f"throttled: {vcgencmd('get_throttled')}")

        if hold_sec > 0:
            # Leave a live rotation on screen for visual inspection
            print(f"  holding rotation for {hold_sec}s — check panel for corruption")
            t_end = time.time() + hold_sec
            i = 0
            while time.time() < t_end:
                src = frames[i % nf]
                fb[GY0:GY0 + GLOBE_SIZE, GX0:GX0 + GLOBE_SIZE] = \
                    src[GY0:GY0 + GLOBE_SIZE, GX0:GX0 + GLOBE_SIZE]
                region = np.ascontiguousarray(
                    fb[GY0:GY0 + GLOBE_SIZE, GX0:GX0 + GLOBE_SIZE]).tobytes()
                disp.write_region(GX0, GY0, GX0 + GLOBE_SIZE - 1,
                                  GY0 + GLOBE_SIZE - 1, region)
                i += 1
                time.sleep(0.02)
            # Phase separator: 2 s of black at a conservative clock so the
            # viewer can count phases unambiguously.
            disp.set_speed(int(24e6))
            disp.write_full(bytes(FRAME_BYTES))
            time.sleep(2.0)

    # Restore a sane display state (full frame at conservative speed)
    disp.set_speed(int(24e6))
    disp.write_full(full_bufs[0])
    disp.close()
    print("\nDone. Restart the service with: systemctl --user start iss-display")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["cpu", "spi"])
    ap.add_argument("--speeds", default="32,48,62.5,80",
                    help="comma-separated requested SPI clocks in MHz")
    ap.add_argument("--frames", type=int, default=120,
                    help="frames per end-to-end loop test")
    ap.add_argument("--hold", type=float, default=0.0,
                    help="seconds of live rotation to hold per speed for visual check")
    args = ap.parse_args()

    if args.mode == "cpu":
        bench_cpu()
    else:
        bench_spi([float(s) for s in args.speeds.split(",")],
                  args.frames, args.hold)


if __name__ == "__main__":
    main()
