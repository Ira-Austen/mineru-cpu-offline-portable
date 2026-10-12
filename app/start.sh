#!/usr/bin/env bash
set -eu
APP_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
unset PYTHONHOME
export PYTHONPATH="$APP_DIR/packages"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export LD_LIBRARY_PATH="$APP_DIR/runtime/python/lib:$APP_DIR/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

# 自动确保便携运行时及 llama-server 的可执行权限（防止解压工具未保留权限）
[ -x "$APP_DIR/runtime/python/bin/python3.12" ] || chmod u+x "$APP_DIR/runtime/python/bin/python3.12" 2>/dev/null || true
[ -x "$APP_DIR/packages/mineru_llama_cpp/bin/llama-server" ] || chmod u+x "$APP_DIR/packages/mineru_llama_cpp/bin/llama-server" 2>/dev/null || true

exec "$APP_DIR/runtime/python/bin/python3.12" -s "$APP_DIR/launcher.py" "$@"
