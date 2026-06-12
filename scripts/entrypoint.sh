#!/bin/bash
# ============================================================
# SlugTube Entrypoint
# Sets up cron jobs and starts the scheduler
# ============================================================

set -e

echo "=========================================="
echo " 🐌 SlugTube Starting Up"
echo "=========================================="
echo "Time: $(date)"

# ----- Create default files if missing -----
if [ ! -f /config/channels.txt ]; then
    echo "# SlugTube Channels - One URL per line" > /config/channels.txt
    echo "# Example: https://www.youtube.com/@EFTUniverse" >> /config/channels.txt
    echo "" >> /config/channels.txt
    echo "⚠️  No channels.txt found — created template at C:\\SlugTube\\channels.txt"
fi

if [ ! -f /config/archive/downloaded.txt ]; then
    mkdir -p /config/archive
    touch /config/archive/downloaded.txt
    echo "📋 Created empty archive file"
fi

mkdir -p /config/logs /config/temp /config/Cookie

# ----- Update yt-dlp on startup -----
echo "🔄 Updating yt-dlp..."
/app/scripts/update-ytdlp.sh

# ----- Set up cron jobs -----
echo "⏰ Setting up schedules..."

FAST_CRON="${FAST_CRON:-0 */6 * * *}"
FULL_CRON="${FULL_CRON:-0 3 * * 0}"
UPDATE_CRON="${UPDATE_CRON:-0 1 * * *}"

# Set system timezone from TZ env var (so cron, date, and logs all agree)
if [ -n "$TZ" ] && [ -f "/usr/share/zoneinfo/$TZ" ]; then
    ln -sf "/usr/share/zoneinfo/$TZ" /etc/localtime
    echo "$TZ" > /etc/timezone
    echo "🕐 Timezone set to $TZ ($(date +%Z))"
else
    echo "⚠️  TZ not set or invalid — using UTC. Set TZ in docker-compose.yml (e.g. TZ=America/Phoenix)"
fi

# Use /etc/cron.d/ format (includes user field) — do NOT load with crontab
cat > /etc/cron.d/slugtube << EOF
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/bin
CRON_TZ=${TZ:-UTC}

${FAST_CRON} root /app/scripts/download.sh --fast >> /config/logs/slugtube.log 2>&1
${FULL_CRON} root /app/scripts/download.sh --full >> /config/logs/slugtube.log 2>&1
${UPDATE_CRON} root /app/scripts/update-ytdlp.sh >> /config/logs/slugtube.log 2>&1

EOF

chmod 0644 /etc/cron.d/slugtube

# ----- START WEB UI FIRST (so dashboard is always available) -----
echo "🌐 Starting web dashboard on port 5000..."
cd /app/web && python app.py &
WEB_PID=$!
sleep 2

echo ""
echo "=========================================="
echo " 🐌 SlugTube Ready"
echo "=========================================="
echo " 🌐 Dashboard: http://localhost:5000"
echo " Fast check:  ${FAST_CRON}"
echo " Full index:  ${FULL_CRON}"
echo " yt-dlp update: ${UPDATE_CRON}"
echo " Channels:    $(grep -cv '^\s*#\|^\s*$' /config/channels.txt 2>/dev/null || echo 0) active"
echo " Archive:     $(wc -l < /config/archive/downloaded.txt) videos tracked"
echo "=========================================="
echo ""

# ----- Seed archive from existing library (first run, in background) -----
if [ ! -f /config/archive/.seeded ]; then
    echo "🌱 First run — seeding archive from existing library (background)..."
    (
        /app/scripts/seed-archive.sh >> /config/logs/slugtube.log 2>&1
        touch /config/archive/.seeded
        echo "✅ Archive seeded. Starting initial full index..." >> /config/logs/slugtube.log
        /app/scripts/download.sh --full >> /config/logs/slugtube.log 2>&1
        touch /config/archive/.initial-done
        echo "✅ Initial index complete!" >> /config/logs/slugtube.log
    ) &
    echo "   Running in background — watch progress at http://localhost:5000/logs"
elif [ ! -f /config/archive/.initial-done ]; then
    echo "🚀 Resuming initial full index (background)..."
    (
        /app/scripts/download.sh --full >> /config/logs/slugtube.log 2>&1
        touch /config/archive/.initial-done
    ) &
else
    echo "📋 Archive already seeded ($(wc -l < /config/archive/downloaded.txt) entries)"
fi

# ----- Start cron daemon (foreground — keeps container alive) -----
echo "🕐 Cron daemon running. Waiting for next scheduled check..."
exec cron -f
