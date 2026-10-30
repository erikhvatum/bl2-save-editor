#!/bin/bash
# Double-click in Finder, or run ./start_editor.command from a terminal.
# The editor opens a browser itself and picks a free port.

cd "$(dirname "$0")" || exit 1

if [[ -x .venv/bin/python ]]; then
    PY=".venv/bin/python"
elif python3 -c "import flask" >/dev/null 2>&1; then
    PY="python3"
else
    echo "Flask is not available. From this folder, run:"
    echo "  python3 setup_wizard.py"
    exit 1
fi

echo "Starting BL2 Save Editor..."
echo "Ctrl-C in this window stops it."
echo
exec "$PY" -B app.py
