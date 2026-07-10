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

# ── Log rotation: keep the download log from growing unbounded ──
# (a multi-MB log made every dashboard load grep the whole file)
LOG_FILE_PATH="/config/logs/download.log"
if [ -f "$LOG_FILE_PATH" ] && [ "$(stat -c%s "$LOG_FILE_PATH" 2>/dev/null || echo 0)" -gt 8388608 ]; then
    tail -c 2097152 "$LOG_FILE_PATH" > "${LOG_FILE_PATH}.tmp" && mv "${LOG_FILE_PATH}.tmp" "$LOG_FILE_PATH"
    echo "🧹 Rotated download log (kept last 2MB)"
fi

# ── Fast-check helper (used by --fast and --fast-single) ──
# Fast mode uses a two-phase check instead of --break-on-existing:
#   Phase 1: one flat-playlist metadata call lists the last N video IDs (~2s)
#   Phase 2: diff those IDs against the archive LOCALLY; download only missing
# --break-on-existing stopped at the FIRST archived video, so it usually
# checked just 1 video per channel and silently missed out-of-order uploads,
# unhidden members videos, and anything that wasn't the very latest upload.
# This checks all N, every run, and is faster in the common case.
FAST_CHECK_DEPTH="${FAST_CHECK_DEPTH:-50}"

fast_check_channel() {
    # $1 = channel URL, $2 = name of the yt-dlp options array to use
    # Prints status lines; returns: 0 = downloaded new videos OK,
    # 10 = up to date, 20 = listing failed, other = download error
    local url="$1"
    local -n _opts=$2

    local ids
    ids=$(yt-dlp --flat-playlist --playlist-end "$FAST_CHECK_DEPTH" \
                 --print id --no-warnings --ignore-errors \
                 --cookies "$COOKIES_FILE" \
                 --sleep-requests 2 \
                 "$url" 2>/dev/null | grep -E '^[A-Za-z0-9_-]{11}$' || true)

    if [ -z "$ids" ]; then
        echo "   ⚠️  Could not list recent videos for fast check"
        return 20
    fi

    local checked
    checked=$(printf '%s\n' "$ids" | sed '/^$/d' | wc -l)

    # Archive lines are "youtube <id>" — extract IDs and diff locally
    local new_ids
    new_ids=$(printf '%s\n' "$ids" | grep -vxFf <(awk '$1=="youtube"{print $2}' "$ARCHIVE_FILE" 2>/dev/null) || true)

    if [ -z "$new_ids" ]; then
        echo "   ⏭️  Up to date — checked $checked recent videos, all archived"
        return 10
    fi

    local new_count
    new_count=$(printf '%s\n' "$new_ids" | sed '/^$/d' | wc -l)
    echo "   🆕 $new_count new of $checked checked — downloading"

    local urls=()
    while IFS= read -r vid; do
        [ -n "$vid" ] && urls+=("https://www.youtube.com/watch?v=${vid}")
    done <<< "$new_ids"

    yt-dlp "${_opts[@]}" "${urls[@]}"
}


PAUSE_FILE="/config/paused"
OUTPUT_DIR="/shows"
TEMP_DIR="/config/temp"
LOG_FILE="/config/logs/slugtube.log"

# Ensure dirs exist
mkdir -p "$TEMP_DIR" "$OUTPUT_DIR" /config/logs /config/archive

# ── Lock file (prevent concurrent downloads) ──
LOCK_FILE="/config/slugtube.lock"
QUEUE_DIR="/config/queue"
mkdir -p "$QUEUE_DIR"

cleanup_lock() {
    rm -f "$LOCK_FILE"
}

process_queue() {
    # After finishing, check for queued jobs and run the next one
    local next_job
    next_job=$(ls -1t "$QUEUE_DIR"/*.job 2>/dev/null | head -1)
    if [ -n "$next_job" ] && [ -f "$next_job" ]; then
        local job_args
        job_args=$(cat "$next_job")
        rm -f "$next_job"
        echo ""
        echo "📋 Running queued job: $job_args"
        echo ""
        # Release lock, then re-run ourselves with the queued args
        # Use eval to restore quoted arguments (spaces in folder names)
        rm -f "$LOCK_FILE"
        eval exec /app/scripts/download.sh "$job_args"
    fi
}

if [ -f "$LOCK_FILE" ]; then
    LOCK_PID=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
    # Check if the process that created the lock is still running
    if [ -n "$LOCK_PID" ] && kill -0 "$LOCK_PID" 2>/dev/null; then
        # Queue this job instead of skipping
        JOB_KEY="$MODE"
        [ -n "$SINGLE_URL" ] && JOB_KEY="${MODE}_${SINGLE_URL}"
        QUEUE_FILE="$QUEUE_DIR/$(date +%s)_$(echo "$JOB_KEY" | tr '/:@' '___').job"
        # Don't queue duplicates of the same mode (fast/full)
        if [ "$MODE" = "--fast" ] || [ "$MODE" = "--full" ]; then
            if ls "$QUEUE_DIR"/*"${MODE}"*.job 2>/dev/null | grep -q .; then
                echo "🔒 Download running (PID $LOCK_PID). Already queued: $MODE. Skipping duplicate."
                exit 0
            fi
        fi
        # Quote each argument so spaces in folder names survive the queue round-trip
        printf '%q ' "$@" > "$QUEUE_FILE"
        echo "🔒 Download running (PID $LOCK_PID). Queued: $MODE → will run when current job finishes."
        exit 0
    else
        echo "🔓 Stale lock found (PID $LOCK_PID not running). Cleaning up."
        rm -f "$LOCK_FILE"
    fi
fi

echo $$ > "$LOCK_FILE"
trap 'process_queue; cleanup_lock' EXIT

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
    [ "${SLUGTUBE_DEBUG:-0}" = "1" ] && echo "[DEBUG] All arguments: $@"
    [ "${SLUGTUBE_DEBUG:-0}" = "1" ] && echo "[DEBUG] \$1=$1  \$2=$2  \$3=${3:-EMPTY}  \$4=${4:-EMPTY}"

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
    --no-progress
        -o "$OUTPUT_TEMPLATE"
        --replace-in-metadata "channel" "#" ""
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
        --trim-filenames 200
        --no-write-playlist-metafiles
        --exec "after_move:curl -sf -X POST http://localhost:5000/api/index-video -d file={} || true"
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

    # Fast-single mode: flat-list the latest videos, diff against archive,
    # download only what's missing (checks all of them, not just until the
    # first archived hit)
    if [ "$FAST_SINGLE" = "true" ]; then
        echo "⚡ Fast mode: checking latest ${FAST_CHECK_DEPTH} videos (flat-list diff)"
    fi

    echo "📺 Channel: $SINGLE_URL"
    echo "   Started: $(date '+%H:%M:%S')"

    if [ "$FAST_SINGLE" = "true" ]; then
        fast_check_channel "$SINGLE_URL" YT_OPTS
        RC=$?
        if [ $RC -eq 0 ]; then
            echo "   ✅ Done"
        elif [ $RC -ne 10 ]; then
            echo "   ❌ Error (exit code: $RC)"
        fi
    elif yt-dlp "${YT_OPTS[@]}" "$SINGLE_URL" 2>&1; then
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
# Use %(channel)s but strip trailing # via --replace-in-metadata
YT_OPTS=(
    --no-progress
    -o "${OUTPUT_DIR}/%(channel)s/Season %(upload_date>%Y,release_date>%Y,modified_date>%Y)s/s%(upload_date>%Y,release_date>%Y,modified_date>%Y)se%(upload_date,release_date,modified_date)s - %(title).150s [%(id)s].%(ext)s"
    --replace-in-metadata "channel" "#" ""
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
    --trim-filenames 200
    --no-write-playlist-metafiles

    # Live-index: notify Flask to index each video as soon as it's downloaded
    --exec "after_move:curl -sf -X POST http://localhost:5000/api/index-video -d file={} || true"
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
    echo "⚡ Fast mode — checking last ${FAST_CHECK_DEPTH} videos per channel (flat-list diff)"
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

    # Skip if URL is empty after parsing (malformed entry like lone "|" or whitespace)
    if [ -z "$CHANNEL_URL" ]; then
        continue
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
        # Replace the -o template value. YT_OPTS is (--no-progress, -o, <template>, ...),
        # so the template lives at index 2.
        CHANNEL_YT_OPTS[2]="${OUTPUT_DIR}/${FOLDER_NAME}/Season %(upload_date>%Y,release_date>%Y,modified_date>%Y)s/s%(upload_date>%Y,release_date>%Y,modified_date>%Y)se%(upload_date,release_date,modified_date)s - %(title).150s [%(id)s].%(ext)s"
    else
        CHANNEL_YT_OPTS=("${YT_OPTS[@]}")
    fi

    if [ "$MODE" = "--fast" ]; then
        fast_check_channel "$CHANNEL_URL" CHANNEL_YT_OPTS
        RC=$?
        if [ $RC -eq 0 ]; then
            echo "   ✅ Done"
            ((SUCCESS++)) || true
        elif [ $RC -eq 10 ]; then
            ((SKIPPED++)) || true
        else
            echo "   ❌ Error (exit code: $RC)"
            ((FAILED++)) || true
        fi
    elif yt-dlp "${CHANNEL_YT_OPTS[@]}" "$CHANNEL_URL" 2>&1; then
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

# ── Post-download: merge any # folders ──
/app/scripts/cleanup-hash.sh 2>/dev/null || true

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
