# ISS Tracker Display

A Raspberry Pi-powered display that tracks the International Space Station in real time. It renders a rotating 3D globe with the ISS position and telemetry on a 3.5" LCD, and can show a list of people currently in space.

## Features

- **Live ISS tracking** — spinning 3D globe with an ISS marker that updates continuously
- **Telemetry HUD** — latitude, longitude, altitude, velocity, region name, and data freshness
- **People in Space** — toggle to a crew list showing every astronaut in orbit and their spacecraft
- **View switching** — wire up an optional toggle switch to flip between views
- **Runs 24/7** — systemd service with watchdog, auto-restart, and graceful shutdown
- **Free APIs** — works out of the box with no API keys required

---

## Hardware

| Component | Details |
|-----------|---------|
| **Raspberry Pi** | Model 3B or newer, running Raspberry Pi OS |
| **LCD display** | Waveshare 3.5" RPi LCD (F) — 320x480, SPI, plugs directly onto the GPIO header |
| **Power supply** | **5.1 V** / 2.5 A+ — the official Raspberry Pi supply is strongly recommended (see below) |
| **Toggle switch** *(optional)* | Latching switch wired between GPIO 17 and GND to switch display views |

> The display sits on top of the Pi — no breadboard or extra wiring needed unless you add the toggle switch.

> Make sure you get the **(F)** model. Other models, such as the (A), use different drivers and won't work with this code as-is.

> ⚠️ **The power supply makes or breaks display performance.** The Pi throttles
> whenever the 5 V rail dips below 4.63 V: the CPU is capped at half speed
> (600 MHz) and the core clock — **which the SPI display bus is derived from** —
> drops from 400 to 250 MHz. The result is a silently slow, jittery display
> with no error message anywhere; a generic "5 V" phone charger typically sits
> just above the trip point and sags below it under load, and a thin micro-USB
> cable can ruin even a good adapter. This is why the official supply is
> **5.1 V**: it holds voltage at the connector under full load. Check yours
> with `vcgencmd get_throttled` — anything other than `throttled=0x0` means
> power problems (bit 0 = under-voltage right now, bit 16 = it has happened
> since boot). During development this exact issue silently halved this
> project's frame rate for months.

---

## Quick Start

### 1. Enable SPI

Open the Raspberry Pi configuration tool and enable the SPI interface:

```bash
sudo raspi-config
# Interface Options → SPI → Enable
```

### 2. Install system packages

```bash
sudo apt install libgeos-dev libproj-dev python3-dev swig liblgpio-dev fonts-b612
```

### 3. Increase the SPI buffer size

The display needs to send full frames (307 KB each) over SPI. The default buffer is only 4 KB, so this step is required:

```bash
echo 'options spidev bufsiz=307200' | sudo tee /etc/modprobe.d/spidev.conf
sudo reboot
```

### 4. Clone and install

```bash
git clone https://github.com/filbot/iss-tracker.git
cd iss-tracker
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 5. Configure

```bash
cp .env.example .env
```

The defaults work out of the box. Edit `.env` only if you need to change GPIO pins or add an optional N2YO API key.

### 6. Run

```bash
iss-display
```

On the first run, the app generates 144 globe frames. This takes a few minutes on a Pi 3B — subsequent starts load from cache in a few seconds.

To test without hardware (generates preview PNGs instead):

```bash
iss-display --preview-only
```

### 7. GPIO permissions

If you get permission errors, add your user to the required groups:

```bash
sudo usermod -a -G gpio,spi $USER
```

Then log out and back in.

---

## Display Views

### ISS Tracker *(default)*

A continuously rotating 3D globe showing the current ISS position with a glowing marker. Two telemetry bars overlay the globe:

- **Top bar** — LAT (latitude), LON (longitude), OVER (region the ISS is flying over)
- **Bottom bar** — ALT (altitude in km), VEL (velocity in km/h), LAST (time since last data update — `38s`, `12m`, `47h`)

Position is fetched every 30 seconds and interpolated between updates for smooth tracking. Dead reckoning is capped at 5 minutes past the last real fix; if data is more than 10 minutes old the OVER cell switches to an amber **NO LINK** flag and the marker holds at the last known position.

### People in Space

A text list of every astronaut currently in orbit, grouped by spacecraft. Data refreshes every 5 minutes.

### Switching Views

If you wire a latching toggle switch between **GPIO 17** and **GND**:
- Switch closed (connected to GND) → ISS Tracker
- Switch open → People in Space

Without a switch, the ISS Tracker view is shown by default. The pin is configurable via the `GPIO_TOGGLE` environment variable.

---

## APIs

The app uses free, public APIs. No registration is required for the default setup.

| API | Purpose | Key Required |
|-----|---------|:------------:|
| [Where the ISS at?](https://wheretheiss.at) | ISS position (primary) | No |
| [Open Notify ISS](http://open-notify.org) | ISS position (fallback) | No |
| [N2YO](https://www.n2yo.com/api/) | ISS position (fallback) | Yes (free) |
| [Open Notify Astros](http://open-notify.org) | People in Space | No |

The app tries the primary API first. If it fails, it falls through to the fallbacks automatically. N2YO is only used if you provide an API key in `.env`.

---

## Run on Boot (systemd)

A production-ready systemd service is included for 24/7 operation.

### Setup

```bash
mkdir -p ~/.config/systemd/user
ln -sf ~/iss-tracker/deploy/iss-display.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable iss-display.service
sudo loginctl enable-linger $USER
systemctl --user start iss-display.service
```

### What the service provides

- **Watchdog** — restarts the process if it stops responding (60s timeout)
- **Auto-restart** — recovers from crashes automatically
- **Memory cap** — limits usage to 450 MB
- **Graceful shutdown** — turns off the backlight and releases GPIO on stop

### Common commands

```bash
systemctl --user status iss-display       # Check status
journalctl --user -u iss-display -f       # Follow live logs
systemctl --user restart iss-display      # Restart (e.g. after config changes)
systemctl --user stop iss-display         # Stop
```

---

## System Resilience (recommended for 24/7 operation)

The app keeps running through network outages (it shows the last known
position with an amber **NO LINK** flag and a humanized **LAST** age), but
some failures can only be fixed below the application: in August 2026 the
Pi's `brcmfmac` Wi-Fi driver hung at the kernel level, leaving NetworkManager
and wpa_supplicant in unkillable D-state for 47 hours until a manual power
cycle. `deploy/install-system.sh` installs three system-level safeguards
against that class of failure:

```bash
cd ~/iss-tracker/deploy
sudo ./install-system.sh
```

- **Connectivity watchdog** (`net-watchdog.timer`, every 2 min) — on outage,
  escalates through recovery stages: restart NetworkManager → cycle the Wi-Fi
  radio (10 min) → reload the `brcmfmac` driver (20 min) → forced reboot
  (45 min, at most one per 2 h). Every action runs in a detached transient
  unit so a hung stage can't wedge the watchdog itself. If the gateway is
  reachable but DNS is dead (upstream/ISP outage a reboot can't fix),
  escalation stops after the NetworkManager restart.
- **Hardware watchdog** — Raspberry Pi OS already arms the BCM2835 runtime
  watchdog (1 min); the drop-in adds `RebootWatchdogSec=2min` so even a
  reboot wedged on hung kernel tasks completes via hardware reset.
- **Wi-Fi power save off** — a known trigger for `brcmfmac` SDIO firmware
  hangs, and pointless on a mains-powered appliance.

Watch the watchdog's decisions with:

```bash
journalctl -u net-watchdog -f
```

---

## Configuration

### Environment Variables (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `ISS_API_URL` | `https://api.wheretheiss.at/v1/satellites/25544` | Primary ISS API |
| `N2YO_API_KEY` | *(empty)* | Optional N2YO fallback key ([get one free](https://www.n2yo.com/api/)) |
| `GPIO_DC` | `22` | LCD data/command pin |
| `GPIO_RST` | `27` | LCD reset pin |
| `GPIO_BL` | `18` | LCD backlight pin |
| `GPIO_TOGGLE` | `17` | View toggle switch pin |
| `SPI_SPEED_HZ` | `48000000` | SPI clock speed. 66.7 MHz verified on this unit with a pinned 400 MHz core; per-panel — validate with `bench/display_bench.py` before raising |
| `PREVIEW_ONLY` | `false` | Set to `true` to generate PNGs instead of driving the LCD |
| `ISS_LOG_LEVEL` | `INFO` | Log verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

### Theme (`theme.toml`)

All colors, fonts, and layout are controlled by `theme.toml` in the project root. Edit this file to customize the display without touching any code.

Colors are `[R, G, B]` arrays (0–255). The default theme uses Boeing Primary Flight Display conventions — green labels, white values, magenta markers.

```toml
# Example: change the ocean color
[globe]
ocean_color = [62, 160, 206]

# Example: make all HUD labels cyan
[hud.label]
color = [0, 255, 255]
```

Styles cascade: `hud base → bar base → element override`. Set a style broadly and override it where needed. Delete any line to fall back to the built-in default.

---

## Globe Frame Cache

The 3D globe is rendered as 480 pre-computed frames (`num_frames` in `theme.toml`) using [Cartopy](https://scitools.org.uk/cartopy). Two caches live in `var/frame_cache/`:

- `globe_480f.npz` — the rendered RGB frames (Cartopy output)
- `globe_480f_rgb565_320x480.npy` — the display-ready RGB565 conversion

- **First run** — generates frames (~2–4 minutes on Pi 4, longer on Pi 3), then converts to RGB565 (~4.5 minutes on Pi 3) and caches both
- **Subsequent runs** — loads the RGB565 cache directly in a few seconds (the `.npz` isn't even opened)
- **Regenerate** — delete `var/frame_cache/` (needed after changing globe colors or `num_frames` in `theme.toml`); deleting only the `.npz` and regenerating it also invalidates the RGB565 cache automatically (mtime comparison)

> **SPI bandwidth ceiling.** A full 320×480 RGB565 frame is 307 KB. The SPI clock is derived from the Pi's **core clock** (SCLK = core / even divisor), so the requested `SPI_SPEED_HZ` only means what it says when the core clock is pinned (`core_freq=400` + `core_freq_min=400` in config.txt) and the PSU is healthy (`vcgencmd get_throttled` = `0x0`) — an under-volted or scaling core silently ran this bus at 25 MHz for months. At the measured 66.7 MHz (verified artifact-free on this panel; see `bench/RESULTS.md`), a full frame takes ~38 ms and a globe-disc region ~13 ms, supporting the 34 FPS rotation (480 frames / 14 s). Full-screen 60 FPS remains physically impossible (~148 Mbit/s needed), but disc-region updates have headroom to ~76 FPS.

To speed things up, you can generate the cache on a faster machine and copy it over:

```bash
# On your dev machine
iss-display --preview-only
scp var/frame_cache/globe_144f.npz pi@raspberrypi:~/iss-tracker/var/frame_cache/
```

---

## Project Structure

```
iss-tracker/
├── theme.toml                          # Display theme (colors, fonts, layout)
├── .env.example                        # Environment variable template
├── pyproject.toml                      # Package metadata and dependencies
├── deploy/
│   ├── iss-display.service             # systemd user service
│   ├── install-system.sh               # installs the system-level safeguards below
│   ├── net-watchdog.sh                 # staged network recovery script
│   ├── net-watchdog.service            # oneshot unit running the script
│   ├── net-watchdog.timer              # fires the check every 2 minutes
│   ├── 10-hardware-watchdog.conf       # systemd hardware-watchdog drop-in
│   └── wifi-powersave-off.conf         # NetworkManager Wi-Fi powersave drop-in
├── src/iss_display/
│   ├── app/main.py                     # Entry point, main loop, view toggling
│   ├── display/lcd_driver.py           # ST7796S SPI driver, rendering engine
│   ├── data/iss_client.py              # ISS position API client with fallbacks
│   ├── data/astros_client.py           # People in Space API client
│   ├── data/geography.py               # Region name lookup
│   ├── config.py                       # Settings from environment variables
│   └── theme.py                        # Theme TOML loader, cascade resolution
├── var/
│   ├── frame_cache/                    # Cached globe frames (auto-generated)
│   └── previews/                       # Preview PNGs (--preview-only mode)
├── start.sh                            # Manual start script
└── stop.sh                             # Manual stop script
```

---

## Troubleshooting

**Display is blank or freezes after a few seconds**
The SPI buffer size is probably still at the 4 KB default. Follow step 3 in Quick Start to increase it to 307,200 bytes, then reboot.

**Globe rotation is slow or jittery**
Almost always power. Run `vcgencmd get_throttled` — anything other than
`throttled=0x0` means the Pi is (or has been) under-voltage and is silently
running the CPU and the SPI display bus at reduced, fluctuating speed. Use a
5.1 V supply (official recommended) and a short, thick USB cable, and verify
`0x0` under load. Also pin the core clock (`core_freq=400` +
`core_freq_min=400` in `/boot/firmware/config.txt`) so the SPI clock can't
wander with system load. `bench/display_bench.py` measures the actual
achieved SPI throughput and frame rates on your hardware.

**Permission denied on SPI or GPIO**
Add your user to the `gpio` and `spi` groups: `sudo usermod -a -G gpio,spi $USER`, then log out and back in.

**Globe frames regenerate every time**
Make sure `var/frame_cache/` exists and is writable by your user.

**Service won't start or keeps restarting**
Check the logs: `journalctl --user -u iss-display -f`

---

## License

[MIT](LICENSE)
