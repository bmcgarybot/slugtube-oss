#!/bin/bash
# ============================================================
# SlugTube yt-dlp Auto-Updater
# Uses nightly channel for fastest YouTube fixes
# ============================================================

echo "🔄 Updating yt-dlp (nightly channel)..."
BEFORE=$(yt-dlp --version 2>/dev/null || echo "not installed")

pip install --quiet --upgrade --pre yt-dlp 2>&1

AFTER=$(yt-dlp --version 2>/dev/null || echo "failed")

if [ "$BEFORE" = "$AFTER" ]; then
    echo "   ✅ yt-dlp is current: v${AFTER}"
else
    echo "   🆕 yt-dlp updated: v${BEFORE} → v${AFTER}"
fi
