#!/bin/bash
# ============================================================
# SlugTube Auto-Detect Folder Names
#
# For each URL in channels.txt, probes YouTube to get the current
# channel display name, finds the matching folder on disk, and
# rewrites channels.txt with pinned folder names: URL|FolderName
#
# This is a ONE-TIME setup script. Run it once, then download.sh
# will always use the pinned names going forward.
#
# Usage: pin-folders.sh [--dry-run|--go]
# ============================================================

CHANNELS_FILE="/config/channels.txt"
SHOWS_DIR="/shows"
MODE="${1:---dry-run}"
BACKUP="/config/channels.txt.bak.$(date +%Y%m%d%H%M%S)"

echo "📌 SlugTube Folder Pinner"
echo "   Mode: $MODE"
echo ""

# Backup channels.txt
if [ "$MODE" = "--go" ]; then
    cp "$CHANNELS_FILE" "$BACKUP"
    echo "   💾 Backup saved: $BACKUP"
    echo ""
fi

# Get list of existing folders on disk
declare -A FOLDER_LOWER_MAP
while IFS= read -r -d '' dir; do
    folder_name=$(basename "$dir")
    # Map lowercase name → actual name for fuzzy matching
    lower=$(echo "$folder_name" | tr '[:upper:]' '[:lower:]' | tr -d ' .')
    FOLDER_LOWER_MAP["$lower"]="$folder_name"
done < <(find "$SHOWS_DIR" -mindepth 1 -maxdepth 1 -type d -print0)

OUTPUT_LINES=()
PINNED=0
UNPINNED=0

while IFS= read -r line || [ -n "$line" ]; do
    # Preserve comments and blank lines
    if [[ "$line" =~ ^[[:space:]]*# ]] || [[ -z "${line// /}" ]]; then
        OUTPUT_LINES+=("$line")
        continue
    fi

    # Skip if already pinned
    if [[ "$line" == *"|"* ]]; then
        OUTPUT_LINES+=("$line")
        echo "   ✅ Already pinned: ${line#*|}"
        ((PINNED++))
        continue
    fi

    URL="$(echo "$line" | xargs)"
    echo "   🔍 Probing: $URL"

    # Get channel name from YouTube (just need 1 video)
    YT_NAME=$(yt-dlp --print channel --playlist-items 1 --no-warnings --quiet "$URL" 2>/dev/null | head -1)

    if [ -z "$YT_NAME" ]; then
        echo "      ⚠️  Could not resolve channel name — keeping URL only"
        OUTPUT_LINES+=("$URL")
        ((UNPINNED++))
        continue
    fi

    echo "      YouTube says: '$YT_NAME'"

    # Try exact match first
    if [ -d "${SHOWS_DIR}/${YT_NAME}" ]; then
        echo "      📁 Exact match: ${YT_NAME}"
        OUTPUT_LINES+=("${URL}|${YT_NAME}")
        ((PINNED++))
        continue
    fi

    # Try fuzzy match (lowercase, no spaces/periods)
    yt_lower=$(echo "$YT_NAME" | tr '[:upper:]' '[:lower:]' | tr -d ' .')
    if [ -n "${FOLDER_LOWER_MAP[$yt_lower]+x}" ]; then
        MATCH="${FOLDER_LOWER_MAP[$yt_lower]}"
        echo "      📁 Fuzzy match: ${MATCH}"
        OUTPUT_LINES+=("${URL}|${MATCH}")
        ((PINNED++))
        continue
    fi

    # Try partial match — folder name contains channel name or vice versa
    PARTIAL_MATCH=""
    while IFS= read -r -d '' dir; do
        folder_name=$(basename "$dir")
        folder_lower=$(echo "$folder_name" | tr '[:upper:]' '[:lower:]')
        yt_lower_spaced=$(echo "$YT_NAME" | tr '[:upper:]' '[:lower:]')
        if [[ "$folder_lower" == *"$yt_lower_spaced"* ]] || [[ "$yt_lower_spaced" == *"$folder_lower"* ]]; then
            # Pick the folder with the most files
            count=$(find "$dir" -type f -name "*.mp4" -o -name "*.mkv" -o -name "*.webm" 2>/dev/null | wc -l)
            if [ "$count" -gt 0 ]; then
                PARTIAL_MATCH="$folder_name"
                break
            fi
        fi
    done < <(find "$SHOWS_DIR" -mindepth 1 -maxdepth 1 -type d -print0)

    if [ -n "$PARTIAL_MATCH" ]; then
        echo "      📁 Partial match: ${PARTIAL_MATCH}"
        OUTPUT_LINES+=("${URL}|${PARTIAL_MATCH}")
        ((PINNED++))
    else
        # No match — use YouTube name (new channel, will create folder)
        echo "      📁 No existing folder — will use: ${YT_NAME}"
        OUTPUT_LINES+=("${URL}|${YT_NAME}")
        ((PINNED++))
    fi

done < "$CHANNELS_FILE"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📊 Results: ${PINNED} pinned, ${UNPINNED} could not resolve"

if [ "$MODE" = "--go" ]; then
    # Write new channels.txt
    printf '%s\n' "${OUTPUT_LINES[@]}" > "$CHANNELS_FILE"
    echo "✅ channels.txt updated!"
    echo "   Backup at: $BACKUP"
else
    echo ""
    echo "🔍 DRY RUN — here's what channels.txt would look like:"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    printf '%s\n' "${OUTPUT_LINES[@]}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "Run with --go to save."
fi
