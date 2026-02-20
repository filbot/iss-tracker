# ISS Tracker - Architecture

## Overview

A Raspberry Pi 3B application that renders a continuously spinning 3D globe with real-time ISS position tracking on a 3.5" TFT LCD (320x480, ST7796S controller). The ISS marker stays locked to its geographic coordinates as the globe rotates, disappearing behind the Earth and reappearing with occlusion effects.

**Target hardware:** Raspberry Pi 3 Model B Rev 1.2, 3.5" IPS LCD (320x480), SPI interface
**Runtime:** Designed for 24/7 continuous operation
**API:** wheretheiss.at (primary), open-notify.org (fallback), polled every 30 seconds

---

## Architecture

```
main() → run_loop()
  ├─ ISSClient              Fetches ISS position from API
  ├─ ISSOrbitInterpolator    Background thread: fetches every 30s, interpolates between
  ├─ LcdDisplay              Renders globe + ISS marker + HUD
  │   ├─ Cartopy frames     72 pre-rendered orthographic globe frames (cached as NPZ)
  │   ├─ ISS marker         Drawn into RGB565 byte buffer with occlusion
  │   └─ HUD overlay        Cached amber telemetry bars (top: LAT/LON, bottom: ALT/VEL)
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
7. On shutdown: stop interpolator, close display (backlight off, screen clear, sleep mode)

---

## Source Files

| File | Lines | Purpose |
|------|-------|---------|
| `src/iss_display/app/main.py` | 263 | Entry point, render loop, `ISSOrbitInterpolator` |
| `src/iss_display/display/lcd_driver.py` | 718 | `ST7796S` SPI driver, `LcdDisplay` (globe + ISS + HUD rendering) |
| `src/iss_display/data/iss_client.py` | 92 | `ISSClient` with API fallback chain, `ISSFix` dataclass |
| `src/iss_display/data/geography.py` | 63 | Bounding-box continent/ocean lookup (not currently used) |
| `src/iss_display/config.py` | 58 | `Settings` frozen dataclass, loads from environment variables |

### Tests

| File | Tests |
|------|-------|
| `tests/test_geography.py` | `geography.get_common_area_name()` |

No tests exist for the LCD pipeline, interpolator, HUD, or ST7796S driver.

### Deployment

| File | Purpose |
|------|---------|
| `deploy/iss-display.service` | systemd user service (`Type=simple`, `Restart=always`) |
| `start.sh` | Start the app manually: `./start.sh` (or `./start.sh --preview-only`) |
| `stop.sh` | Stop a running instance: `./stop.sh` (sends SIGTERM) |

---

## Rendering Pipeline

### Per-Frame Pipeline (Optimized)

```
bytearray(frame_bytes_cache[n])     # ~1 ms  — copy pre-computed RGB565 bytes
  → draw ISS marker into byte buffer  # ~1 ms  — direct RGB565 byte writes (~15x15 px)
  → patch HUD bars into byte buffer   # <1 ms  — memcpy of cached RGB565 bytes
  → SPI write                         # ~65 ms — 307 KB at 40 MHz (hard bottleneck)
```

Total CPU work per frame: ~2-5 ms. SPI transfer dominates at ~65 ms.

### Globe (Cartopy)

- 72 frames pre-rendered at startup using Cartopy orthographic projection
- Each frame is 5° apart in longitude (-180° to +175°)
- Cached as `var/frame_cache/cartopy_frames_v2.npz` (~1.3 MB compressed)
- On first run: generates all frames (slow). Subsequent runs: loads from NPZ (fast)
- All frames pre-converted to RGB565 bytes at startup (`frame_bytes_cache`)
- Globe occupies 70% of display area, centered
- Colors: ocean (#001133), land (#FFFFFF), coastlines (#888888), grid (#444444), background (#050510)

### ISS Marker

- Drawn directly into the RGB565 byte buffer (no PIL operations in hot path)
- Plotted at correct geographic lat/lon relative to each frame's `central_lon`
- Uses orthographic projection math to map lat/lon to pixel coordinates
- Exaggerated altitude: ISS rendered at 1.10x Earth radius for visibility
- Occlusion: fades and disappears when behind Earth using geometric horizon calculation
- Visual: 3 concentric red glow rings + red core + white center dot
- Size and opacity scale with visibility angle

### HUD Overlay

- **Top bar** (48px): LAT, LON, green ISS indicator
- **Bottom bar** (48px): ALT (km), VEL (km/h), calculated ORB/D
- Typography: DejaVuSansMono (20px values, 15px units, 11px labels)
- Colors: NASA amber theme — bright (255,210,0), muted (160,135,30), dim (100,85,20)
- Layout: 8px grid baseline, explicit pixel positioning
- **Cached as RGB565 bytes** — only re-rendered when formatted display values change (every ~30s)

### Display Output

- RGB565 conversion via NumPy (vectorized) for PIL Images (HUD bars, frame pre-computation)
- Direct RGB565 byte writes for ISS marker (no PIL/NumPy in hot path)
- SPI: 40 MHz, `writebytes2()` for binary data
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
- Fallback: `http://api.open-notify.org/iss-now.json` (returns lat, lon only)
- Uses `requests.Session()` for connection pooling
- 5-second timeout per request

### Orbital Interpolation

- Background daemon thread fetches real position every 30 seconds (~2 calls/min)
- **Exponential backoff** on consecutive failures: 30s → 60s → 120s → 300s max, resets on success
- Between fetches, position is linearly interpolated using estimated velocity
- Velocity estimated from consecutive API fixes (degrees/second)
- Default longitude velocity: ~0.065°/s (360° / 92.68-minute orbital period)
- Latitude velocity: estimated from data, updated each fetch
- Thread-safe via `threading.Lock`

### Shutdown

1. Signal handler sets `running = False`
2. Main loop exits
3. `interpolator.stop()` — sets flag, joins thread (2s timeout)
4. `driver.close()` → `ST7796S.close()`:
   - Backlight off
   - Fill black 3x (prevent ghosting)
   - DISPOFF, SLPIN (sleep mode)
   - RST held low
   - SPI close, GPIO cleanup

---

## Known Limitations

1. **Globe rotation stutters at 5° steps** — 72 frames means each step is a 5° jump. The ISS marker tracks correctly with the globe, but the jumps are visible. Increasing frame count (e.g., 144 at 2.5° or 360 at 1°) would smooth this at the cost of more memory and longer startup.

---

## Future Improvements

| Task | Impact |
|------|--------|
| Smooth globe rotation (increase frame count or interpolate between frames) | Visual quality |
| Split `lcd_driver.py` (718 lines) into ST7796S driver, globe renderer, HUD renderer | Maintainability |
| Add tests for current code (interpolator, globe frame selection, HUD) | Reliability |

---

## Performance Profile (RPi 3B, 24/7)

| Metric | Value | Notes |
|--------|-------|-------|
| Memory (steady state) | ~53 MB | 72 PIL frames + 72 RGB565 buffers. 1 GB available. |
| CPU per frame | ~2-5 ms | bytearray copy + ISS marker bytes + HUD patch |
| SPI transfer | ~65 ms/frame | 307 KB at 40 MHz (hard bottleneck) |
| Max FPS (SPI limited) | ~15 FPS | CPU overhead is negligible |
| API calls | 2/min | Well under rate limits |
| Startup (cached) | ~5 seconds | Loading NPZ + pre-computing RGB565 |
| Startup (uncached) | 1-2 minutes | Generating 72 Cartopy frames |
| Memory leaks | None | Fixed-size caches, `deque(maxlen=150)` for frame timing, proper cleanup |

---

## Configuration

All settings loaded from `.env` via `Settings.load()` in `config.py`.

| Variable | Default | Used by |
|----------|---------|---------|
| `ISS_API_URL` | `https://api.wheretheiss.at/v1/satellites/25544` | `ISSClient` |
| `DISPLAY_WIDTH` | `320` | `LcdDisplay`, `ST7796S` |
| `DISPLAY_HEIGHT` | `480` | `LcdDisplay`, `ST7796S` |
| `GPIO_DC` | `22` | `ST7796S` (data/command pin) |
| `GPIO_RST` | `27` | `ST7796S` (reset pin) |
| `GPIO_BL` | `18` | `ST7796S` (backlight pin) |
| `SPI_BUS` | `0` | `ST7796S` |
| `SPI_DEVICE` | `0` | `ST7796S` |
| `SPI_SPEED_HZ` | `40000000` | `ST7796S` (40 MHz max) |
| `PREVIEW_ONLY` | `false` | `LcdDisplay` (hardware vs preview) |
| `ISS_PREVIEW_DIR` | `var/previews` | Preview output path |
| `ISS_LOG_LEVEL` | `INFO` | Logging |

---

## Dependencies

### Required (active)

| Package | Version | Purpose |
|---------|---------|---------|
| Pillow | >=10.0.0 | Image creation, drawing, font rendering (HUD, frame generation) |
| requests | >=2.32.0 | HTTP client for ISS API |
| python-dotenv | >=1.0.0 | Load `.env` configuration |
| cartopy | >=0.22.0 | Geographic projections (orthographic globe) |
| matplotlib | >=3.8.0 | Rendering backend for Cartopy |
| numpy | >=1.26.0 | RGB565 conversion, frame cache storage |
| spidev | >=3.6 | SPI communication (hardware only) |
| RPi.GPIO | >=0.7.1 | GPIO control (hardware only) |

### Dev only

| Package | Purpose |
|---------|---------|
| pytest | >=8.0.0 | Testing |
