#!/usr/bin/env sh
# Start Chezz on Linux/macOS. First run creates .venv, installs dependencies and builds the UI.
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    PY=""
    for cand in python3.13 python3.12 python3.11 python3; do
        if command -v "$cand" >/dev/null 2>&1 &&
           "$cand" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
            PY="$cand"; break
        fi
    done
    if [ -z "$PY" ]; then
        echo "Chezz needs Python 3.11 or newer (found: $(python3 --version 2>&1 || echo none))." >&2
        exit 1
    fi
    echo "Creating virtual environment with $PY..."
    # --system-site-packages lets pywebview use the distro's GTK bindings (python3-gi) for the native window.
    if ! "$PY" -m venv --system-site-packages .venv; then
        rm -rf .venv
        echo "Could not create a virtual environment. On Debian/Ubuntu: sudo apt install python3-venv" >&2
        exit 1
    fi
fi

# Reinstall dependencies only when pyproject.toml changed (keeps startup offline).
if ! cmp -s pyproject.toml .venv/pyproject.installed; then
    echo "Installing Python dependencies..."
    .venv/bin/python -m pip install --disable-pip-version-check -q -e .
    cp pyproject.toml .venv/pyproject.installed
fi

if [ ! -f web/dist/index.html ] && ! command -v npm >/dev/null 2>&1; then
    echo "The UI is not built yet and npm was not found. Install Node.js 18+ (it includes npm) and run again." >&2
    exit 1
fi

exec .venv/bin/python -m chezz "$@"
