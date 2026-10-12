#!/usr/bin/env bash
set -eu
APP_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
unset PYTHONHOME
export PYTHONPATH="$APP_DIR/packages"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export LD_LIBRARY_PATH="$APP_DIR/runtime/python/lib:$APP_DIR/lib"
exec "$APP_DIR/runtime/python/bin/python3.12" -s "$APP_DIR/launcher.py" "$@"
