#!/usr/bin/env bash
pkill -SIGTERM -f "iss_display.app.main" 2>/dev/null \
  && echo "ISS Tracker stopped." \
  || echo "ISS Tracker is not running."
