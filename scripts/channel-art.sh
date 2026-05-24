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

        # Method 1: Use --playlist-items 1 (more reliable than 0)
        ART_OK=false
        if yt-dlp \
            --cookies "$COOKIES_FILE" \
            --write-thumbnail \
            --skip-download \
            --playlist-items 1 \
            --convert-thumbnails jpg \
            -o "$TEMP_DIR/%(channel)s" \
            "$CHANNEL_URL" 2>/dev/null; then

            THUMB=$(find "$TEMP_DIR" -name "*.jpg" 2>/dev/null | head -1)
            if [ -n "$THUMB" ] && [ -f "$THUMB" ]; then
                cp "$THUMB" "$channeldir/poster.jpg"
                [ ! -f "$channeldir/fanart.jpg" ] && cp "$THUMB" "$channeldir/fanart.jpg"
                echo "   ✅ poster.jpg saved (method 1)"
                ((FIXED++)) || true
                ART_OK=true
            fi
        fi
        rm -rf "$TEMP_DIR"/*

        # Method 2: Fallback — grab avatar from channel page directly
        if ! $ART_OK; then
            echo "   📸 Trying fallback method..."
            # Extract avatar URL from any .info.json in the channel
            if [ -n "$SAMPLE_JSON" ] && [ -f "$SAMPLE_JSON" ]; then
                AVATAR_URL=$(jq -r '
                    (.thumbnails // [])[] | select(.id == "avatar_uncropped" or (.url // "" | test("yt3.ggpht|yt3.googleusercontent"))) | .url
                ' "$SAMPLE_JSON" 2>/dev/null | head -1)

                if [ -n "$AVATAR_URL" ] && [ "$AVATAR_URL" != "null" ]; then
                    if curl -sL -o "$TEMP_DIR/avatar.jpg" "$AVATAR_URL" 2>/dev/null; then
                        if [ -s "$TEMP_DIR/avatar.jpg" ]; then
                            cp "$TEMP_DIR/avatar.jpg" "$channeldir/poster.jpg"
                            [ ! -f "$channeldir/fanart.jpg" ] && cp "$TEMP_DIR/avatar.jpg" "$channeldir/fanart.jpg"
                            echo "   ✅ poster.jpg saved (method 2 - from info.json)"
                            ((FIXED++)) || true
                            ART_OK=true
                        fi
                    fi
                fi
            fi
        fi
        rm -rf "$TEMP_DIR"/*

        if ! $ART_OK; then
            echo "   ⚠️  Could not fetch channel artwork for $CHANNEL_NAME"
        fi
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
