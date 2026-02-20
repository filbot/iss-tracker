## ISS Tracker Display

Self-contained Python application that fetches real-time ISS telemetry, renders a continuously spinning 3D globe with the ISS position, and drives a 3.5" TFT LCD (320x480, ST7796S controller) on a Raspberry Pi.

### Flow
1. Query the ISS position from `wheretheiss.at`.
2. Render an orthographic globe projection via Cartopy, with ISS marker and occlusion effects.
3. Overlay HUD telemetry bars (LAT, LON, ALT, VEL).
4. Send the frame to either the LCD (via SPI) or a preview PNG.

### Hardware Prerequisites
- Raspberry Pi 3B (or newer) running Raspberry Pi OS.
- 3.5" IPS LCD (320x480, ST7796S) connected to the SPI header.
- SPI enabled via `raspi-config`.

### Software Requirements
- Python 3.10+
- System packages: `libopenjp2-7`, `libtiff6`, etc. (installed automatically on Raspberry Pi OS).
- Python dependencies listed in `pyproject.toml` (install via `pip install -e .`).

### Environment Variables
Create a `.env` file in the repo root or export the variables manually:

```
ISS_API_URL=https://api.wheretheiss.at/v1/satellites/25544
PREVIEW_ONLY=false
ISS_PREVIEW_DIR=var/previews
```

### Installing & Running
```bash
pip install -e .
iss-display --preview-only  # dry run without hardware
iss-display                 # run on hardware
```

Or run directly:
```bash
python -m iss_display.app.main --preview-only  # dry run without hardware
python -m iss_display.app.main                 # run on hardware
```
