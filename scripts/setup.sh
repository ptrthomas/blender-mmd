#!/usr/bin/env bash
# Symlink blender_mmd addon into Blender's user extensions directory.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADDON_DIR="$SCRIPT_DIR/../blender_mmd"

# Blender 5.0+ extensions directory (macOS)
TARGET_DIR="$HOME/Library/Application Support/Blender/5.0/extensions/user_default"

mkdir -p "$TARGET_DIR"

LINK="$TARGET_DIR/blender_mmd"
if [ -L "$LINK" ]; then
    echo "Removing existing symlink: $LINK"
    rm "$LINK"
elif [ -e "$LINK" ]; then
    echo "Error: $LINK exists and is not a symlink. Remove it manually."
    exit 1
fi

ln -sf "$ADDON_DIR" "$LINK"
echo "Symlinked: $LINK -> $ADDON_DIR"
echo "Restart Blender to load the addon."
