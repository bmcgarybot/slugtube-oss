#!/bin/bash
# Fetch YouTube channel banner images using metadata from existing videos
# Reads channel_url from .info.json files already on disk — no URL matching needed

SHOWS_DIR="/shows"
COOKIE_FILE="/config/Cookie/cookies.txt"
LOG="/config/logs/slugtube.log"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [banners] $1" | tee -a "$LOG"; }

log "Starting banner fetch..."

COOKIE_ARGS=""
if [ -f "$COOKIE_FILE" ]; then
    COOKIE_ARGS="--cookies $COOKIE_FILE"
fi

count=0
skipped=0
failed=0
total=0

for channel_dir in "$SHOWS_DIR"/*/; do
    [ ! -d "$channel_dir" ] && continue
    channel_name=$(basename "$channel_dir")
    total=$((total + 1))

    # Skip if banner already exists
    if [ -f "$channel_dir/banner.jpg" ]; then
        skipped=$((skipped + 1))
        continue
    fi

    # Find a channel URL from any .info.json in this channel's directory
    channel_url=""
    json_file=$(find "$channel_dir" -name "*.info.json" -type f | head -1)

    if [ -n "$json_file" ]; then
        channel_url=$(python3 -c "
import json, sys
try:
    with open('$json_file', 'r', errors='replace') as f:
        d = json.load(f)
    url = d.get('channel_url') or d.get('uploader_url') or ''
    if url:
        print(url)
except:
    pass
" 2>/dev/null)
    fi

    if [ -z "$channel_url" ]; then
        log "  ⏭️  $channel_name — no channel URL found in metadata"
        failed=$((failed + 1))
        continue
    fi

    log "  Fetching banner for: $channel_name ($channel_url)"

    # Use yt-dlp to get channel page thumbnails
    banner_url=$(yt-dlp $COOKIE_ARGS --dump-json --playlist-items 0 "$channel_url" 2>/dev/null | \
        python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    thumbs = data.get('thumbnails', [])
    # Look for banner-type thumbnails
    banners = [t for t in thumbs if t.get('width', 0) > 1000 and t.get('height', 0) < t.get('width', 0)]
    if banners:
        best = sorted(banners, key=lambda t: t.get('width', 0), reverse=True)[0]
        print(best['url'])
    else:
        for t in thumbs:
            if 'banner' in t.get('id', '').lower():
                print(t['url'])
                break
except:
    pass
" 2>/dev/null)

    if [ -n "$banner_url" ]; then
        curl -s -L -o "${channel_dir}banner.jpg" "$banner_url" 2>/dev/null
        if [ -f "${channel_dir}banner.jpg" ] && [ -s "${channel_dir}banner.jpg" ]; then
            count=$((count + 1))
            log "  ✅ $channel_name"
        else
            rm -f "${channel_dir}banner.jpg"
            failed=$((failed + 1))
            log "  ❌ $channel_name — download failed"
        fi
    else
        failed=$((failed + 1))
        log "  ❌ $channel_name — no banner URL found"
    fi

    sleep 2
done

log "Banner fetch done. Channels: $total, Downloaded: $count, Already had: $skipped, Failed: $failed"
