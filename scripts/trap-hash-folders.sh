#!/bin/bash
# ============================================================
# SlugTube — Pre-create symlinks to trap # folders
#
# For every pinned channel in channels.txt, creates:
#   /shows/ChannelName# → /shows/ChannelName (symlink)
#
# Result: when yt-dlp tries to write to "ChannelName#/...",
# the filesystem silently redirects to "ChannelName/...".
# No cleanup needed. No race conditions. No # folders. Ever.
#
# Also handles any EXISTING # folders by merging them first.
# ============================================================

SHOWS_DIR="${SHOWS_DIR:-/shows}"
CHANNELS_FILE="${CHANNELS_FILE:-/config/channels.txt}"
CREATED=0
MERGED=0

# ── Step 1: Merge any existing real # directories first ──
for hdir in "$SHOWS_DIR"/*\#; do
    [ -d "$hdir" ] || continue
    [ -L "$hdir" ] && continue          # already a symlink — skip
    [[ "$(basename "$hdir")" == *"#playlists"* ]] && continue

    BASE_DIR="${hdir%#}"
    HASH_NAME="$(basename "$hdir")"
    BASE_NAME="$(basename "$BASE_DIR")"

    # Ensure base folder exists
    mkdir -p "$BASE_DIR"

    echo "🔧 Merging existing: $HASH_NAME → $BASE_NAME"
    find "$hdir" -type f | while IFS= read -r src; do
        rel="${src#$hdir/}"
        dest="$BASE_DIR/$rel"
        mkdir -p "$(dirname "$dest")"
        if [ ! -f "$dest" ]; then
            mv "$src" "$dest" 2>/dev/null && echo "   ✅ $rel"
        fi
    done
    rm -rf "$hdir" 2>/dev/null
    ((MERGED++)) || true
done

[ "$MERGED" -gt 0 ] && echo "🔧 Merged $MERGED existing # folders"

# ── Step 2: Create symlinks for every pinned channel ──
if [ ! -f "$CHANNELS_FILE" ]; then
    echo "⚠️  No channels.txt found"
    exit 0
fi

while IFS= read -r line || [ -n "$line" ]; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${line// /}" ]] && continue

    # Only process pinned channels (URL|FolderName)
    [[ "$line" != *"|"* ]] && continue

    FOLDER_NAME="$(echo "${line#*|}" | xargs)"
    [ -z "$FOLDER_NAME" ] && continue

    HASH_PATH="$SHOWS_DIR/${FOLDER_NAME}#"

    # Skip if symlink already exists and points to the right place
    if [ -L "$HASH_PATH" ]; then
        continue
    fi

    # If a real # directory somehow appeared again, merge first
    if [ -d "$HASH_PATH" ]; then
        mkdir -p "$SHOWS_DIR/$FOLDER_NAME"
        find "$HASH_PATH" -type f | while IFS= read -r src; do
            rel="${src#$HASH_PATH/}"
            dest="$SHOWS_DIR/$FOLDER_NAME/$rel"
            mkdir -p "$(dirname "$dest")"
            [ ! -f "$dest" ] && mv "$src" "$dest" 2>/dev/null
        done
        rm -rf "$HASH_PATH" 2>/dev/null
    fi

    # Create the trap symlink
    # Ensure the base folder exists first
    mkdir -p "$SHOWS_DIR/$FOLDER_NAME"
    ln -sf "$SHOWS_DIR/$FOLDER_NAME" "$HASH_PATH"
    ((CREATED++)) || true

done < "$CHANNELS_FILE"

[ "$CREATED" -gt 0 ] && echo "🔗 Created $CREATED symlink traps"
echo "🛡️ All # folder traps in place"
