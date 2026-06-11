#!/bin/bash
# ============================================================
# SlugTube Health Check — Per-Channel Video Integrity Scanner
# ============================================================
# Designed to run on-demand per channel, NOT as a library-wide
# scheduled scan. Saves results per-file so progress is never lost.
#
# Runs at lowest CPU priority (nice -n 19) so streaming isn't affected.
#
# Usage:
#   healthcheck.sh --channel "Channel Name" [--quick] [--fix] [--throttle N]
#
# Modes:
#   (default)   Full decode scan (reads every frame)
#   --quick     Container/stream check only (fast)
#   --fix       Re-download broken files after scan
#   --throttle  Seconds to sleep between files (default: 1)
# ============================================================

set -euo pipefail

SHOWS_DIR="/shows"
RESULTS_DIR="/config/healthcheck"
DB_FILE="${RESULTS_DIR}/checked.json"
LOCK_FILE="/tmp/healthcheck.lock"

QUICK=false
FIX=false
CHANNEL=""
THROTTLE=1

# ----- Parse arguments -----
while [[ $# -gt 0 ]]; do
    case "$1" in
        --quick)     QUICK=true; shift ;;
        --fix)       FIX=true; shift ;;
        --channel)   CHANNEL="$2"; shift 2 ;;
        --throttle)  THROTTLE="$2"; shift 2 ;;
        --cancel)    rm -f "$LOCK_FILE"; echo '{"status":"cancelled"}' > "${RESULTS_DIR}/progress.json" 2>/dev/null; exit 0 ;;
        *)           echo "Unknown arg: $1"; exit 1 ;;
    esac
done

if [ -z "$CHANNEL" ]; then
    echo "❌ --channel is required. Health checks run per-channel."
    exit 1
fi

SCAN_DIR="$SHOWS_DIR/$CHANNEL"
if [ ! -d "$SCAN_DIR" ]; then
    echo "❌ Channel folder not found: $SCAN_DIR"
    exit 1
fi

# ----- Lock (single instance) -----
if [ -f "$LOCK_FILE" ]; then
    LOCK_PID=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
    if [ -n "$LOCK_PID" ] && kill -0 "$LOCK_PID" 2>/dev/null; then
        echo "❌ Health check already running (PID $LOCK_PID)"
        exit 1
    fi
    rm -f "$LOCK_FILE"
fi
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

# ----- Setup -----
mkdir -p "$RESULTS_DIR"

# Load persistent per-file database
# Structure: { "filepath": { "status": "healthy"|"broken", "checked": "timestamp", "error": "" } }
if [ ! -f "$DB_FILE" ]; then
    echo '{}' > "$DB_FILE"
fi

# ----- Collect files to scan -----
echo "🔍 Collecting video files from: $CHANNEL"

mapfile -t ALL_FILES < <(find "$SCAN_DIR" -type f \( -name "*.mp4" -o -name "*.mkv" -o -name "*.webm" -o -name "*.avi" \) | sort)
TOTAL_IN_CHANNEL=${#ALL_FILES[@]}

# Filter out already-checked files
FILES=()
ALREADY_CHECKED=0
for filepath in "${ALL_FILES[@]}"; do
    # Check if this file was already scanned (and file hasn't been modified since)
    file_mtime=$(stat -c%Y "$filepath" 2>/dev/null || echo 0)
    already=$(python3 -c "
import json
db = json.load(open('$DB_FILE'))
entry = db.get('''$filepath''')
if entry and entry.get('checked_mtime', 0) >= $file_mtime:
    print('yes')
else:
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
    echo "✅ All $TOTAL_IN_CHANNEL videos in '$CHANNEL' already checked. No new files to scan."
    # Update progress file for UI
    python3 -c "
import json
progress = {
    'status': 'complete',
    'channel': '$CHANNEL',
    'current': 0,
    'total': 0,
    'total_in_channel': $TOTAL_IN_CHANNEL,
    'already_checked': $ALREADY_CHECKED,
    'healthy': 0,
    'broken': 0,
    'pct': 100,
    'current_file': '',
    'message': 'All files already checked'
}
json.dump(progress, open('${RESULTS_DIR}/progress.json', 'w'))
"
    exit 0
fi

echo "📊 $CHANNEL: $TO_SCAN new files to scan ($ALREADY_CHECKED already checked out of $TOTAL_IN_CHANNEL total)"
echo ""

# ----- Scan at lowest CPU priority -----
HEALTHY=0
BROKEN=0
SCAN_START=$(date +%s)
MODE=$( [ "$QUICK" = true ] && echo "quick" || echo "full" )

update_progress() {
    local current=$1
    local status=$2
    local current_file=$3
    python3 -c "
import json
data = {
    'status': '$status',
    'channel': '$CHANNEL',
    'current': $current,
    'total': $TO_SCAN,
    'total_in_channel': $TOTAL_IN_CHANNEL,
    'already_checked': $ALREADY_CHECKED,
    'healthy': $HEALTHY,
    'broken': $BROKEN,
    'pct': round($current / $TO_SCAN * 100, 1) if $TO_SCAN > 0 else 0,
    'current_file': '''$current_file''',
    'started': $SCAN_START,
    'mode': '$MODE'
}
json.dump(data, open('${RESULTS_DIR}/progress.json', 'w'))
"
}

IDX=0
for filepath in "${FILES[@]}"; do
    IDX=$((IDX + 1))

    # Check for cancellation
    if [ ! -f "$LOCK_FILE" ]; then
        echo "🛑 Cancelled."
        update_progress $IDX "cancelled" ""
        break
    fi

    filename=$(basename "$filepath")
    rel_path="${filepath#$SHOWS_DIR/}"
    filesize=$(stat -c%s "$filepath" 2>/dev/null || echo 0)
    filesize_mb=$(echo "scale=1; $filesize / 1048576" | bc)
    file_mtime=$(stat -c%Y "$filepath" 2>/dev/null || echo 0)

    update_progress $IDX "scanning" "$rel_path"

    # Extract video ID from filename if present [videoID]
    video_id=""
    if [[ "$filename" =~ \[([a-zA-Z0-9_-]{11})\]\.[^.]+$ ]]; then
        video_id="${BASH_REMATCH[1]}"
    fi

    # Run at lowest CPU priority
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
        ERROR_SHORT=$(echo "$ERROR_OUTPUT" | head -5 | tr '\n' ' ' | cut -c1-300)
        echo "❌ [$IDX/$TO_SCAN] $rel_path ($filesize_mb MB)"
        echo "   Error: $ERROR_SHORT"
    fi

    # Save result to persistent database (per-file, never lost)
    python3 -c "
import json, time

db_path = '$DB_FILE'
try:
    db = json.load(open(db_path))
except:
    db = {}

db['''$filepath'''] = {
    'status': '$STATUS',
    'checked': time.strftime('%Y-%m-%d %H:%M:%S'),
    'checked_mtime': $file_mtime,
    'size_mb': round($filesize / 1048576, 1),
    'video_id': '$video_id',
    'channel': '$CHANNEL',
    'rel_path': '''$rel_path''',
    'error': '''$(echo "$ERROR_OUTPUT" | head -5 | sed "s/'/\\\\'/g" | tr '\n' ' ' | cut -c1-300)''' if '$STATUS' == 'broken' else ''
}

json.dump(db, open(db_path, 'w'), indent=2)
" 2>/dev/null || true

    # Throttle between files to keep server responsive
    if [ "$THROTTLE" -gt 0 ] && [ "$IDX" -lt "$TO_SCAN" ]; then
        sleep "$THROTTLE"
    fi
done

SCAN_END=$(date +%s)
DURATION=$((SCAN_END - SCAN_START))
DURATION_MIN=$(echo "scale=1; $DURATION / 60" | bc)

# ----- Summary -----
echo ""
echo "=========================================="
echo " 🩺 Health Check Complete: $CHANNEL"
echo "=========================================="
echo " New files scanned: $((HEALTHY + BROKEN))"
echo " ✅ Healthy:        $HEALTHY"
echo " ❌ Broken:         $BROKEN"
echo " ⏭️  Already checked: $ALREADY_CHECKED"
echo " ⏱️  Duration:       ${DURATION_MIN} minutes"
echo " Mode:             $([ "$QUICK" = true ] && echo 'Quick' || echo 'Full')"
echo "=========================================="

update_progress $TO_SCAN "complete" ""

# ----- Fix mode -----
if [ "$FIX" = true ] && [ "$BROKEN" -gt 0 ]; then
    echo ""
    echo "🔧 Queuing $BROKEN broken files for re-download..."
    python3 -c "
import json
db = json.load(open('$DB_FILE'))
for path, entry in db.items():
    if entry.get('channel') == '$CHANNEL' and entry.get('status') == 'broken' and entry.get('video_id'):
        print(entry['video_id'])
" | while read -r vid; do
        if [ -n "$vid" ]; then
            echo "   🔄 Removing from archive: $vid"
            sed -i "/youtube ${vid}/d" /config/archive/downloaded.txt 2>/dev/null || true
        fi
    done
fi
