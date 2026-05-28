#!/bin/bash
# Fetch YouTube channel poster/banner images
# Method 1: YouTube channel page HTML (og:image meta tag) — no yt-dlp needed
# Method 2: Existing .info.json thumbnail metadata fallback
# Method 3: yt-dlp --write-thumbnail as last resort

SHOWS_DIR="/shows"
CHANNELS_FILE="/config/channels.txt"
COOKIE_FILE="/config/Cookie/cookies.txt"
LOG="/config/logs/slugtube.log"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [banners] $1" | tee -a "$LOG"; }

log "Starting banner fetch..."

CURL_COOKIE=""
if [ -f "$COOKIE_FILE" ]; then
    CURL_COOKIE="-b $COOKIE_FILE"
fi

YT_COOKIE=""
if [ -f "$COOKIE_FILE" ]; then
    YT_COOKIE="--cookies $COOKIE_FILE"
fi

# Build lookup: folder_name -> channel_url from channels.txt
declare -A CHANNEL_URLS
if [ -f "$CHANNELS_FILE" ]; then
    while IFS='|' read -r url name; do
        [[ "$url" =~ ^#  ]] && continue
        [[ "$url" =~ ^[[:space:]]*$ ]] && continue
        [ -z "$url" ] || [ -z "$name" ] && continue
        # Strip /videos suffix for channel page access
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

    need_poster=false
    need_banner=false
    [ ! -f "$channel_dir/poster.jpg" ] && need_poster=true
    [ ! -f "$channel_dir/banner.jpg" ] && need_banner=true

    # Skip if both already exist
    if ! $need_poster && ! $need_banner; then
        skipped=$((skipped + 1))
        continue
    fi

    # Get channel URL from channels.txt
    channel_url="${CHANNEL_URLS[$channel_name]:-}"

    # Fallback: get URL from .info.json
    if [ -z "$channel_url" ]; then
        json_file=$(find "$channel_dir" -name "*.info.json" -type f 2>/dev/null | head -1)
        if [ -n "$json_file" ]; then
            channel_url=$(python3 -c "
import json
try:
    with open(r'''$json_file''', 'r', errors='replace') as f:
        d = json.load(f)
    url = d.get('channel_url') or d.get('uploader_url') or ''
    # Strip /videos if present
    print(url.rstrip('/').removesuffix('/videos'))
except: pass
" 2>/dev/null)
        fi
    fi

    # Skip playlists — they don't have channel art
    if [[ "$channel_url" == *"playlist?"* ]]; then
        log "  ⏭️  $channel_name — playlist (no channel art)"
        skipped=$((skipped + 1))
        continue
    fi

    if [ -z "$channel_url" ]; then
        log "  ⏭️  $channel_name — no channel URL found"
        failed=$((failed + 1))
        continue
    fi

    log "  Fetching art for: $channel_name"
    got_something=false

    # ══════════════════════════════════════════════════════
    # METHOD 1: Scrape YouTube channel page HTML
    # YouTube puts the channel avatar in <meta property="og:image">
    # and sometimes a banner in the page JSON
    # ══════════════════════════════════════════════════════
    if $need_poster; then
        page_html=$(curl -sL $CURL_COOKIE \
            -H "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36" \
            -H "Accept-Language: en-US,en;q=0.9" \
            --max-time 15 \
            "$channel_url" 2>/dev/null)

        if [ -n "$page_html" ]; then
            # Extract og:image (channel avatar)
            poster_url=$(echo "$page_html" | python3 -c "
import sys, re
html = sys.stdin.read()
# Try og:image meta tag
m = re.search(r'<meta\s+property=\"og:image\"\s+content=\"([^\"]+)\"', html)
if m:
    url = m.group(1)
    # Upgrade to high-res: replace =s... with =s900
    url = re.sub(r'=s\d+', '=s900', url)
    print(url)
" 2>/dev/null)

            if [ -n "$poster_url" ]; then
                curl -sL --max-time 10 -o "${channel_dir}poster.jpg" "$poster_url"
                if [ -s "${channel_dir}poster.jpg" ]; then
                    log "    ✅ poster (from channel page)"
                    got_something=true
                    need_poster=false
                else
                    rm -f "${channel_dir}poster.jpg"
                fi
            fi

            # Extract banner from page JSON (ytInitialData)
            if $need_banner; then
                banner_url=$(echo "$page_html" | python3 -c "
import sys, re, json
html = sys.stdin.read()
# Find ytInitialData JSON blob
m = re.search(r'var\s+ytInitialData\s*=\s*(\{.*?\});', html, re.DOTALL)
if not m:
    m = re.search(r'ytInitialData\"\s*:\s*(\{.*?\})\s*[,;]', html, re.DOTALL)
if m:
    try:
        data = json.loads(m.group(1))
        header = data.get('header', {})
        url = ''
        # NEW path: pageHeaderRenderer (2025+)
        phr = header.get('pageHeaderRenderer', {})
        if phr:
            vm = phr.get('content', {}).get('pageHeaderViewModel', {})
            ibvm = vm.get('banner', {}).get('imageBannerViewModel', {})
            sources = ibvm.get('image', {}).get('sources', [])
            if sources:
                best = max(sources, key=lambda s: s.get('width', 0))
                url = best.get('url', '')
        # OLD path: c4TabbedHeaderRenderer (legacy fallback)
        if not url:
            c4 = header.get('c4TabbedHeaderRenderer', {})
            thumbs = c4.get('banner', {}).get('thumbnails', [])
            if thumbs:
                best = max(thumbs, key=lambda t: t.get('width', 0))
                url = best.get('url', '')
        if url:
            print(url)
    except: pass
" 2>/dev/null)

                if [ -n "$banner_url" ]; then
                    # YouTube banner URLs sometimes lack protocol
                    [[ "$banner_url" == //* ]] && banner_url="https:$banner_url"
                    curl -sL --max-time 10 -o "${channel_dir}banner.jpg" "$banner_url"
                    if [ -s "${channel_dir}banner.jpg" ]; then
                        log "    ✅ banner (from channel page)"
                        got_something=true
                        need_banner=false
                    else
                        rm -f "${channel_dir}banner.jpg"
                    fi
                fi
            fi
        fi
    fi

    # ══════════════════════════════════════════════════════
    # METHOD 2: Existing .info.json thumbnail metadata
    # ══════════════════════════════════════════════════════
    if $need_poster; then
        for jf in $(find "$channel_dir" -name "*.info.json" -type f 2>/dev/null | head -3); do
            avatar_url=$(python3 -c "
import json
with open(r'''$jf''', 'r', errors='replace') as f:
    d = json.load(f)
for t in d.get('thumbnails', []):
    tid = (t.get('id') or '').lower()
    if 'avatar' in tid and t.get('url'):
        print(t['url']); break
" 2>/dev/null)
            if [ -n "$avatar_url" ]; then
                curl -sL --max-time 10 -o "${channel_dir}poster.jpg" "$avatar_url"
                if [ -s "${channel_dir}poster.jpg" ]; then
                    log "    ✅ poster (from .info.json)"
                    got_something=true
                    need_poster=false
                    break
                else
                    rm -f "${channel_dir}poster.jpg"
                fi
            fi
        done
    fi

    # ══════════════════════════════════════════════════════
    # METHOD 3: yt-dlp --write-thumbnail (last resort)
    # ══════════════════════════════════════════════════════
    if $need_poster; then
        tmpdir=$(mktemp -d)
        if yt-dlp $YT_COOKIE \
            --write-thumbnail \
            --skip-download \
            --playlist-items 1 \
            --convert-thumbnails jpg \
            -o "$tmpdir/%(channel)s" \
            "$channel_url/videos" 2>/dev/null; then

            thumb=$(find "$tmpdir" -name "*.jpg" 2>/dev/null | head -1)
            if [ -n "$thumb" ] && [ -s "$thumb" ]; then
                cp "$thumb" "${channel_dir}poster.jpg"
                log "    ✅ poster (from yt-dlp thumbnail)"
                got_something=true
            fi
        fi
        rm -rf "$tmpdir"
    fi

    if $got_something; then
        count=$((count + 1))
    else
        failed=$((failed + 1))
        log "  ❌ $channel_name — no art found"
    fi

    # Rate limit: 1 second between channels
    sleep 1
done

log "Art fetch done. Total: $total | Got art: $count | Already had: $skipped | Failed: $failed"
