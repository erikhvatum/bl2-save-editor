#!/bin/bash
# Double-click in Finder, or run ./extract_assets.command from a terminal.
# Exports the 3D viewer meshes and textures from a Mac Borderlands 2 install.
# The game install is only read. Extracts stay in gitignored static/models
# and static/textures. Pass "check" to report what is already missing.

cd "$(dirname "$0")" || exit 1

if [[ -x /usr/bin/python3 ]]; then
    PY="/usr/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
else
    echo "python3 is not installed."
    exit 1
fi

echo "Extracting Borderlands 2 viewer assets..."
echo "This reads the game install and does not modify it."
echo "Ctrl-C stops the extract. Re-run the same command to resume."
echo

exec "$PY" -B tools/extract_viewer_assets.py "$@"
