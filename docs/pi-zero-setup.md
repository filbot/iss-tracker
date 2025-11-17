# Raspberry Pi Zero Setup Guide

This guide walks through deploying the ISS display entirely on a Raspberry Pi Zero running Raspberry Pi OS Trixie (32-bit). It assumes a fresh install, no access to a faster Pi for building wheels, and minimal prior experience. Follow each step in order.

## 1. Prepare the Pi Zero

1. Boot the Pi, connect to the network, and run all updates:

   ```bash
   sudo apt update
   sudo apt full-upgrade -y
   sudo reboot
   ```

2. After the reboot, enable SPI (required for the e-paper HAT):

   ```bash
   sudo raspi-config nonint do_spi 0
   sudo reboot
   ```

3. Install the base packages (git, Python tooling, BLAS/LAPACK + Pillow runtime libs, and system services). This command installs everything `make deploy` expects so the later steps do not prompt for missing dependencies:

   ```bash
   sudo apt install -y \
     git python3 python3-venv python3-pip python3-dev build-essential pkg-config \
       libssl-dev libcurl4-openssl-dev \
       libjpeg-dev zlib1g-dev libopenjp2-7 libopenjp2-7-dev libtiff-dev libfreetype6-dev \
       liblcms2-dev libwebp-dev libharfbuzz-dev libfribidi-dev libxcb1 gettext-base \
       libopenblas0 libopenblas-dev liblapack-dev
   ```

## 2. Clone the Repository

```bash
cd /home/${USER}
git clone https://github.com/your-user/iss-tracker.git
cd iss-tracker
```

> Replace `your-user` with the actual GitHub owner (or use your fork). The project auto-detects the current user, so no manual edits to the systemd unit are needed.

## 3. Configure Environment Variables

1. Copy the sample env file and edit it with your Mapbox token and any overrides you need:

   ```bash
   cp .env.example .env
   nano .env
   ```

2. At minimum set `MAPBOX_TOKEN`. Tweak other options (LEDs, cache paths) as desired.

## 4. Create the Virtual Environment (Pi Wheels Friendly)

The Pi Zero can avoid most local compilation by pulling wheels from [piwheels.org](https://www.piwheels.org/).

```bash
python3 -m venv .venv
source .venv/bin/activate
PIP_INDEX_URL=https://www.piwheels.org/simple/ pip install --upgrade pip
PIP_INDEX_URL=https://www.piwheels.org/simple/ pip install -r requirements.txt
deactivate
```

> Using `PIP_INDEX_URL` here means `pip` downloads prebuilt armv6 wheels whenever available. The process still takes a few minutes on a Zero but avoids the multi-hour compile times.

## 5. Deploy via Makefile

The Makefile handles venv validation, dependency installs, systemd unit rendering, and service enablement. Because you already cloned the repo locally, `make deploy` will only pull updates, reuse the venv, and install the service.

```bash
cd /home/${USER}/iss-tracker
make deploy
```

What this does:
- Installs any missing apt packages (idempotent thanks to the earlier step).
- Verifies the repo is present and up to date.
- Ensures `.venv` exists (no-op since you already built it).
- Copies `.env.example` to `.env` if you didn’t already.
- Renders `systemd/iss-display.service` with your current username, installs it to `/etc/systemd/system`, reloads systemd, and enables the service to start immediately.

## 6. Verify the Service

1. Check that the service is active:

   ```bash
   make status
   ```

2. Watch the logs for a minute to confirm ISS/API calls, image downloads, and display refreshes:

   ```bash
   make journal
   ```

   You should see lines like `Refreshing display frame` followed by Mapbox/ISS client messages. Press `Ctrl+C` to exit the log tail.

3. If you need to restart after editing `.env`, run:

   ```bash
   make restart
   ```

## 7. Common Gotchas (and fixes)

- **Missing Mapbox token**: the app exits with a runtime error. Double-check `.env` contains `MAPBOX_TOKEN=...`.
- **SPI not enabled**: you’ll see hardware driver errors. Re-run `sudo raspi-config` and enable SPI, then reboot.
- **Low disk space**: `pip` may fail with `No space left on device`. Clear `/var/cache/apt` (`sudo apt clean`) or expand the filesystem via Raspberry Pi Imager.
- **Networking slow or offline**: Mapbox/ISS fetches will retry; check connectivity if logs show repeated failures.

## 8. Updating Later

Whenever you pull new code:

```bash
cd /home/${USER}/iss-tracker
git pull
source .venv/bin/activate
PIP_INDEX_URL=https://www.piwheels.org/simple/ pip install -r requirements.txt
deactivate
make restart
```

This keeps dependencies aligned and restarts the daemon with the latest code.

---

You now have the ISS display running purely from a Pi Zero, with all services managed by systemd. Keep an eye on `make journal` for ongoing health, and tweak `.env` whenever you need to adjust behavior.
