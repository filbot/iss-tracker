## ISS E-Paper Display

Self-contained Python application that fetches live ISS telemetry, downloads the corresponding Mapbox Static Image, renders overlays, and drives the GeeekPi 2.13" tri-color e-paper HAT.

### Features
- Portrait Mapbox Static Tiles pipeline that computes slippy tile indices from the ISS position, downloads a single 512px tile (@2x), crops it to the 122×250 portrait aspect ratio, and caches both the tile and finished portrait image on disk.
- ISS telemetry client supporting both `wheretheiss.at` and `open-notify` schemas with local state caching.
- Image processing pipeline that draws overlays and converts frames to the panel’s red/black bitplanes.
- Hardware abstraction layer with automatic fallback to preview PNGs when SPI/GPIO modules are unavailable.
- Optional GPIO heartbeat LED that pulses between updates and flashes rapidly during refreshes.
- CLI with single-run, daemon, cache-only, and test-pattern modes.
- Pytest suite covering the critical data pipeline bits.

### Hardware Prerequisites
- Raspberry Pi Zero v1.3 or any newer Pi running Raspberry Pi OS.
- GeeekPi 2.13" e-paper HAT connected to the SPI header.
- SPI enabled via `raspi-config`.

### Software Requirements
- Python 3.10+
- System packages: `libopenjp2-7`, `libtiff6`, etc. (installed automatically on Raspberry Pi OS).
- Python dependencies listed in `requirements.txt` (install via `pip install -r requirements.txt`).

### Environment Variables
Create a `.env` file in the repo root or export the variables manually (see `.env.example` for defaults):

```
MAPBOX_TOKEN=<your_mapbox_access_token>
MAPBOX_USERNAME=your-mapbox-username
MAPBOX_STYLE_ID=your-style-id
MAPBOX_ZOOM=2
MAPBOX_TILE_SIZE=512
MAPBOX_HIDPI=true
MAPBOX_BEARING=0
MAPBOX_REFRESH_RADIUS_KM=400
MAPBOX_MAX_REQUESTS_PER_HOUR=60
MAPBOX_ENABLE_STATIC_FALLBACK=true
PORTRAIT_WORK_WIDTH=128
PORTRAIT_TRIM_LEFT=3
PORTRAIT_TRIM_RIGHT=3
MAP_PIN_COLOR=#ED1C24
ISS_API_URL=https://api.wheretheiss.at/v1/satellites/25544
ISS_POLL_INTERVAL=60
EPD_PREVIEW_ONLY=false  # set true for development without hardware
EPD_WIDTH=128
EPD_LOGICAL_WIDTH=122
EPD_PAD_LEFT=3
EPD_PAD_RIGHT=3
LED_ENABLED=false
LED_PIN=12
```

Directories used for cache/preview/state default to `var/cache`, `var/previews`, and `var/state`. Override via `ISS_CACHE_DIR`, `ISS_PREVIEW_DIR`, `ISS_STATE_DIR` if desired.

### Installing & Running
If you are configuring everything directly on a Raspberry Pi Zero, see `docs/pi-zero-setup.md` for a complete step-by-step walkthrough tailored to that board.

```bash
pip install -r requirements.txt
python -m iss_display.app.main --preview-only refresh-once   # dry run without hardware
python -m iss_display.app.main refresh-once                  # single hardware refresh
python -m iss_display.app.main daemon                        # continuous updates
python -m iss_display.app.main cache-only                    # refresh cached map only
python -m iss_display.app.main test-pattern                  # simple hardware test
```

### Auto-Start on Raspberry Pi Zero (Raspberry Pi OS)
Running as a `systemd` service is the most reliable option for a Pi Zero: it keeps resource usage low, ensures the app starts after networking comes online, and automatically restarts on failure.

1. **Prepare the application once**
	```bash
	sudo apt update && sudo apt install python3-venv -y
	git clone https://github.com/<your-fork>/iss-tracker.git
	cd iss-tracker
	python3 -m venv .venv
	source .venv/bin/activate
	pip install -r requirements.txt
	cp .env.example .env    # create and fill in MAPBOX_TOKEN, etc.
	```

2. **Create the service definition** at `/etc/systemd/system/iss-display.service`:
	```ini
	[Unit]
	Description=ISS E-paper Display
	After=network-online.target spi-config.service
	Wants=network-online.target

	[Service]
	Type=simple
	User=pi
	WorkingDirectory=/home/pi/e-display-iss-map
	EnvironmentFile=/home/pi/e-display-iss-map/.env
	Environment=PYTHONPATH=/home/pi/e-display-iss-map/src
	ExecStart=/home/pi/e-display-iss-map/.venv/bin/python -m iss_display.app.main daemon
	Restart=on-failure
	RestartSec=10

	[Install]
	WantedBy=multi-user.target
	```

3. **Enable and start the service**
	```bash
	sudo systemctl daemon-reload
	sudo systemctl enable --now iss-display.service
	```

4. **Monitor logs when needed**
	```bash
	journalctl -u iss-display.service -f
	```

This setup boots the Pi Zero into the display loop automatically, keeps the virtual environment isolated, waits for networking before requesting Mapbox tiles, and restarts the process if it crashes.

### Mapbox Portrait Workflow
1. **Legacy style definition** – Your Mapbox style (`MAPBOX_USERNAME/MAPBOX_STYLE_ID`), pin color (`#ED1C24` by default), and 128×250 portrait framing from the historic Static Images URL are captured in config so you can tweak zoom or styling centrally.
2. **Slippy tile math** – For each ISS fix, the app converts latitude/longitude + zoom to `{z}/{x}/{y}` indices using the standard Web Mercator formulas and requests a single style tile via the Mapbox Static Tiles API (`/tiles/512/...@2x`).
3. **Crop + resize** – The square tile is cropped around the ISS-centered longitude to match the 128×250 portrait aspect, resized with Lanczos filtering, then trimmed to the active 122-pixel panel width before being padded back to the 128-pixel hardware buffer.
4. **Tri-color conversion** – Pixels near the pin color are routed to the red plane; remaining pixels are thresholded into black/white masks, yielding the two bitplanes required by the GeekPi 2.13" tri-color panel.
5. **Caching & graceful fallback** – Tiles are stored under `var/cache/tiles/<style>/<zoom>/` and re-used whenever the ISS stays within the configured radius; processed portraits are cached separately, and if the Static Tiles endpoint ever errors out the app replays the legacy Static Images URL to keep frames flowing without exceeding quotas.

### Optional Heartbeat LED
- Wire an LED + resistor to any BCM GPIO pin (default `GPIO12`) and ground.
- Set `LED_ENABLED=true` in `.env` and, if necessary, change `LED_PIN` to match your wiring.
- During idle periods the LED mimics the legacy shell script by pulsing with random on/off times (roughly 50–200 ms on, 50–550 ms off) for a soft heartbeat. When the display refresh starts the LED switches to a tight 50 ms strobe until the update completes, then smoothly returns to the heartbeat pattern.
- If the app can’t import `RPi.GPIO` (e.g., when running on a laptop), it automatically disables the LED and logs a warning.

### Makefile Automation
For repeat deployments, take advantage of the provided `Makefile` targets (`INSTALL_DIR` defaults to the currently checked-out repo and `PI_USER` auto-detects the invoking user—override either if you need a different path or service account):

```bash
make deploy            # install apt deps, refresh repo in place, set up venv, install service
make restart           # restart the daemon
make journal           # follow service logs
make stop|start|status # control the systemd unit
```

`make deploy` requires `sudo` privileges, installs `python3-venv git gettext-base libssl-dev libcurl4-openssl-dev libopenblas0 libopenblas-dev liblapack-dev`, verifies the repo already exists under `INSTALL_DIR`, pulls the latest changes, bootstraps the virtualenv, ensures `.env` exists (copied from `.env.example` if missing), renders `systemd/iss-display.service` with your chosen paths via `envsubst`, reloads `systemd`, and enables the service so the display loop starts immediately. Heavy wheels (e.g., `numpy`, `Pillow`) are built with `TMPDIR`/`PIP_CACHE_DIR` redirected to `$(INSTALL_DIR)/.build/` so they never run out of space on the Pi’s small `/tmp`; delete that directory if you ever want to reclaim the cache.

### Project Structure
```
src/iss_display/
	app/                # CLI + scheduler
	config.py           # settings loader
	data/               # Mapbox & ISS API clients
	display/            # hardware & preview drivers
	pipeline/           # layout + frame encoding
tests/                # pytest-based coverage for core logic
```

### Testing
```bash
python -m pytest
```
