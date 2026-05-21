#!/bin/bash
# ============================================================
# SlugTube Download Script
# Usage: download.sh --fast   (recent 50 videos per channel)
#        download.sh --full   (entire channel playlist)
#        download.sh --single URL (download one channel, full)
# ============================================================

set -euo pipefail

MODE="${1:---fast}"
SINGLE_URL="${2:-}"
CHANNELS_FILE="/config/channels.txt"
ARCHIVE_FILE="/config/archive/downloaded.txt"
COOKIES_FILE="/config/Cookie/cookies.txt"
CONFIG_FILE="/config/slugtube-config.json"
PAUSE_FILE="/config/paused"
OUTPUT_DIR="/shows"
TEMP_DIR="/config/temp"
LOG_FILE="/config/logs/slugtube.log"

# Ensure dirs exist
mkdir -p "$TEMP_DIR" "$OUTPUT_DIR" /config/logs /config/archive

# ── Lock file (prevent concurrent downloads) ──
LOCK_FILE="/config/slugtube.lock"

cleanup_lock() {
    rm -f "$LOCK_FILE"
}

if [ -f "$LOCK_FILE" ]; then
    LOCK_PID=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
    # Check if the process that created the lock is still running
    if [ -n "$LOCK_PID" ] && kill -0 "$LOCK_PID" 2>/dev/null; then
        echo "🔒 Another download is already running (PID $LOCK_PID). Skipping."
        echo "   Delete /config/slugtube.lock manually if this is stale."
        exit 0
    else
        echo "🔓 Stale lock found (PID $LOCK_PID not running). Cleaning up."
        rm -f "$LOCK_FILE"
    fi
fi

echo $$ > "$LOCK_FILE"
trap cleanup_lock EXIT

# ── Check pause state ──
if [ -f "$PAUSE_FILE" ]; then
    echo "⏸️ Downloads are PAUSED. Skipping. Resume from the web UI."
    exit 0
fi

echo ""
echo "============================================"
echo " 🐌 SlugTube Download — $(date)"
echo " Mode: $MODE"
echo "============================================"

# ── Validate cookies ──
if [ ! -f "$COOKIES_FILE" ] || [ ! -s "$COOKIES_FILE" ]; then
    echo "⚠️  No cookies file found at $COOKIES_FILE"
    echo "   Downloads may fail with 'Sign in to confirm you're not a bot'"
    echo "   Export fresh cookies from your browser and save to C:\\SlugTube\\Cookie\\cookies.txt"
fi

# ── Read config ──
QUALITY="1080p"
FORMAT="mp4"
EMBED_SUBS="true"
EMBED_META="true"
EMBED_THUMB="true"
WRITE_NFO="true"
WRITE_THUMB="true"
WRITE_SUBS="true"
SKIP_SHORTS="true"

if [ -f "$CONFIG_FILE" ] && command -v jq &>/dev/null; then
    QUALITY=$(jq -r '.quality // "1080p"' "$CONFIG_FILE")
    FORMAT=$(jq -r '.format // "mp4"' "$CONFIG_FILE")
    EMBED_SUBS=$(jq -r '.embed_subs // true' "$CONFIG_FILE")
    EMBED_META=$(jq -r '.embed_metadata // true' "$CONFIG_FILE")
    EMBED_THUMB=$(jq -r '.embed_thumbnail // true' "$CONFIG_FILE")
    WRITE_NFO=$(jq -r '.write_nfo // true' "$CONFIG_FILE")
    WRITE_THUMB=$(jq -r '.write_thumbnail // true' "$CONFIG_FILE")
    WRITE_SUBS=$(jq -r '.write_subs_file // true' "$CONFIG_FILE")
    SKIP_SHORTS=$(jq -r '.skip_shorts // true' "$CONFIG_FILE")
    echo "📋 Config loaded: ${QUALITY} / ${FORMAT} / subs=${EMBED_SUBS} / meta=${EMBED_META}"
fi

# ── Resolution mapping ──
case "$QUALITY" in
    720p)  HEIGHT_LIMIT=720 ;;
    1080p) HEIGHT_LIMIT=1080 ;;
    1440p) HEIGHT_LIMIT=1440 ;;
    4k)    HEIGHT_LIMIT=2160 ;;
    best)  HEIGHT_LIMIT=99999 ;;
    *)     HEIGHT_LIMIT=1080 ;;
esac

# ── Single channel mode ──
if ([ "$MODE" = "--single" ] || [ "$MODE" = "--fast-single" ]) && [ -n "$SINGLE_URL" ]; then
    # Debug: show exactly what arguments were received
    echo "[DEBUG] All arguments: $@"
    echo "[DEBUG] \$1=$1  \$2=$2  \$3=${3:-EMPTY}  \$4=${4:-EMPTY}"

    FAST_SINGLE=false
    if [ "$MODE" = "--fast-single" ]; then
        FAST_SINGLE=true
    fi

    # Check for --folder argument
    FOLDER_OVERRIDE=""
    if [ "${3:-}" = "--folder" ] && [ -n "${4:-}" ]; then
        FOLDER_OVERRIDE="${4}"
    fi

    echo ""
    echo "============================================"
    echo " 🐌 SlugTube Single Channel — $(date)"
    echo " URL: $SINGLE_URL"
    echo " Folder: ${FOLDER_OVERRIDE:-NOT SET (will use %(channel)s)}"
    echo "============================================"

    # Build output template — use folder override if set, otherwise %(channel)s
    if [ -n "$FOLDER_OVERRIDE" ]; then
        OUTPUT_TEMPLATE="${OUTPUT_DIR}/${FOLDER_OVERRIDE}/Season %(upload_date>%Y,release_date>%Y,modified_date>%Y)s/s%(upload_date>%Y,release_date>%Y,modified_date>%Y)se%(upload_date,release_date,modified_date)s - %(title).150s [%(id)s].%(ext)s"
    else
        OUTPUT_TEMPLATE="${OUTPUT_DIR}/%(channel)s/Season %(upload_date>%Y,release_date>%Y,modified_date>%Y)s/s%(upload_date>%Y,release_date>%Y,modified_date>%Y)se%(upload_date,release_date,modified_date)s - %(title).150s [%(id)s].%(ext)s"
    fi

    # Build yt-dlp options for single channel (same as full, no playlist-end)
    YT_OPTS=(
        -o "$OUTPUT_TEMPLATE"
        --download-archive "$ARCHIVE_FILE"
        --cookies "$COOKIES_FILE"
        -f "bestvideo[height<=${HEIGHT_LIMIT}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=${HEIGHT_LIMIT}]+bestaudio/best[height<=${HEIGHT_LIMIT}]/best"
        --merge-output-format "$FORMAT"
        -P "temp:${TEMP_DIR}"
        --sleep-interval 5
        --max-sleep-interval 15
        --sleep-requests 2
        --no-abort-on-error
        --ignore-errors
        --retries 3
        --retry-sleep 30
        --no-overwrites
        --windows-filenames
    )

    if [ "$EMBED_SUBS" = "true" ]; then
        YT_OPTS+=(--write-subs --write-auto-subs --embed-subs --sub-langs "en.*,en" --sub-format "srt/ass/vtt")
    fi
    if [ "$WRITE_SUBS" = "true" ]; then
        YT_OPTS+=(--convert-subs srt)
    fi
    if [ "$EMBED_META" = "true" ]; then
        YT_OPTS+=(--embed-metadata)
    fi
    if [ "$EMBED_THUMB" = "true" ]; then
        YT_OPTS+=(--embed-thumbnail)
    fi
    if [ "$WRITE_THUMB" = "true" ]; then
        YT_OPTS+=(--write-thumbnail --convert-thumbnails jpg)
    fi
    if [ "$WRITE_NFO" = "true" ]; then
        YT_OPTS+=(--write-info-json)
    fi
    if [ "$SKIP_SHORTS" = "true" ]; then
        YT_OPTS+=(--match-filter "duration > 60")
    fi

    # Fast-single mode: only check latest 50 videos
    if [ "$FAST_SINGLE" = "true" ]; then
        YT_OPTS+=(--playlist-end 50 --break-on-existing --break-on-reject)
        echo "⚡ Fast mode: checking latest 50 videos only"
    fi

    echo "📺 Channel: $SINGLE_URL"
    echo "   Started: $(date '+%H:%M:%S')"

    if yt-dlp "${YT_OPTS[@]}" "$SINGLE_URL" 2>&1; then
        echo "   ✅ Done"
    else
        EXIT_CODE=$?
        if [ $EXIT_CODE -eq 101 ]; then
            echo "   ⏭️  No new videos (all in archive)"
        else
            echo "   ❌ Error (exit code: $EXIT_CODE)"
        fi
    fi

    /app/scripts/channel-art.sh 2>/dev/null || true

    echo ""
    echo "============================================"
    echo " 🐌 Single Channel Complete — $(date)"
    echo "============================================"
    rm -rf "${TEMP_DIR:?}"/*
    exit 0
fi

# Check channels file
if [ ! -f "$CHANNELS_FILE" ]; then
    echo "❌ No channels.txt found at $CHANNELS_FILE"
    exit 1
fi

CHANNEL_COUNT=$(grep -cv '^\s*#\|^\s*$' "$CHANNELS_FILE" 2>/dev/null || echo 0)
if [ "$CHANNEL_COUNT" -eq 0 ]; then
    echo "⚠️  channels.txt is empty. Add URLs and try again."
    exit 0
fi

echo "📺 Processing $CHANNEL_COUNT channels..."

# ── Build yt-dlp options ──
YT_OPTS=(
    -o "${OUTPUT_DIR}/%(channel)s/Season %(upload_date>%Y,release_date>%Y,modified_date>%Y)s/s%(upload_date>%Y,release_date>%Y,modified_date>%Y)se%(upload_date,release_date,modified_date)s - %(title).150s [%(id)s].%(ext)s"
    --download-archive "$ARCHIVE_FILE"
    --cookies "$COOKIES_FILE"

    # Format selection (dynamic based on config)
    -f "bestvideo[height<=${HEIGHT_LIMIT}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=${HEIGHT_LIMIT}]+bestaudio/best[height<=${HEIGHT_LIMIT}]/best"
    --merge-output-format "$FORMAT"

    # Temp directory
    -P "temp:${TEMP_DIR}"

    # Rate limiting (aggressive to avoid YouTube bot detection)
    --sleep-interval 5
    --max-sleep-interval 15
    --sleep-requests 2

    # Error handling
    --no-abort-on-error
    --ignore-errors
    --retries 3
    --retry-sleep 30
    --no-overwrites
    --windows-filenames
)

# ── Conditional options based on config ──

# Subtitles
if [ "$EMBED_SUBS" = "true" ]; then
    YT_OPTS+=(--write-subs --write-auto-subs --embed-subs --sub-langs "en.*,en" --sub-format "srt/ass/vtt")
fi
if [ "$WRITE_SUBS" = "true" ]; then
    YT_OPTS+=(--convert-subs srt)
fi

# Metadata
if [ "$EMBED_META" = "true" ]; then
    YT_OPTS+=(--embed-metadata)
fi

# Thumbnails
if [ "$EMBED_THUMB" = "true" ]; then
    YT_OPTS+=(--embed-thumbnail)
fi
if [ "$WRITE_THUMB" = "true" ]; then
    YT_OPTS+=(--write-thumbnail --convert-thumbnails jpg)
fi

# NFO / info.json
if [ "$WRITE_NFO" = "true" ]; then
    YT_OPTS+=(--write-info-json)
fi

# Skip shorts
if [ "$SKIP_SHORTS" = "true" ]; then
    YT_OPTS+=(--match-filter "duration > 60")
fi

# ── Mode-specific options ──
if [ "$MODE" = "--fast" ]; then
    echo "⚡ Fast mode — checking last 50 videos per channel"
    YT_OPTS+=(--playlist-end 50 --break-on-existing --break-on-reject)
elif [ "$MODE" = "--full" ]; then
    echo "📚 Full mode — indexing entire channel playlists"
fi

# ── Process each channel ──
SUCCESS=0
FAILED=0
SKIPPED=0

while IFS= read -r line || [ -n "$line" ]; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${line// /}" ]] && continue

    # Check pause mid-run
    if [ -f "$PAUSE_FILE" ]; then
        echo "⏸️ Pause detected mid-run. Stopping."
        break
    fi

    # Support "URL|FolderName" format for pinned folder names
    if [[ "$line" == *"|"* ]]; then
        CHANNEL_URL="$(echo "${line%%|*}" | xargs)"
        FOLDER_NAME="$(echo "${line#*|}" | xargs)"
    else
        CHANNEL_URL="$(echo "$line" | xargs)"
        FOLDER_NAME=""
    fi

    echo ""
    if [ -n "$FOLDER_NAME" ]; then
        echo "📺 Channel: $CHANNEL_URL → 📁 $FOLDER_NAME"
    else
        echo "📺 Channel: $CHANNEL_URL"
    fi
    echo "   Started: $(date '+%H:%M:%S')"

    # Build output template — use pinned folder name if set, otherwise %(channel)s
    if [ -n "$FOLDER_NAME" ]; then
        CHANNEL_YT_OPTS=("${YT_OPTS[@]}")
        # Replace the -o template (it's always the first element)
        CHANNEL_YT_OPTS[1]="${OUTPUT_DIR}/${FOLDER_NAME}/Season %(upload_date>%Y,release_date>%Y,modified_date>%Y)s/s%(upload_date>%Y,release_date>%Y,modified_date>%Y)se%(upload_date,release_date,modified_date)s - %(title).150s [%(id)s].%(ext)s"
    else
        CHANNEL_YT_OPTS=("${YT_OPTS[@]}")
    fi

    if yt-dlp "${CHANNEL_YT_OPTS[@]}" "$CHANNEL_URL" 2>&1; then
        echo "   ✅ Done"
        ((SUCCESS++)) || true
    else
        EXIT_CODE=$?
        if [ $EXIT_CODE -eq 101 ]; then
            echo "   ⏭️  No new videos (all in archive)"
            ((SKIPPED++)) || true
        else
            echo "   ❌ Error (exit code: $EXIT_CODE)"
            ((FAILED++)) || true
        fi
    fi

done < "$CHANNELS_FILE"

# ── Generate channel artwork ──
/app/scripts/channel-art.sh 2>/dev/null || true

# ── Summary ──
echo ""
echo "============================================"
echo " 🐌 SlugTube Complete — $(date)"
echo " ✅ Success: $SUCCESS  ⏭️ Up-to-date: $SKIPPED  ❌ Failed: $FAILED"
echo " 📋 Archive: $(wc -l < "$ARCHIVE_FILE") videos tracked"
echo "============================================"
echo ""

rm -rf "${TEMP_DIR:?}"/*
