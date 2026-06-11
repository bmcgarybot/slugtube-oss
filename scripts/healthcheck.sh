#!/bin/bash
# ============================================================
# SlugTube Health Check — Per-Channel Video Integrity Scanner
# ============================================================
# Simple, reliable. Writes status to a plain text status file
# that Flask reads directly. No Python inline, no JSON dance.
#
# Usage:
#   healthcheck.sh --channel "Channel Name" [--quick] [--throttle N]
# ============================================================

set -euo pipefail

SHOWS_DIR="/shows"
HC_DIR="/config/healthcheck"
STATUS_FILE="${HC_DIR}/status.txt"
DB_FILE="${HC_DIR}/checked.json"
LOCK_FILE="/tmp/healthcheck.lock"

QUICK=false
CHANNEL=""
THROTTLE=1

while [[ $# -gt 0 ]]; do
    case "$1" in
        --quick)     QUICK=true; shift ;;
        --channel)   CHANNEL="$2"; shift 2 ;;
        --throttle)  THROTTLE="$2"; shift 2 ;;
        --cancel)    rm -f "$LOCK_FILE"; echo "CANCELLED" > "$STATUS_FILE"; exit 0 ;;
        *)           echo "Unknown arg: $1"; exit 1 ;;
    esac
done

if [ -z "$CHANNEL" ]; then
    echo "❌ --channel is required."
    exit 1
fi

SCAN_DIR="$SHOWS_DIR/$CHANNEL"
if [ ! -d "$SCAN_DIR" ]; then
    echo "ERROR|Channel folder not found: $SCAN_DIR" > "$STATUS_FILE"
    exit 1
fi

# Lock
if [ -f "$LOCK_FILE" ]; then
    LOCK_PID=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
    if [ -n "$LOCK_PID" ] && kill -0 "$LOCK_PID" 2>/dev/null; then
        echo "ERROR|Health check already running (PID $LOCK_PID)" > "$STATUS_FILE"
        exit 1
    fi
    rm -f "$LOCK_FILE"
fi
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

mkdir -p "$HC_DIR"

# Init DB if needed
if [ ! -f "$DB_FILE" ]; then
    echo '{}' > "$DB_FILE"
fi

# Collect video files
mapfile -t ALL_FILES < <(find "$SCAN_DIR" -type f \( -name "*.mp4" -o -name "*.mkv" -o -name "*.webm" -o -name "*.avi" \) | sort)
TOTAL_IN_CHANNEL=${#ALL_FILES[@]}

# Filter already-checked files
FILES=()
ALREADY_CHECKED=0
for filepath in "${ALL_FILES[@]}"; do
    file_mtime=$(stat -c%Y "$filepath" 2>/dev/null || echo 0)
    already=$(python3 -c "
import json
try:
    db = json.load(open('${DB_FILE}'))
    entry = db.get(r'''${filepath}''')
    if entry and entry.get('checked_mtime', 0) >= ${file_mtime}:
        print('yes')
    else:
        print('no')
except:
    print('no')
" 2>/dev/null || echo "no")
    if [ "$already" = "yes" ]; then
        ALREADY_CHECKED=$((ALREADY_CHECKED + 1))
    else
        FILES+=("$filepath")
    fi
done

TO_SCAN=${#FILES[@]}

if [ "$TO_SCAN" -eq 0 ]; then
    echo "DONE|${CHANNEL}|0|0|0|0|${TOTAL_IN_CHANNEL}|${ALREADY_CHECKED}|All files already checked" > "$STATUS_FILE"
    exit 0
fi

# Write initial status
MODE=$( [ "$QUICK" = true ] && echo "quick" || echo "full" )
echo "SCANNING|${CHANNEL}|0|${TO_SCAN}|0|0|${TOTAL_IN_CHANNEL}|${ALREADY_CHECKED}|Starting..." > "$STATUS_FILE"

HEALTHY=0
BROKEN=0
IDX=0

for filepath in "${FILES[@]}"; do
    IDX=$((IDX + 1))

    # Check for cancellation
    if [ ! -f "$LOCK_FILE" ]; then
        echo "CANCELLED|${CHANNEL}|${IDX}|${TO_SCAN}|${HEALTHY}|${BROKEN}|${TOTAL_IN_CHANNEL}|${ALREADY_CHECKED}|Cancelled by user" > "$STATUS_FILE"
        break
    fi

    filename=$(basename "$filepath")
    rel_path="${filepath#$SHOWS_DIR/}"
    filesize=$(stat -c%s "$filepath" 2>/dev/null || echo 0)
    file_mtime=$(stat -c%Y "$filepath" 2>/dev/null || echo 0)

    # Truncate filename for display
    display_name="$filename"
    if [ ${#display_name} -gt 60 ]; then
        display_name="${display_name:0:57}..."
    fi

    # Write status BEFORE scanning this file
    echo "SCANNING|${CHANNEL}|${IDX}|${TO_SCAN}|${HEALTHY}|${BROKEN}|${TOTAL_IN_CHANNEL}|${ALREADY_CHECKED}|${display_name}" > "$STATUS_FILE"

    # Extract video ID
    video_id=""
    if [[ "$filename" =~ \[([a-zA-Z0-9_-]{11})\]\.[^.]+$ ]]; then
        video_id="${BASH_REMATCH[1]}"
    fi

    # Scan at lowest CPU priority
    if [ "$QUICK" = true ]; then
        ERROR_OUTPUT=$(nice -n 19 ffprobe -v error -i "$filepath" 2>&1) || true
    else
        ERROR_OUTPUT=$(nice -n 19 ffmpeg -v error -i "$filepath" -f null - 2>&1) || true
    fi

    if [ -z "$ERROR_OUTPUT" ]; then
        HEALTHY=$((HEALTHY + 1))
        STATUS="healthy"
    else
        BROKEN=$((BROKEN + 1))
        STATUS="broken"
        ERROR_SHORT=$(echo "$ERROR_OUTPUT" | head -3 | tr '\n' ' ' | cut -c1-200)
    fi

    # Save to persistent DB
    python3 -c "
import json, time
db_path = r'''${DB_FILE}'''
try:
    db = json.load(open(db_path))
except:
    db = {}
db[r'''${filepath}'''] = {
    'status': '${STATUS}',
    'checked': time.strftime('%Y-%m-%d %H:%M:%S'),
    'checked_mtime': ${file_mtime},
    'size_mb': round(${filesize} / 1048576, 1),
    'video_id': '${video_id}',
    'channel': '${CHANNEL}',
    'rel_path': r'''${rel_path}''',
    'error': r'''$(echo "${ERROR_OUTPUT:-}" | head -3 | tr '\n' ' ' | cut -c1-200 | sed "s/'/\\\\'/g")''' if '${STATUS}' == 'broken' else ''
}
json.dump(db, open(db_path, 'w'))
" 2>/dev/null || true

    # Throttle
    if [ "$THROTTLE" -gt 0 ] && [ "$IDX" -lt "$TO_SCAN" ]; then
        sleep "$THROTTLE"
    fi
done

# Final status
echo "DONE|${CHANNEL}|${IDX}|${TO_SCAN}|${HEALTHY}|${BROKEN}|${TOTAL_IN_CHANNEL}|${ALREADY_CHECKED}|Complete" > "$STATUS_FILE"
