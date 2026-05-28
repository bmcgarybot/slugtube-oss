#!/bin/bash
# Fetch YouTube channel banner/poster images
# Uses channels.txt as primary URL source, falls back to .info.json metadata

SHOWS_DIR="/shows"
CHANNELS_FILE="/config/channels.txt"
COOKIE_FILE="/config/Cookie/cookies.txt"
LOG="/config/logs/slugtube.log"
TMPJSON="/tmp/slugtube-banner-fetch.json"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [banners] $1" | tee -a "$LOG"; }

log "Starting banner fetch..."

COOKIE_ARGS=""
if [ -f "$COOKIE_FILE" ]; then
    COOKIE_ARGS="--cookies $COOKIE_FILE"
fi

# Build lookup: folder_name -> channel_url from channels.txt
declare -A CHANNEL_URLS
if [ -f "$CHANNELS_FILE" ]; then
    while IFS='|' read -r url name; do
        [[ "$url" =~ ^# ]] && continue
        [ -z "$url" ] || [ -z "$name" ] && continue
        clean_url="${url%/videos}"
        CHANNEL_URLS["$name"]="$clean_url"
    done < "$CHANNELS_FILE"
fi

count=0
skipped=0
failed=0
total=0

for channel_dir in "$SHOWS_DIR"/*/; do
    [ ! -d "$channel_dir" ] && continue
    channel_name=$(basename "$channel_dir")
    total=$((total + 1))

    # Skip if both already exist
    if [ -f "$channel_dir/poster.jpg" ] && [ -f "$channel_dir/banner.jpg" ]; then
        skipped=$((skipped + 1))
        continue
    fi

    # Get channel URL: channels.txt first, then .info.json
    channel_url="${CHANNEL_URLS[$channel_name]:-}"

    if [ -z "$channel_url" ]; then
        json_file=$(find "$channel_dir" -name "*.info.json" -type f 2>/dev/null | head -1)
        if [ -n "$json_file" ]; then
            channel_url=$(python3 -c "
import json
try:
    with open(r'$json_file', 'r', errors='replace') as f:
        d = json.load(f)
    print(d.get('channel_url') or d.get('uploader_url') or '')
except: pass
" 2>/dev/null)
        fi
    fi

    if [ -z "$channel_url" ]; then
        log "  ⏭️  $channel_name — no channel URL"
        failed=$((failed + 1))
        continue
    fi

    log "  Fetching art for: $channel_name ($channel_url)"

    # Dump channel/video metadata to temp file
    rm -f "$TMPJSON"
    yt-dlp $COOKIE_ARGS --dump-json --playlist-items 0 --flat-playlist "$channel_url" > "$TMPJSON" 2>/dev/null
    if [ ! -s "$TMPJSON" ]; then
        yt-dlp $COOKIE_ARGS --dump-json --playlist-items 1 "$channel_url/videos" > "$TMPJSON" 2>/dev/null
    fi

    got_poster=false
    got_banner=false

    if [ -s "$TMPJSON" ]; then
        # Extract poster URL
        if [ ! -f "${channel_dir}poster.jpg" ]; then
            poster_url=$(python3 -c "
import json
with open('$TMPJSON') as f:
    d = json.load(f)
thumbs = d.get('thumbnails', [])
# Try tagged avatars first
for t in thumbs:
    tid = (t.get('id') or '').lower()
    if 'avatar' in tid and t.get('url'):
        print(t['url']); exit()
# Fallback: square-ish thumbnail >= 100px
for t in sorted(thumbs, key=lambda x: x.get('width',0), reverse=True):
    w, h = t.get('width',0), t.get('height',0)
    if w > 0 and h > 0 and 0.8 < w/h < 1.3 and w >= 100 and t.get('url'):
        print(t['url']); exit()
" 2>/dev/null)

            if [ -n "$poster_url" ]; then
                curl -s -L -o "${channel_dir}poster.jpg" "$poster_url"
                if [ -s "${channel_dir}poster.jpg" ]; then
                    log "    ✅ poster"
                    got_poster=true
                else
                    rm -f "${channel_dir}poster.jpg"
                fi
            fi
        else
            got_poster=true
        fi

        # Extract banner URL
        if [ ! -f "${channel_dir}banner.jpg" ]; then
            banner_url=$(python3 -c "
import json
with open('$TMPJSON') as f:
    d = json.load(f)
thumbs = d.get('thumbnails', [])
# Try tagged banners first
for t in thumbs:
    tid = (t.get('id') or '').lower()
    if 'banner' in tid and t.get('url'):
        w = t.get('width', 0)
        if w >= 1000 or w == 0:
            print(t['url']); exit()
# Fallback: wide landscape
for t in sorted(thumbs, key=lambda x: x.get('width',0), reverse=True):
    w, h = t.get('width',0), t.get('height',0)
    if w > 0 and h > 0 and w/h > 2.5 and t.get('url'):
        print(t['url']); exit()
" 2>/dev/null)

            if [ -n "$banner_url" ]; then
                curl -s -L -o "${channel_dir}banner.jpg" "$banner_url"
                if [ -s "${channel_dir}banner.jpg" ]; then
                    log "    ✅ banner"
                    got_banner=true
                else
                    rm -f "${channel_dir}banner.jpg"
                fi
            fi
        else
            got_banner=true
        fi
    fi

    # Fallback for poster: check video .info.json files for avatar
    if [ ! -f "${channel_dir}poster.jpg" ]; then
        for jf in $(find "$channel_dir" -name "*.info.json" -type f 2>/dev/null | head -3); do
            avatar_url=$(python3 -c "
import json
with open(r'$jf', 'r', errors='replace') as f:
    d = json.load(f)
for t in d.get('thumbnails', []):
    if 'avatar' in (t.get('id') or '').lower() and t.get('url'):
        print(t['url']); break
" 2>/dev/null)
            if [ -n "$avatar_url" ]; then
                curl -s -L -o "${channel_dir}poster.jpg" "$avatar_url"
                if [ -s "${channel_dir}poster.jpg" ]; then
                    log "    ✅ poster (from video metadata)"
                    got_poster=true
                    break
                else
                    rm -f "${channel_dir}poster.jpg"
                fi
            fi
        done
    fi

    if $got_poster || $got_banner; then
        count=$((count + 1))
    else
        failed=$((failed + 1))
        log "  ❌ $channel_name — no art found"
    fi

    sleep 2
done

rm -f "$TMPJSON"
log "Art fetch done. Channels: $total, Got art: $count, Already had: $skipped, Failed: $failed"
