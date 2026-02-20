#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
source .venv/bin/activate
exec python -m iss_display.app.main "$@"
