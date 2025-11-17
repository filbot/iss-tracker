## ISS E-Paper Display

Self-contained Python script that fetches the latest ISS telemetry, downloads a Mapbox Static Image, renders a lightweight overlay, and drives the GeeekPi 2.13" tri-color e-paper HAT.

### Flow
1. Query the ISS position from `wheretheiss.at`.
2. Request a portrait Mapbox Static Image centered on that coordinate.
3. Draw a crosshair + telemetry banner and convert the frame into red/black bitplanes.
4. Send the frame to either the real panel (via `waveshare_epd`) or a preview PNG.

### Hardware Prerequisites
- Raspberry Pi Zero v1.3 or any newer Pi running Raspberry Pi OS.
- GeeekPi 2.13" e-paper HAT connected to the SPI header.
- SPI enabled via `raspi-config`.

### Software Requirements
- Python 3.10+
- System packages: `libopenjp2-7`, `libtiff6`, etc. (installed automatically on Raspberry Pi OS).
- Python dependencies listed in `requirements.txt` (install via `pip install -r requirements.txt`).
- For hardware runs, install Waveshare’s Python driver by cloning their repository and adding `python/lib` to `PYTHONPATH`, or vendor the module beside this project. Use `--preview-only` if you only need PNG output.

### Environment Variables
Create a `.env` file in the repo root or export the variables manually:

```
MAPBOX_TOKEN=<required Mapbox access token>
MAPBOX_USERNAME=mapbox
MAPBOX_STYLE_ID=streets-v12
MAPBOX_ZOOM=2
MAP_PIN_COLOR=#ED1C24
ISS_API_URL=https://api.wheretheiss.at/v1/satellites/25544
EPD_WIDTH=128
EPD_HEIGHT=250
EPD_LOGICAL_WIDTH=122
EPD_PAD_LEFT=3
EPD_PAD_RIGHT=3
EPD_HAS_RED=true
EPD_PREVIEW_ONLY=false
ISS_PREVIEW_DIR=var/previews
```

### Installing & Running
```bash
pip install -r requirements.txt
python -m iss_display.app.main --preview-only  # dry run without hardware
python -m iss_display.app.main                 # single hardware refresh
```

The systemd and Makefile automation has been removed to keep the project focused on the immediate data → image → display flow. If you want to run it in a loop, wrap `python -m iss_display.app.main` with the scheduler of your choice (cron, systemd, etc.).
