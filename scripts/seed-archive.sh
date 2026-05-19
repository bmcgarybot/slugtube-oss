#!/bin/bash
# ============================================================
# SlugTube Archive Seeder
# Scans existing library and pre-populates the archive file
# so yt-dlp never re-downloads existing videos
# ============================================================

set +e

ARCHIVE_FILE="/config/archive/downloaded.txt"
SHOWS_DIR="/shows"
TEMP_IDS="/tmp/seed_ids.txt"

echo "🌱 Seeding archive from existing library..."
echo "   Scanning: $SHOWS_DIR"

> "$TEMP_IDS"

# ----- Method 1: Extract video IDs from .info.json files -----
JSON_COUNT=0
while IFS= read -r -d '' jsonfile; do
    # Extract video ID from the JSON
    VID_ID=$(jq -r '.id // empty' "$jsonfile" 2>/dev/null)
    if [ -n "$VID_ID" ]; then
        echo "youtube $VID_ID" >> "$TEMP_IDS"
        ((JSON_COUNT++)) || true
    fi
done < <(find "$SHOWS_DIR" -name "*.info.json" -print0 2>/dev/null)

echo "   📄 Found $JSON_COUNT video IDs from .info.json files"

# ----- Method 2: Extract IDs from filenames [VIDEO_ID] pattern -----
FNAME_COUNT=0
while IFS= read -r -d '' vidfile; do
    BASENAME=$(basename "$vidfile")
    # Match [xxxxxxxxxxx] pattern (YouTube IDs are 11 chars)
    if [[ "$BASENAME" =~ \[([a-zA-Z0-9_-]{11})\] ]]; then
        VID_ID="${BASH_REMATCH[1]}"
        echo "youtube $VID_ID" >> "$TEMP_IDS"
        ((FNAME_COUNT++)) || true
    fi
done < <(find "$SHOWS_DIR" \( -name "*.mp4" -o -name "*.mkv" -o -name "*.webm" \) -print0 2>/dev/null)

echo "   🎬 Found $FNAME_COUNT video IDs from filenames"

# ----- Method 3: Extract from PinchFlat-style .json files -----
# PinchFlat JSON files contain "id" field just like yt-dlp .info.json
PF_COUNT=0
while IFS= read -r -d '' jsonfile; do
    # Skip if already counted as .info.json
    [[ "$jsonfile" == *.info.json ]] && continue
    VID_ID=$(jq -r '.id // empty' "$jsonfile" 2>/dev/null)
    if [ -n "$VID_ID" ]; then
        echo "youtube $VID_ID" >> "$TEMP_IDS"
        ((PF_COUNT++)) || true
    fi
done < <(find "$SHOWS_DIR" -name "*.json" -print0 2>/dev/null)

echo "   📋 Found $PF_COUNT video IDs from PinchFlat JSON files"

# ----- Deduplicate and merge with existing archive -----
if [ -f "$ARCHIVE_FILE" ]; then
    cat "$ARCHIVE_FILE" >> "$TEMP_IDS"
fi

sort -u "$TEMP_IDS" > "$ARCHIVE_FILE"
rm -f "$TEMP_IDS"

TOTAL=$(wc -l < "$ARCHIVE_FILE")
echo ""
echo "   ✅ Archive seeded: $TOTAL unique videos tracked"
echo "   📁 File: $ARCHIVE_FILE"
echo "   These will all be SKIPPED during downloads (no re-downloading)"
