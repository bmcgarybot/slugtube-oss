#!/bin/bash
# ============================================================
# SlugTube Health Check — Video Integrity Scanner
# ============================================================
# Uses ffmpeg to decode every frame and detect corruption,
# truncation, or incomplete downloads.
#
# Usage:
#   healthcheck.sh [--channel "Channel Name"] [--quick] [--fix]
#
# Modes:
#   (default)   Full decode scan (slow but thorough — reads every frame)
#   --quick     Container/stream check only (fast, catches most issues)
#   --fix       Re-download broken files after scan
#   --channel   Scan only one channel folder
#   --recent N  Only scan files modified in the last N days
#   --resume    Skip files already in the results log
# ============================================================

set -euo pipefail

SHOWS_DIR="/shows"
RESULTS_DIR="/config/healthcheck"
RESULTS_FILE="${RESULTS_DIR}/results.json"
PROGRESS_FILE="${RESULTS_DIR}/progress.json"
LOG_FILE="${RESULTS_DIR}/healthcheck.log"
LOCK_FILE="/tmp/healthcheck.lock"

QUICK=false
FIX=false
CHANNEL=""
RECENT_DAYS=0
RESUME=false

# ----- Parse arguments -----
while [[ $# -gt 0 ]]; do
    case "$1" in
        --quick)   QUICK=true; shift ;;
        --fix)     FIX=true; shift ;;
        --channel) CHANNEL="$2"; shift 2 ;;
        --recent)  RECENT_DAYS="$2"; shift 2 ;;
        --resume)  RESUME=true; shift ;;
        --cancel)  rm -f "$LOCK_FILE"; echo '{"status":"cancelled"}' > "$PROGRESS_FILE"; exit 0 ;;
        *)         echo "Unknown arg: $1"; exit 1 ;;
    esac
done

# ----- Lock (single instance) -----
if [ -f "$LOCK_FILE" ]; then
    LOCK_PID=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
    if [ -n "$LOCK_PID" ] && kill -0 "$LOCK_PID" 2>/dev/null; then
        echo "❌ Health check already running (PID $LOCK_PID)"
        exit 1
    fi
    echo "🔓 Stale lock found. Cleaning up."
    rm -f "$LOCK_FILE"
fi
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

# ----- Setup -----
mkdir -p "$RESULTS_DIR"

# Load previously scanned files if resuming
declare -A ALREADY_SCANNED
if [ "$RESUME" = true ] && [ -f "$RESULTS_FILE" ]; then
    while IFS= read -r path; do
        ALREADY_SCANNED["$path"]=1
    done < <(python3 -c "
import json, sys
try:
    data = json.load(open('$RESULTS_FILE'))
    for f in data.get('files', []):
        print(f['path'])
except: pass
")
fi

# ----- Collect files to scan -----
SCAN_DIR="$SHOWS_DIR"
if [ -n "$CHANNEL" ]; then
    SCAN_DIR="$SHOWS_DIR/$CHANNEL"
    if [ ! -d "$SCAN_DIR" ]; then
        echo "❌ Channel folder not found: $SCAN_DIR"
        exit 1
    fi
fi

echo "🔍 Collecting video files from: $SCAN_DIR"

FIND_ARGS=( "$SCAN_DIR" -type f \( -name "*.mp4" -o -name "*.mkv" -o -name "*.webm" -o -name "*.avi" \) )
if [ "$RECENT_DAYS" -gt 0 ]; then
    FIND_ARGS+=( -mtime "-${RECENT_DAYS}" )
fi

mapfile -t FILES < <(find "${FIND_ARGS[@]}" | sort)
TOTAL=${#FILES[@]}

if [ "$TOTAL" -eq 0 ]; then
    echo "✅ No video files found to scan."
    echo '{"status":"complete","total":0,"healthy":0,"broken":0,"files":[]}' > "$RESULTS_FILE"
    exit 0
fi

echo "📊 Found $TOTAL video files to scan"
echo ""

# ----- Scan -----
HEALTHY=0
BROKEN=0
SKIPPED=0
ERRORS_JSON="[]"
ALL_JSON="[]"
SCAN_START=$(date +%s)

update_progress() {
    local current=$1
    local status=$2
    local current_file=$3
    python3 -c "
import json
data = {
    'status': '$status',
    'current': $current,
    'total': $TOTAL,
    'healthy': $HEALTHY,
    'broken': $BROKEN,
    'skipped': $SKIPPED,
    'pct': round($current / $TOTAL * 100, 1) if $TOTAL > 0 else 0,
    'current_file': '''$current_file''',
    'started': $SCAN_START
}
json.dump(data, open('$PROGRESS_FILE', 'w'))
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

    # Skip if already scanned (resume mode)
    if [ "$RESUME" = true ] && [ -n "${ALREADY_SCANNED[$filepath]+x}" ]; then
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    filename=$(basename "$filepath")
    rel_path="${filepath#$SHOWS_DIR/}"
    filesize=$(stat -c%s "$filepath" 2>/dev/null || echo 0)
    filesize_mb=$(echo "scale=1; $filesize / 1048576" | bc)

    update_progress $IDX "scanning" "$rel_path"

    # Extract video ID from filename if present [videoID]
    video_id=""
    if [[ "$filename" =~ \[([a-zA-Z0-9_-]{11})\]\.[^.]+$ ]]; then
        video_id="${BASH_REMATCH[1]}"
    fi

    if [ "$QUICK" = true ]; then
        # Quick mode: just check container integrity with ffprobe
        ERROR_OUTPUT=$(ffprobe -v error -i "$filepath" 2>&1) || true
    else
        # Full mode: decode entire file, capture stderr errors
        ERROR_OUTPUT=$(ffmpeg -v error -i "$filepath" -f null - 2>&1) || true
    fi

    if [ -z "$ERROR_OUTPUT" ]; then
        HEALTHY=$((HEALTHY + 1))
        STATUS="healthy"
    else
        BROKEN=$((BROKEN + 1))
        STATUS="broken"
        # Truncate error output for JSON
        ERROR_SHORT=$(echo "$ERROR_OUTPUT" | head -5 | tr '\n' ' ' | cut -c1-300)
        echo "❌ [$IDX/$TOTAL] $rel_path ($filesize_mb MB)" | tee -a "$LOG_FILE"
        echo "   Error: $ERROR_SHORT" | tee -a "$LOG_FILE"
    fi

    # Build JSON entry
    python3 -c "
import json, os

entry = {
    'path': '''$filepath''',
    'rel_path': '''$rel_path''',
    'filename': '''$filename''',
    'video_id': '$video_id',
    'size_mb': round($filesize / 1048576, 1),
    'status': '$STATUS',
    'error': '''$(echo "$ERROR_OUTPUT" | head -10 | sed "s/'/\\\\'/g")''' if '$STATUS' == 'broken' else ''
}

# Append to results file incrementally
results_path = '$RESULTS_FILE'
try:
    data = json.load(open(results_path))
except:
    data = {'status': 'running', 'total': 0, 'healthy': 0, 'broken': 0, 'files': []}

data['files'].append(entry)
data['total'] = $IDX
data['healthy'] = $HEALTHY
data['broken'] = $BROKEN
json.dump(data, open(results_path, 'w'))
" 2>/dev/null || true

    # Progress indicator every 50 files
    if [ $((IDX % 50)) -eq 0 ]; then
        echo "📊 Progress: $IDX/$TOTAL scanned ($HEALTHY healthy, $BROKEN broken, $SKIPPED skipped)"
    fi
done

SCAN_END=$(date +%s)
DURATION=$((SCAN_END - SCAN_START))
DURATION_MIN=$(echo "scale=1; $DURATION / 60" | bc)

# ----- Final results -----
echo ""
echo "=========================================="
echo " 🐌 SlugTube Health Check Complete"
echo "=========================================="
echo " Total scanned:  $((HEALTHY + BROKEN))"
echo " ✅ Healthy:     $HEALTHY"
echo " ❌ Broken:      $BROKEN"
echo " ⏭️  Skipped:     $SKIPPED"
echo " ⏱️  Duration:    ${DURATION_MIN} minutes"
echo " Mode:          $([ "$QUICK" = true ] && echo 'Quick (container only)' || echo 'Full (decode every frame)')"
echo "=========================================="

# Update final results
python3 -c "
import json, time

results_path = '$RESULTS_FILE'
try:
    data = json.load(open(results_path))
except:
    data = {'files': []}

data['status'] = 'complete'
data['total'] = $((HEALTHY + BROKEN))
data['healthy'] = $HEALTHY
data['broken'] = $BROKEN
data['skipped'] = $SKIPPED
data['duration_sec'] = $DURATION
data['mode'] = 'quick' if $( [ "$QUICK" = true ] && echo 'True' || echo 'False' ) else 'full'
data['scan_time'] = time.strftime('%Y-%m-%d %H:%M:%S')
data['channel'] = '$CHANNEL' or None

json.dump(data, open(results_path, 'w'), indent=2)
print(json.dumps({'broken': $BROKEN, 'healthy': $HEALTHY}))
"

# Update progress to complete
update_progress $TOTAL "complete" ""

# ----- Fix mode: re-download broken files -----
if [ "$FIX" = true ] && [ "$BROKEN" -gt 0 ]; then
    echo ""
    echo "🔧 Fix mode: attempting to re-download $BROKEN broken files..."

    python3 -c "
import json
data = json.load(open('$RESULTS_FILE'))
for f in data.get('files', []):
    if f['status'] == 'broken' and f.get('video_id'):
        print(f['video_id'])
" | while read -r vid; do
        if [ -n "$vid" ]; then
            echo "   🔄 Re-downloading: $vid"
            # Remove from archive so yt-dlp will re-download
            sed -i "/youtube ${vid}/d" /config/archive/downloaded.txt 2>/dev/null || true
            # Download
            yt-dlp --cookies /config/Cookie/cookies.txt \
                   -f "bestvideo[height<=1080]+bestaudio/best[height<=1080]" \
                   --merge-output-format mp4 \
                   --write-subs --write-auto-subs --sub-lang en \
                   --embed-subs --embed-metadata --embed-thumbnail \
                   --convert-thumbnails jpg \
                   "https://www.youtube.com/watch?v=${vid}" \
                   -o "/tmp/redownload/%(title)s [%(id)s].%(ext)s" 2>&1 || echo "   ⚠️ Failed to re-download $vid"
        fi
    done
fi

echo ""
echo "📄 Full results: $RESULTS_FILE"
echo "📋 Log: $LOG_FILE"
