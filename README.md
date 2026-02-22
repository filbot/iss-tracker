## ISS Tracker Display

Self-contained Python application that fetches real-time ISS telemetry, renders a continuously spinning 3D globe with the ISS position, and drives a 3.5" TFT LCD (320x480, ST7796S controller) on a Raspberry Pi.

### Flow

1. Query the ISS position from `wheretheiss.at` (with fallback APIs).
2. Render an orthographic globe projection via Cartopy, with ISS marker and occlusion effects.
3. Overlay HUD telemetry bars (LAT, LON, OVER, ALT, VEL, LAST).
4. Send the frame to either the LCD (via SPI) or a preview PNG.

### Hardware Prerequisites

- Raspberry Pi 3B (or newer) running Raspberry Pi OS.
- 3.5" IPS LCD (320x480, ST7796S) connected to the SPI header.
- SPI enabled via `raspi-config`.

### Software Requirements

- Python 3.11+ (uses `tomllib` from stdlib)
- System packages: `libopenjp2-7`, `libtiff6`, etc. (installed automatically on Raspberry Pi OS).
- Python dependencies listed in `pyproject.toml` (install via `pip install -e .`).

---

### Installing

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install the package (editable)
pip install -e .

# Create your .env file
cp .env.example .env   # then edit with your API keys
```

### Running

```bash
iss-display                 # run on hardware
iss-display --preview-only  # dry run without hardware (generates preview PNGs)

# Or run directly:
python -m iss_display.app.main
```

---

### Theme Configuration

The display appearance is controlled by **`theme.toml`** in the project root. Edit this file to customize colors, fonts, sizes, and layout without touching any Python code.

Colors are `[R, G, B]` arrays with values 0-255. The default theme is inspired by Boeing Primary Flight Display conventions.

```toml
# Change the globe ocean color
[globe]
ocean_color = [62, 160, 206]

# Change all HUD labels to cyan
[hud.label]
color = [0, 255, 255]

# Override just the OVER element's value color
[hud.top.over.value]
color = [255, 0, 255]
```

#### 3-Level Cascade

HUD text styles use a cascade system — set a style at a broad level and override it at narrower levels:

```
hud base  >  bar base  >  element override
```

- **`[hud.label]`** — sets the default for ALL labels across both bars
- **`[hud.top.label]`** — overrides for all top-bar labels only
- **`[hud.top.lat.label]`** — overrides for just the LAT label

Delete or comment out any line to fall back to the built-in default. If `theme.toml` is missing entirely, the app runs with hardcoded defaults.

#### HUD Elements

| Bar | Element | Sub-parts | Notes |
|-----|---------|-----------|-------|
| Top | `lat` | label, value | Cell-width positioned |
| Top | `lon` | label, value | Cell-width positioned |
| Top | `over` | label, value | Right-aligned (region name) |
| Bottom | `alt` | label, value, unit ("km") | Cell-width positioned |
| Bottom | `vel` | label, value, unit ("km/h") | Cell-width positioned |
| Bottom | `age` | label, value | Right-aligned (data freshness) |

Each sub-part (label, value, unit) accepts `color`, `size`, and `font` (absolute path to a `.ttf`/`.otf` file).

---

### Environment Variables

Create a `.env` file in the project root (or export manually):

```bash
# ISS API
ISS_API_URL=https://api.wheretheiss.at/v1/satellites/25544
N2YO_API_KEY=               # Optional: free key from https://www.n2yo.com/api/

# Display hardware pins
GPIO_DC=22
GPIO_RST=27
GPIO_BL=18
SPI_BUS=0
SPI_DEVICE=0
SPI_SPEED_HZ=48000000

# Display dimensions
DISPLAY_WIDTH=320
DISPLAY_HEIGHT=480

# Modes
PREVIEW_ONLY=false
ISS_PREVIEW_DIR=var/previews
ISS_LOG_LEVEL=INFO
```

---

### Running on Boot (systemd)

A production-ready systemd user service is included at `deploy/iss-display.service`.

```bash
# 1. Symlink the service file
mkdir -p ~/.config/systemd/user
ln -sf ~/iss-tracker/deploy/iss-display.service ~/.config/systemd/user/

# 2. Reload systemd
systemctl --user daemon-reload

# 3. Enable on boot
systemctl --user enable iss-display.service

# 4. Enable lingering (so it starts without a login session)
sudo loginctl enable-linger $USER

# 5. Start now
systemctl --user start iss-display.service
```

#### Service Features

- **Watchdog** (`WatchdogSec=60s`) — systemd restarts the process if it stops responding.
- **Auto-restart** (`Restart=always`, `RestartSec=5`) — restarts on any crash.
- **Crash limit** (`StartLimitBurst=5` in 5 minutes) — reboots the system after repeated failures.
- **Memory cap** (`MemoryMax=250M`) — prevents runaway memory usage.
- **Graceful shutdown** — SIGTERM triggers LCD cleanup (backlight off, sleep mode, GPIO release).

#### Operations

```bash
# Check status
systemctl --user status iss-display

# Follow live logs
journalctl --user -u iss-display -f

# Restart (e.g., after editing theme.toml)
systemctl --user restart iss-display

# Stop
systemctl --user stop iss-display

# Disable from boot
systemctl --user disable iss-display
```

#### Permissions

The user running the service needs GPIO and SPI access:

```bash
sudo usermod -a -G gpio,spi $USER
```

Or run as root if permissions are difficult to manage.

---

### Frame Generation

The globe is rendered as 144 pre-computed frames using Cartopy orthographic projections. These are cached to disk at `var/frame_cache/globe_144f.npz`.

- **First run**: generates all frames using multiprocessing (~1-2 minutes on Pi 4, longer on Pi 3).
- **Subsequent runs**: loads from cache (~3 seconds).
- **Cache invalidation**: delete `var/frame_cache/` to regenerate (needed after changing globe colors/scale in `theme.toml`).

To speed up first-time setup, you can generate frames on a fast machine and copy the cache:

```bash
# On dev machine
iss-display --preview-only   # generates frames + cache
scp var/frame_cache/globe_144f.npz pi@raspberrypi:~/iss-tracker/var/frame_cache/
```

---

### Project Structure

```
iss-tracker/
├── theme.toml                      # Display theme (colors, fonts, layout)
├── .env                            # API keys and hardware config
├── pyproject.toml                  # Package metadata and dependencies
├── deploy/
│   └── iss-display.service         # systemd user service
├── src/iss_display/
│   ├── app/main.py                 # Entry point, render loop, orbital interpolator
│   ├── display/lcd_driver.py       # ST7796S SPI driver, globe + HUD rendering
│   ├── data/iss_client.py          # ISS API client with fallback chain
│   ├── data/geography.py           # Region name lookup
│   ├── config.py                   # Settings from environment variables
│   └── theme.py                    # Theme dataclasses, TOML loader, cascade resolution
├── var/
│   ├── frame_cache/                # Cached globe frames (auto-generated)
│   └── previews/                   # Preview PNGs (--preview-only mode)
├── start.sh                        # Manual start script
└── stop.sh                         # Manual stop script (sends SIGTERM)
```
