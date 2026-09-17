# 🐌 SlugTube

**Homemade PinchFlat — no UI, never breaks.**

Same features (embedded subs, metadata, thumbnails, Jellyfin TV show structure), but it's just yt-dlp with a cron job. Nothing to crash, nothing to corrupt.

---

## Quick Start

You need Docker installed. Everything else is in this repo.

```bash
git clone https://github.com/bmcgarybot/slugtube-oss.git
cd slugtube-oss

# 1. Tell it where your files go
cp .env.example .env
#    then edit .env and set MEDIA_PATH and CONFIG_PATH

# 2. Start it
docker compose up -d

# 3. Open the dashboard
#    http://localhost:5000
```

Add channels from the dashboard, or edit `channels.txt` directly.

### Cookies

YouTube blocks a lot of downloads without a signed-in session. Export your
cookies to `cookies.txt` (the "Get cookies.txt LOCALLY" browser extension
works) and place it at `<CONFIG_PATH>/Cookie/cookies.txt`. The dashboard shows
a coloured dot for cookie health: green is fine, orange means refresh soon,
red means expired.

### Storage across more than one drive

SlugTube treats `/shows` as a single library. If one disk is not enough, mount
a second disk at the path of a specific channel inside `/shows`. There are
commented examples in `docker-compose.yml`. The app still sees one library
while the files live on two drives, which also works when the disks cannot be
pooled, for example a non-NTFS disk that StableBit DrivePool will not accept.

## What It Does

| Schedule | Action | What Happens |
|----------|--------|-------------|
| **First run** | Full index | Downloads every video from every channel (skips existing via archive) |
| **Every 6 hours** | Fast check | Checks last 50 videos per channel — catches new uploads quickly |
| **Sunday 3 AM** | Full re-index | Re-scans entire playlists to catch anything RSS missed |
| **Daily 1 AM** | yt-dlp update | Updates yt-dlp automatically so YouTube changes don't break things |

## Managing Channels

```powershell
# Add a channel — just edit the text file
notepad <CONFIG_PATH>\channels.txt

# Add a line like:
# https://www.youtube.com/@NewChannel

# Save. Done. Next scheduled check picks it up automatically.
```

## Manual Commands

```powershell
# Force a download check right now (fast — recent videos only)
docker compose exec slugtube /app/scripts/download.sh --fast

# Force a full re-index of all channels
docker compose exec slugtube /app/scripts/download.sh --full

# Manually update yt-dlp
docker compose exec slugtube /app/scripts/update-ytdlp.sh

# Check how many videos are tracked
docker compose exec slugtube wc -l /config/archive/downloaded.txt

# View logs
docker compose logs --tail 100

# Restart
docker compose restart

# Stop
docker compose down
```

## File Structure

```
<CONFIG_PATH>\
├── docker-compose.yml      ← Docker config
├── Dockerfile              ← Container build
├── channels.txt            ← YOUR CHANNELS (edit this!)
├── Cookie\
│   └── cookies.txt         ← YouTube cookies (you provide this)
├── archive\
│   └── downloaded.txt      ← Tracks every video ID downloaded
├── logs\
│   └── slugtube.log        ← Download logs
├── temp\                   ← Temporary download files
└── scripts\                ← All the automation scripts

G:\Youtube\shows\
├── Channel Name\
│   ├── tvshow.nfo          ← Jellyfin show metadata
│   ├── poster.jpg          ← Channel art
│   ├── Season 2024\
│   │   ├── s2024e20240315 - Video Title [dQw4w9].mp4
│   │   ├── s2024e20240315 - Video Title [dQw4w9].en.srt
│   │   ├── s2024e20240315 - Video Title [dQw4w9].info.json
│   │   └── s2024e20240315 - Video Title [dQw4w9].jpg
│   └── Season 2025\
└── ...
```

## How It Handles Your Existing Library

On first run, SlugTube:
1. Scans all your existing `.json` and `.info.json` files
2. Extracts every YouTube video ID
3. Pre-populates the archive file
4. **Result: nothing gets re-downloaded**

Your existing PinchFlat files (named `s2021e011292 - Title.mp4`) stay untouched. New downloads use a slightly different episode number format (`s2024e20240315`) but Jellyfin handles both perfectly in the same library.

## Cookies

YouTube requires cookies for:
- Age-restricted videos
- Member/paid content
- Sometimes to avoid rate limiting

Your cookies file should be in Netscape format at `<CONFIG_PATH>/Cookie/cookies.txt`.

To refresh cookies, use a browser extension like "Get cookies.txt LOCALLY" and export to that path.

## Troubleshooting

**"No channels.txt found"**
→ Make sure `<CONFIG_PATH>\channels.txt` exists with at least one URL

**Videos aren't downloading**
→ Check logs: `docker compose logs --tail 50`
→ Cookies might be expired — refresh them

**yt-dlp errors after YouTube changes**
→ Usually fixed by updating: `docker compose exec slugtube /app/scripts/update-ytdlp.sh`
→ Or restart the container (it updates on startup)

**Want to re-download a specific video**
→ Remove its line from `<CONFIG_PATH>\archive\downloaded.txt`
→ Run a manual check

---

*Built by Obsidian Slug 🐌 — May 2026*
