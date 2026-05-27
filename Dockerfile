FROM python:3.12-slim

# Install system dependencies
# - ffmpeg: video processing / merging
# - atomicparsley: thumbnail embedding in MP4
# - cron: scheduled downloads
# - jq: JSON parsing in shell scripts
# - curl/unzip: downloading tools
# - deno: required by yt-dlp nightly for YouTube JS challenges
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    atomicparsley \
    cron \
    jq \
    curl \
    unzip \
    && pip install --no-cache-dir --pre "yt-dlp[default]" flask mutagen curl_cffi \
    && curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy application code
COPY scripts/ /app/scripts/
COPY web/ /app/web/
RUN chmod +x /app/scripts/*.sh

# Create working directories
RUN mkdir -p /config/archive /config/logs /config/temp /shows

# Health check — verify web UI is responsive
HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:5000/ || exit 1

EXPOSE 5000

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
