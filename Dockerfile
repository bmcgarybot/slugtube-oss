FROM python:3.12-slim

# Install system dependencies + deno (required by yt-dlp nightly for YouTube JS challenges)
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

# Copy scripts and web app
COPY scripts/ /app/scripts/
COPY web/ /app/web/
RUN chmod +x /app/scripts/*.sh

# Create working directories
RUN mkdir -p /config/archive /config/logs /config/temp /shows

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
