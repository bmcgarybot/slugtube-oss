#!/bin/bash
# ============================================================
# SlugTube Channel Art Fetcher
# Automatically downloads poster/banner/fanart for channels
# that are missing artwork. Runs after each download cycle.
# ============================================================

set -euo pipefail

SHOWS_DIR="/shows"
COOKIES_FILE="/config/Cookie/cookies.txt"
TEMP_DIR="/config/temp/art"

mkdir -p "$TEMP_DIR"

echo "🎨 Checking for missing channel artwork..."

FIXED=0
SKIPPED=0

# Find channel folders missing artwork
while IFS= read -r -d '' channeldir; do
    CHANNEL_NAME=$(basename "$channeldir")

    # Skip if not a real channel folder (needs Season subfolders)
    if ! find "$channeldir" -maxdepth 1 -type d -name "Season *" 2>/dev/null | grep -q .; then
        continue
    fi

    # Check what's missing
    NEED_POSTER=false
    NEED_NFO=false
    [ ! -f "$channeldir/poster.jpg" ] && NEED_POSTER=true
    [ ! -f "$channeldir/tvshow.nfo" ] && NEED_NFO=true

    # Skip if everything exists
    if ! $NEED_POSTER && ! $NEED_NFO; then
        ((SKIPPED++)) || true
        continue
    fi

    echo "🎨 Processing: $CHANNEL_NAME"

    # ── Find channel URL from existing .info.json ──
    CHANNEL_URL=""
    SAMPLE_JSON=$(find "$channeldir" -name "*.info.json" -o -name "*.json" 2>/dev/null | head -1)
    if [ -n "$SAMPLE_JSON" ] && [ -f "$SAMPLE_JSON" ]; then
        CHANNEL_URL=$(jq -r '.channel_url // .uploader_url // empty' "$SAMPLE_JSON" 2>/dev/null)
    fi

    if [ -z "$CHANNEL_URL" ]; then
        echo "   ⚠️  No channel URL found, skipping artwork"
        continue
    fi

    # ── Download channel avatar as poster ──
    if $NEED_POSTER; then
        echo "   📸 Fetching channel avatar..."
        # Use yt-dlp to grab channel/playlist thumbnail
        if yt-dlp \
            --cookies "$COOKIES_FILE" \
            --write-thumbnail \
            --skip-download \
            --playlist-items 0 \
            --convert-thumbnails jpg \
            -o "$TEMP_DIR/%(channel)s" \
            "$CHANNEL_URL" 2>/dev/null; then

            # Find the downloaded thumbnail
            THUMB=$(find "$TEMP_DIR" -name "*.jpg" -o -name "*.webp" -o -name "*.png" 2>/dev/null | head -1)
            if [ -n "$THUMB" ] && [ -f "$THUMB" ]; then
                cp "$THUMB" "$channeldir/poster.jpg"
                # Also use as fanart if missing
                [ ! -f "$channeldir/fanart.jpg" ] && cp "$THUMB" "$channeldir/fanart.jpg"
                echo "   ✅ poster.jpg saved"
                ((FIXED++)) || true
            else
                echo "   ⚠️  Thumbnail download succeeded but file not found"
            fi
        else
            # Fallback: try to extract avatar from video thumbnail array in info.json
            if [ -n "$SAMPLE_JSON" ] && [ -f "$SAMPLE_JSON" ]; then
                AVATAR_URL=$(jq -r '
                    .thumbnails[-1].url //
                    .channel_follower_count // empty
                ' "$SAMPLE_JSON" 2>/dev/null)
                # Not reliable, just note it
                echo "   ⚠️  Could not fetch channel thumbnail"
            fi
        fi
        # Clean temp
        rm -rf "$TEMP_DIR"/*
    fi

    # ── Generate tvshow.nfo if missing ──
    if $NEED_NFO; then
        # Try to get channel description from info.json
        CHANNEL_DESC=""
        if [ -n "$SAMPLE_JSON" ] && [ -f "$SAMPLE_JSON" ]; then
            CHANNEL_DESC=$(jq -r '.channel_description // .description // "" | .[0:500]' "$SAMPLE_JSON" 2>/dev/null || echo "")
        fi

        cat > "$channeldir/tvshow.nfo" << NFOEOF
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<tvshow>
    <title>${CHANNEL_NAME}</title>
    <sorttitle>${CHANNEL_NAME}</sorttitle>
    <plot>${CHANNEL_DESC}</plot>
    <studio>YouTube</studio>
    <genre>YouTube</genre>
</tvshow>
NFOEOF
        echo "   ✅ tvshow.nfo created"
        ((FIXED++)) || true
    fi

done < <(find "$SHOWS_DIR" -mindepth 1 -maxdepth 1 -type d -print0 2>/dev/null)

# Clean up
rm -rf "$TEMP_DIR"

echo "🎨 Artwork check complete: $FIXED fixed, $SKIPPED already complete"
