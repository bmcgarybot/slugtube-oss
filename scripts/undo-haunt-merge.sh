#!/bin/bash
# ============================================================
# Undo the Haunt Tv Paranormal Survivor Playlist merge
# Moves Season 2022-2025 files from "Haunt TV" back to the playlist folder
# ============================================================

SHOWS_DIR="/shows"
SRC="${SHOWS_DIR}/Haunt TV"
DST="${SHOWS_DIR}/Haunt Tv Paranormal Survivor Playlist"
MODE="${1:---dry-run}"

echo "🔄 Undoing Haunt Tv Paranormal Survivor Playlist merge"
echo "   Mode: $MODE"
echo ""

MOVED=0

for SEASON in "Season 2022" "Season 2023" "Season 2024" "Season 2025"; do
    SEASON_SRC="${SRC}/${SEASON}"

    if [ ! -d "$SEASON_SRC" ]; then
        echo "   ℹ️  ${SEASON} not found in Haunt TV, skipping"
        continue
    fi

    FILE_COUNT=$(find "$SEASON_SRC" -type f | wc -l)
    echo "   📁 ${SEASON}: ${FILE_COUNT} files"

    if [ "$MODE" = "--go" ]; then
        mkdir -p "${DST}/${SEASON}"
        while IFS= read -r -d '' file; do
            filename=$(basename "$file")
            mv "$file" "${DST}/${SEASON}/" && ((MOVED++))
        done < <(find "$SEASON_SRC" -type f -print0)

        # Remove now-empty season folder from Haunt TV
        rmdir "$SEASON_SRC" 2>/dev/null && echo "   🗑️  Removed empty ${SEASON} from Haunt TV" || true
    else
        echo "   [DRY RUN] Would move ${FILE_COUNT} files → '${DST}/${SEASON}/'"
        MOVED=$((MOVED + FILE_COUNT))
    fi
done

echo ""
if [ "$MODE" = "--go" ]; then
    echo "✅ Done! Moved ${MOVED} files back to 'Haunt Tv Paranormal Survivor Playlist'"
else
    echo "🔍 DRY RUN: Would move ~${MOVED} files"
    echo "   Run with --go to execute."
fi
