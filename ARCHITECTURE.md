# ISS Tracker - Architecture

## Overview

A Raspberry Pi application that renders a continuously spinning 3D globe with real-time ISS position tracking on a 3.5" TFT LCD (320x480, ST7796S controller). The ISS marker stays locked to its geographic coordinates as the globe rotates, disappearing behind the Earth and reappearing with occlusion effects.

**Target hardware:** Raspberry Pi 3 Model B Rev 1.2 (or newer), 3.5" IPS LCD (320x480), SPI interface
**Runtime:** Designed for 24/7 continuous operation via systemd
**API:** wheretheiss.at (primary), N2YO (fallback), polled every 30 seconds

---

## Architecture

```
main() → run_loop()
  ├─ ISSClient              Fetches ISS position from API
  ├─ ISSOrbitInterpolator    Background thread: fetches every 30s, interpolates between
  ├─ LcdDisplay              Renders globe + ISS marker + HUD
  │   ├─ Cartopy frames     144 pre-rendered orthographic globe frames (cached as NPZ)
  │   ├─ ISS marker         Drawn into RGB565 byte buffer with occlusion
  │   └─ HUD overlay        Per-element themed telemetry bars (top: LAT/LON/OVER, bottom: ALT/VEL/LAST)
  └─ ST7796S                Low-level SPI driver for the LCD controller

Threading:
  Main thread ──── SPI-limited render loop (get_telemetry → render → SPI write)
  Daemon thread ── API fetch every 30s with exponential backoff
```

### Entry Point

`src/iss_display/app/main.py` → `main()` → `run_loop(settings)`

1. Load settings from `.env` via `Settings.load()`
2. Override `preview_only` from CLI `--preview-only` flag if set
3. Create `ISSClient` and `LcdDisplay`
4. Start `ISSOrbitInterpolator` (background API fetcher + interpolator)
5. Run render loop (no FPS cap — SPI transfer is the natural frame limiter)
6. Signal handling: SIGINT, SIGTERM set `running = False`
7. systemd integration: sends `READY=1`, `WATCHDOG=1` each frame, `STOPPING=1` on shutdown
8. On shutdown: stop interpolator, close display (backlight off, screen clear, sleep mode)

---

## Source Files

| File | Lines | Purpose |
|------|-------|---------|
| `src/iss_display/app/main.py` | 373 | Entry point, render loop, `ISSOrbitInterpolator` |
| `src/iss_display/display/lcd_driver.py` | 996 | `ST7796S` SPI driver, `LcdDisplay` (globe + ISS + HUD rendering) |
| `src/iss_display/theme.py` | 383 | Theme dataclasses, TOML loader, cascade resolution |
| `src/iss_display/data/iss_client.py` | 122 | `ISSClient` with API fallback chain, `ISSFix` dataclass |
| `src/iss_display/data/geography.py` | 54 | Bounding-box continent/ocean lookup |
| `src/iss_display/config.py` | 60 | `Settings` frozen dataclass, loads from environment variables |

### Tests

| File | Tests |
|------|-------|
| `tests/test_geography.py` | `geography.get_common_area_name()` |

### Deployment

| File | Purpose |
|------|---------|
| `deploy/iss-display.service` | systemd user service (`Type=notify`, `Restart=always`, watchdog) |
| `theme.toml` | Display theme (colors, fonts, layout) — loaded at startup |
| `start.sh` | Manual start: `./start.sh` (or `./start.sh --preview-only`) |
| `stop.sh` | Manual stop: `./stop.sh` (sends SIGTERM) |

---

## Theme System

### Architecture

The theme is a hierarchy of frozen dataclasses defined in `theme.py`, loaded from `theme.toml` at module import time via `tomllib`.

```
Theme
├── globe: GlobeStyle          # 3D globe colors, sizing, animation
├── marker: MarkerStyle        # ISS dot: colors, rings, visibility
└── hud: HudStyle              # HUD bars, layout, and text styles
    ├── label/value/unit       # Base TextStyle (color, size, font)
    ├── top: TopBarStyle       # Bar properties + elements
    │   ├── lat: HudElement    #   label/value/unit TextStyle overrides + cell_width
    │   ├── lon: HudElement
    │   └── over: HudElement
    └── bottom: BottomBarStyle
        ├── alt: HudElement
        ├── vel: HudElement
        └── age: HudElement
```

### 3-Level Cascade

HUD text styles (color, size, font) resolve through: **element > bar > hud base**.

`TextStyle` fields set to `None` inherit from the parent level. Resolution happens once at init via `resolve_text_style()`, producing `_ResolvedElement` objects with loaded PIL fonts. No per-frame cascade lookups.

### TOML Loading

1. `_find_theme_toml()` walks up from `theme.py` to find `theme.toml`
2. `_build(cls, data, base)` recursively merges TOML dict over dataclass defaults
3. Missing TOML keys keep their Python defaults; lists convert to tuples (for RGB)
4. On parse error or missing file, falls back to built-in defaults with a log warning

### Font Loading

- `HudStyle.font_search_paths` lists fonts to try in order (B612 Mono preferred)
- Per-element `TextStyle.font` can override with an absolute path
- `LcdDisplay._get_font(path, size)` caches loaded fonts by `(path, size)` tuple
- Default config loads 3 font objects (sizes 11, 15, 17) from the same file

---

## Rendering Pipeline

### Per-Frame Pipeline

```
bytearray(frame_bytes_cache[n])     # ~1 ms  — copy pre-computed RGB565 bytes
  → draw ISS marker into byte buffer  # ~1 ms  — direct RGB565 byte writes (~15x15 px)
  → patch HUD bars into byte buffer   # <1 ms  — memcpy of cached RGB565 bytes
  → SPI write                         # ~65 ms — 307 KB at 48 MHz (hard bottleneck)
```

Total CPU work per frame: ~2-5 ms. SPI transfer dominates at ~65 ms.

### Globe (Cartopy)

- 144 frames pre-rendered at startup using Cartopy orthographic projection
- Each frame is 2.5° apart in longitude (-180° to +177.5°)
- Generated using multiprocessing (`mp.Pool`) across all CPU cores
- Uses 110m resolution Natural Earth features (fastest geometry)
- Raw canvas buffer rendering (no PNG encode/decode round-trip)
- Cached as `var/frame_cache/globe_144f.npz` (uncompressed, ~45 MB)
- On first run: generates all frames (~1-2 min on Pi 4). Subsequent runs: loads from NPZ (~3s)
- All frames pre-converted to RGB565 bytes at startup (`frame_bytes_cache`)
- Globe occupies 70% of display area, centered
- Colors configurable via `[globe]` section in `theme.toml`

### ISS Marker

- Drawn directly into the RGB565 byte buffer (no PIL operations in hot path)
- Plotted at correct geographic lat/lon relative to each frame's `central_lon`
- Uses orthographic projection math to map lat/lon to pixel coordinates
- Exaggerated altitude: ISS rendered at 1.10x Earth radius for visibility
- Occlusion: fades and disappears when behind Earth using geometric horizon calculation
- Visual: 3 concentric glow rings + core + white center dot
- Size and opacity scale with visibility angle
- Colors configurable via `[marker]` section in `theme.toml`

### HUD Overlay

- **Top bar** (48px): LAT, LON, OVER (region name, right-aligned)
- **Bottom bar** (48px): ALT (km), VEL (km/h), LAST (data freshness, right-aligned)
- Typography: B612 Mono preferred (Airbus cockpit font), falls back through DejaVu/Liberation/Free
- Each element has independently resolved colors and fonts via the theme cascade
- Layout: 8px grid padding, explicit pixel positioning
- **Cached as RGB565 bytes** — only re-rendered when formatted display values change (~every 30s)

### Display Output

- RGB565 conversion via NumPy (vectorized) for PIL Images (HUD bars, frame pre-computation)
- Direct RGB565 byte writes for ISS marker (no PIL/NumPy in hot path)
- SPI: 48 MHz, `writebytes2()` for binary data
- No FPS cap — SPI transfer is the natural frame limiter (~15 FPS max)

### Preview Mode

When running without hardware (`--preview-only` or missing SPI/GPIO):
- Renders identically but skips SPI write
- Saves PNG to `var/previews/` every 30th frame (~1 PNG per 2 seconds)
- PNGs are reconstructed from RGB565 bytes back to RGB

---

## Data Flow

### API Fetching

- `ISSClient.get_fix()` tries primary API, then fallbacks, then last known position, then default (0°, -150°)
- Primary: `https://api.wheretheiss.at/v1/satellites/25544` (returns lat, lon, altitude, velocity)
- Fallback: N2YO API (requires free API key)
- Uses `requests.Session()` for connection pooling
- 5-second timeout per request

### Orbital Interpolation

- Background daemon thread fetches real position every 30 seconds (~2 calls/min)
- **Exponential backoff** on consecutive failures: 30s → 60s → 120s → 300s max, resets on success
- Between fetches, position is linearly interpolated using estimated velocity
- Velocity estimated from consecutive API fixes (degrees/second)
- Default longitude velocity: ~0.065°/s (360° / 92.68-minute orbital period)
- Thread-safe via `threading.Lock`

### Error Recovery

- **API**: exponential backoff, falls back to cached position
- **Render loop**: counts consecutive errors; reinits display after 5, exits after 20
- **Fetch thread**: health-checked every 30s, auto-restarted if dead or stale (>5 min)
- **Display hardware**: periodic health checks (SPI readback), light re-init every 15 min, full re-init every 60 min
- **systemd**: watchdog restart if unresponsive >60s, auto-restart on crash, system reboot after 5 crashes in 5 minutes

### Shutdown

1. Signal handler sets `running = False`
2. Main loop exits, sends `STOPPING=1` to systemd
3. `interpolator.stop()` — sets flag, joins thread (2s timeout)
4. `driver.close()` → `ST7796S.close()`:
   - Backlight off
   - Fill black 3x (prevent ghosting)
   - DISPOFF, SLPIN (sleep mode)
   - RST held low
   - SPI close, GPIO cleanup

---

## Performance Profile (RPi 3B/4, 24/7)

| Metric | Value | Notes |
|--------|-------|-------|
| Memory (steady state) | ~100-150 MB | 144 PIL frames + 144 RGB565 buffers + Cartopy |
| CPU per frame | ~2-5 ms | bytearray copy + ISS marker bytes + HUD patch |
| SPI transfer | ~65 ms/frame | 307 KB at 48 MHz (hard bottleneck) |
| Max FPS (SPI limited) | ~15 FPS | CPU overhead is negligible |
| API calls | 2/min | Well under rate limits |
| Startup (cached) | ~3-5 seconds | Loading NPZ + pre-computing RGB565 |
| Startup (uncached, Pi 4) | ~1-2 minutes | Generating 144 Cartopy frames (multiprocessing, 110m) |
| Memory leaks | None | Fixed-size caches, proper cleanup |

---

## Configuration

### Runtime Settings

All settings loaded from `.env` via `Settings.load()` in `config.py`.

| Variable | Default | Used by |
|----------|---------|---------|
| `ISS_API_URL` | `https://api.wheretheiss.at/v1/satellites/25544` | `ISSClient` |
| `N2YO_API_KEY` | (none) | `ISSClient` fallback |
| `DISPLAY_WIDTH` | `320` | `LcdDisplay`, `ST7796S` |
| `DISPLAY_HEIGHT` | `480` | `LcdDisplay`, `ST7796S` |
| `GPIO_DC` | `22` | `ST7796S` (data/command pin) |
| `GPIO_RST` | `27` | `ST7796S` (reset pin) |
| `GPIO_BL` | `18` | `ST7796S` (backlight pin) |
| `SPI_BUS` | `0` | `ST7796S` |
| `SPI_DEVICE` | `0` | `ST7796S` |
| `SPI_SPEED_HZ` | `48000000` | `ST7796S` (48 MHz) |
| `PREVIEW_ONLY` | `false` | `LcdDisplay` (hardware vs preview) |
| `ISS_PREVIEW_DIR` | `var/previews` | Preview output path |
| `ISS_LOG_LEVEL` | `INFO` | Logging |

### Theme

See `theme.toml` in the project root. All visual styling — globe colors, marker appearance, HUD colors/fonts/sizes/layout — is configured there. See `README.md` for full documentation.

---

## Dependencies

### Required

| Package | Version | Purpose |
|---------|---------|---------|
| Pillow | >=10.0.0 | Image creation, drawing, font rendering (HUD, frame generation) |
| requests | >=2.32.0 | HTTP client for ISS API |
| python-dotenv | >=1.0.0 | Load `.env` configuration |
| cartopy | >=0.22.0 | Geographic projections (orthographic globe) |
| matplotlib | >=3.8.0 | Rendering backend for Cartopy |
| numpy | >=1.26.0 | RGB565 conversion, frame cache storage |
| spidev | >=3.6 | SPI communication (hardware only) |
| rpi-lgpio | >=0.6 | Raspberry Pi GPIO control (hardware only) |

### Dev only

| Package | Purpose |
|---------|---------|
| pytest | >=8.0.0 | Testing |
