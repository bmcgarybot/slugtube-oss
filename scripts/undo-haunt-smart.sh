#!/bin/bash
# ============================================================
# Smart undo: Separate Haunt Tv Paranormal Survivor Playlist
# from Haunt TV by matching video IDs from the YouTube playlist.
#
# Strategy:
#   1. Fetch video IDs from the playlist (cached after first run)
#   2. Scan all .nfo files in Haunt TV for <uniqueid> tags
#   3. If the YouTube ID matches a playlist video → move ALL
#      sibling files (same base name: .mkv, .srt, .nfo, -thumb.jpg)
#   4. Also match SlugTube-style filenames with [VIDEO_ID]
#
# Usage:
#   undo-haunt-smart.sh --dry-run
#   undo-haunt-smart.sh --go
# ============================================================

SHOWS_DIR="/shows"
SRC="${SHOWS_DIR}/Haunt TV"
DST="${SHOWS_DIR}/Haunt Tv Paranormal Survivor Playlist"
PLAYLIST_URL="https://youtube.com/playlist?list=PLNSWS5qF9_qTsBKRm4KY53_3oSQ1L9y6I"
ID_CACHE="/config/haunt-playlist-ids.txt"
MODE="${1:---dry-run}"

echo "🔄 Smart undo: Haunt Tv Paranormal Survivor Playlist"
echo "   Mode: $MODE"
echo ""

# ── Step 1: Get playlist video IDs ──
if [ -f "$ID_CACHE" ]; then
    echo "   📋 Using cached playlist IDs from ${ID_CACHE}"
    ID_COUNT=$(wc -l < "$ID_CACHE")
    echo "   📊 ${ID_COUNT} video IDs cached"
else
    echo "   🔍 Fetching video IDs from YouTube playlist..."
    echo "   (This may take a minute)"

    if yt-dlp --flat-playlist --print id "$PLAYLIST_URL" > "$ID_CACHE" 2>/dev/null; then
        ID_COUNT=$(wc -l < "$ID_CACHE")
        echo "   ✅ Got ${ID_COUNT} video IDs"
    else
        echo "   ❌ Failed to fetch playlist IDs. Need cookies/network."
        exit 1
    fi
fi

echo ""

# ── Step 2: Load playlist IDs into a lookup set ──
declare -A PLAYLIST_IDS
while IFS= read -r vid; do
    [ -z "$vid" ] && continue
    PLAYLIST_IDS["$vid"]=1
done < "$ID_CACHE"

echo "   📊 ${#PLAYLIST_IDS[@]} unique playlist IDs loaded"
echo ""

# ── Step 3: Scan .nfo files for matching YouTube IDs ──
MOVED_FILES=0
MATCHED_VIDEOS=0
SKIPPED=0

echo "   🔍 Scanning .nfo files in Haunt TV..."
echo ""

while IFS= read -r -d '' nfo_file; do
    # Extract YouTube video ID from <uniqueid type="youtube">...</uniqueid>
    VIDEO_ID=$(grep -oP '<uniqueid[^>]*type="youtube"[^>]*>\K[^<]+' "$nfo_file" 2>/dev/null)

    [ -z "$VIDEO_ID" ] && continue

    # Check if this video ID is in the playlist
    if [ -z "${PLAYLIST_IDS[$VIDEO_ID]+x}" ]; then
        ((SKIPPED++))
        continue
    fi

    ((MATCHED_VIDEOS++))

    # Get the base name without extension
    NFO_BASE="${nfo_file%.nfo}"
    REL_DIR=$(dirname "${nfo_file#${SRC}/}")
    TARGET_DIR="${DST}/${REL_DIR}"

    # Find all sibling files with the same base name
    # PinchFlat pattern: basename.ext, basename-thumb.jpg, basename.en.srt
    while IFS= read -r -d '' sibling; do
        ((MOVED_FILES++))
        SIBLING_NAME=$(basename "$sibling")

        if [ "$MODE" = "--go" ]; then
            mkdir -p "$TARGET_DIR"
            mv "$sibling" "$TARGET_DIR/"
        else
            echo "   [DRY RUN] ${REL_DIR}/${SIBLING_NAME}"
        fi
    done < <(find "$(dirname "$nfo_file")" -maxdepth 1 -type f -name "$(basename "$NFO_BASE")*" -print0)

done < <(find "$SRC" -type f -name "*.nfo" -print0)

echo ""

# ── Step 4: Also check SlugTube-style filenames with [VIDEO_ID] ──
echo "   🔍 Scanning for SlugTube-style [VIDEO_ID] filenames..."

SLUG_MATCHED=0
while IFS= read -r vid; do
    [ -z "$vid" ] && continue

    while IFS= read -r -d '' file; do
        # Skip if already in the playlist folder
        [[ "$file" == "${DST}"* ]] && continue

        ((SLUG_MATCHED++))
        ((MOVED_FILES++))
        REL_PATH="${file#${SRC}/}"
        TARGET_DIR="${DST}/$(dirname "$REL_PATH")"

        if [ "$MODE" = "--go" ]; then
            mkdir -p "$TARGET_DIR"
            mv "$file" "$TARGET_DIR/"
        else
            echo "   [DRY RUN] (slug) ${REL_PATH}"
        fi
    done < <(find "$SRC" -type f -name "*\[${vid}\]*" -print0)

done < "$ID_CACHE"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📊 Summary:"
echo "   Playlist IDs:        ${#PLAYLIST_IDS[@]}"
echo "   Videos matched (nfo): ${MATCHED_VIDEOS}"
echo "   SlugTube matches:     ${SLUG_MATCHED}"
echo "   NFOs skipped:         ${SKIPPED}"
echo "   Total files to move:  ${MOVED_FILES}"
echo ""

if [ "$MODE" = "--go" ]; then
    # Clean up empty Season dirs in Haunt TV
    find "$SRC" -type d -empty -delete 2>/dev/null
    echo "✅ Done! Moved ${MOVED_FILES} files to '${DST##*/}'"
else
    echo "🔍 DRY RUN — no files were moved."
    echo "   Run with --go to execute."
fi
