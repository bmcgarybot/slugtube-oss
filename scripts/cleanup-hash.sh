#!/bin/bash
# ============================================================
# SlugTube — Auto-cleanup # folders
# Runs on a tight cron to catch # folders as they appear.
# Merges ChannelName# → ChannelName or renames if no base.
# ============================================================

SHOWS_DIR="${SHOWS_DIR:-/shows}"
HASH_COUNT=0

for hdir in "$SHOWS_DIR"/*\#; do
    [ -d "$hdir" ] || continue
    # Skip #playlists folders
    [[ "$(basename "$hdir")" == *"#playlists"* ]] && continue

    BASE_DIR="${hdir%#}"
    BASE_NAME="$(basename "$BASE_DIR")"
    HASH_NAME="$(basename "$hdir")"

    if [ -d "$BASE_DIR" ]; then
        echo "🔧 Merging: $HASH_NAME → $BASE_NAME"
        find "$hdir" -type f | while IFS= read -r src; do
            rel="${src#$hdir/}"
            dest="$BASE_DIR/$rel"
            dest_dir="$(dirname "$dest")"
            mkdir -p "$dest_dir"
            if [ ! -f "$dest" ]; then
                mv "$src" "$dest" 2>/dev/null && echo "   ✅ $rel"
            else
                echo "   ⏭️ exists: $rel"
            fi
        done
        # Remove empty dirs only — collided files are never deleted
        find "$hdir" -depth -type d -exec rmdir {} \; 2>/dev/null
        if [ -d "$hdir" ]; then
            echo "   ⚠️  Left $HASH_NAME/ in place — some files collided with existing ones (nothing deleted)"
        else
            echo "   🗑️ Removed $HASH_NAME/"
        fi
        ((HASH_COUNT++)) || true
    else
        echo "🔧 Renaming: $HASH_NAME → $BASE_NAME"
        mv "$hdir" "$BASE_DIR" 2>/dev/null && echo "   ✅ Renamed"
        ((HASH_COUNT++)) || true
    fi
done

[ "$HASH_COUNT" -gt 0 ] && echo "🔧 Cleaned $HASH_COUNT hash-folders" || true
