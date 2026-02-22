# CLAUDE.md

Project context for Claude Code.

## What This Is

ISS Tracker Display — a Raspberry Pi app that shows a spinning 3D globe with real-time ISS position on a 3.5" TFT LCD (320x480, ST7796S controller via SPI). Runs 24/7 as a systemd user service.

## Quick Reference

```bash
# Run
iss-display                          # hardware mode
iss-display --preview-only           # generates PNGs in var/previews/

# Service management
systemctl --user restart iss-display # restart after changes
journalctl --user -u iss-display -f  # live logs

# After changing globe colors/scale in theme.toml, delete frame cache:
rm -rf var/frame_cache/
systemctl --user restart iss-display
```

## Key Files

| File | What it does |
|------|-------------|
| `theme.toml` | All visual styling — colors, fonts, sizes, layout. Edit this to change appearance. |
| `.env` | API keys, GPIO pins, SPI config, display dimensions. |
| `src/iss_display/theme.py` | Theme dataclasses + TOML loader. `THEME` singleton loaded at import. |
| `src/iss_display/display/lcd_driver.py` | Main rendering: globe frames, ISS marker, HUD bars, SPI output. |
| `src/iss_display/app/main.py` | Entry point, render loop, orbital interpolator thread, systemd notify. |
| `src/iss_display/config.py` | `Settings` dataclass loaded from `.env`. |
| `src/iss_display/data/iss_client.py` | ISS API client with fallback chain. |
| `deploy/iss-display.service` | systemd service (Type=notify, watchdog, auto-restart). |

## Architecture Patterns

### Theme cascade (3 levels)

HUD text styles resolve: **element > bar > hud base**. `None` = inherit from parent.

```
THEME.hud.label.color                    # base for ALL labels
THEME.hud.top.label.color               # override for top-bar labels
THEME.hud.top.lat.label.color           # override for just LAT label
```

Resolution is done once at init in `_init_hud()` → `_ResolvedElement` objects. Rendering in `_render_hud_bars()` uses these pre-resolved values.

### TOML → Dataclass loading

`_build(cls, data, base)` recursively merges a TOML dict over a base dataclass instance. Missing keys keep the base value. Lists become tuples (for RGB). Nested dicts recurse into child dataclasses. The `base` parameter preserves parent-level defaults (e.g., `cell_width`) when only child keys are set in TOML.

### Frame generation

144 Cartopy orthographic frames, generated with multiprocessing (`mp.Pool`), 110m resolution features, raw canvas buffer (no PNG round-trip). Cached as uncompressed `.npz` in `var/frame_cache/`. Pre-converted to RGB565 bytes at startup.

### Display pipeline (hot path)

```
memcpy RGB565 frame → draw ISS marker (byte-level) → patch HUD bars (memcpy) → SPI write
```

~2-5ms CPU, ~65ms SPI. No PIL/numpy in the hot path. HUD only re-rendered when values change.

## Testing

```bash
pytest                               # runs existing tests
python -c "import sys; sys.path.insert(0,'src'); from iss_display.theme import THEME; print(THEME.hud.label.color)"
```

Limited test coverage — only `geography.py` has tests. LCD pipeline, interpolator, HUD, and theme loading have no automated tests. Visual verification on hardware or via `--preview-only` PNGs.

## Common Tasks

**Change a color**: Edit `theme.toml`, restart service.

**Change globe colors**: Edit `[globe]` in `theme.toml`, delete `var/frame_cache/`, restart service.

**Add a new HUD element**: Add field to `TopBarStyle`/`BottomBarStyle` in `theme.py`, add TOML section, add rendering code in `_render_hud_bars()` in `lcd_driver.py`, add resolution in `_init_hud()`.

**Change API source**: Edit `ISS_API_URL` in `.env`, or modify `iss_client.py` for a new API format.

## Gotchas

- `theme.toml` is loaded at Python import time (module-level `THEME = _load_theme()`). Changes require process restart.
- Globe frame cache (`var/frame_cache/*.npz`) must be deleted when globe colors or scale change — the cache doesn't auto-invalidate on theme changes.
- `from __future__ import annotations` is used in `theme.py` — all type annotations are strings at runtime. `get_type_hints()` resolves them for the TOML loader.
- Frozen dataclasses are used throughout the theme — they can't be mutated after creation.
- The display service uses `Type=notify` — the app must call `sd_notify("READY=1")` or systemd will kill it after the timeout.
- `multiprocessing.Pool` is used for frame generation — the worker is a `@staticmethod` that takes picklable args (plain dict, not dataclasses).
