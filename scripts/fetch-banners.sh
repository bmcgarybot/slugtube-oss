#!/bin/bash
# Fetch YouTube channel banner images for all subscribed channels
# Saves as banner.jpg in each channel's directory under /shows/

CHANNELS_FILE="/config/channels.txt"
SHOWS_DIR="/shows"
COOKIE_FILE="/config/Cookie/cookies.txt"
LOG="/config/logs/slugtube.log"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [banners] $1" | tee -a "$LOG"; }

log "Starting banner fetch..."

count=0
skipped=0
failed=0

while IFS= read -r line; do
    # Skip comments and empty lines
    [[ "$line" =~ ^#.*$ || -z "$line" ]] && continue

    url="$line"

    # Extract channel name (same logic as app.py)
    if [[ "$url" == *"/@"* ]]; then
        name=$(echo "$url" | sed 's|.*/@||' | sed 's|/.*||')
    elif [[ "$url" == *"/c/"* ]]; then
        name=$(echo "$url" | sed 's|.*/c/||' | sed 's|/.*||')
    else
        continue  # Skip playlists and channel IDs for banners
    fi

    channel_dir="$SHOWS_DIR/$name"

    # Skip if banner already exists
    if [ -f "$channel_dir/banner.jpg" ]; then
        skipped=$((skipped + 1))
        continue
    fi

    # Skip if channel directory doesn't exist yet
    if [ ! -d "$channel_dir" ]; then
        continue
    fi

    log "  Fetching banner for: $name"

    # Use yt-dlp to get channel metadata and extract banner URL
    COOKIE_ARGS=""
    if [ -f "$COOKIE_FILE" ]; then
        COOKIE_ARGS="--cookies $COOKIE_FILE"
    fi

    # Download the channel page info and extract banner
    banner_url=$(yt-dlp $COOKIE_ARGS --dump-json --playlist-items 0 "$url" 2>/dev/null | \
        python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    thumbs = data.get('thumbnails', [])
    # Look for banner-type thumbnails (usually the widest ones)
    banners = [t for t in thumbs if t.get('width', 0) > 1000 and t.get('height', 0) < t.get('width', 0)]
    if banners:
        # Get the highest resolution banner
        best = sorted(banners, key=lambda t: t.get('width', 0), reverse=True)[0]
        print(best['url'])
    else:
        # Try channel metadata
        for t in thumbs:
            if 'banner' in t.get('id', '').lower():
                print(t['url'])
                break
except:
    pass
" 2>/dev/null)

    if [ -n "$banner_url" ]; then
        curl -s -L -o "$channel_dir/banner.jpg" "$banner_url" 2>/dev/null
        if [ -f "$channel_dir/banner.jpg" ] && [ -s "$channel_dir/banner.jpg" ]; then
            count=$((count + 1))
            log "  ✅ $name banner saved"
        else
            rm -f "$channel_dir/banner.jpg"
            failed=$((failed + 1))
        fi
    else
        failed=$((failed + 1))
    fi

    # Be nice to YouTube
    sleep 2

done < "$CHANNELS_FILE"

log "Banner fetch done. Downloaded: $count, Skipped (already have): $skipped, Failed: $failed"
