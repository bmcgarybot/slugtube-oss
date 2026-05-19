# SlugTube — Build & Usage Guide
### Custom YouTube Archiver with Web Dashboard
*Built by Obsidian Slug 🐌 — May 2026*

---

## What It Is

SlugTube replaces PinchFlat with a simpler, more reliable YouTube archiver. It uses yt-dlp + cron inside Docker with a web dashboard for management. Downloads are Jellyfin-compatible with TV show folder structure.

---

## Requirements

- **Docker Desktop** (Windows, Mac, or Linux)
- **A media drive** with your YouTube library (e.g., `G:\Youtube\shows`)
- **YouTube cookies** (for age-restricted or members-only content)

---

## Installation

### 1. Create the SlugTube folder

```
C:\SlugTube\
```

### 2. Extract the archive

Extract `SlugTube.tar.gz` into `C:\SlugTube\`. You should have:

```
C:\SlugTube\
├── docker-compose.yml
├── Dockerfile
├── README.md
├── channels.txt          ← your channel list
├── scripts/
│   ├── download.sh
│   ├── entrypoint.sh
│   ├── seed-archive.sh
│   ├── channel-art.sh
│   └── update-ytdlp.sh
└── web/
    ├── app.py
    └── templates/
        ├── base.html
        ├── dashboard.html
        ├── channels.html
        ├── cookies.html
        ├── logs.html
        └── settings.html
```

### 3. Edit docker-compose.yml paths (if needed)

The default mount points are:

```yaml
volumes:
  - ./:/config              # C:\SlugTube config, cookies, archive, logs
  - G:\Youtube\shows:/shows  # Your media library
```

Change `G:\Youtube\shows` to wherever your library lives.

### 4. Build and start

```powershell
cd C:\SlugTube
docker compose up -d --build
```

### 5. Open the dashboard

```
http://localhost:5000
```

---

## First Run

On first start, SlugTube will:

1. **Update yt-dlp** to the latest nightly build
2. **Start the web dashboard** (available immediately)
3. **Seed the archive** — scans your existing library to avoid re-downloading videos you already have (runs in background, can take 20 min to 2 hours on large libraries)
4. **Run initial full index** — downloads new videos from all channels (runs after seeding completes)

You can watch progress on the **Logs** page.

---

## Daily Operations

### Dashboard (http://localhost:5000)

- **Dashboard** — Stats, top channels, recent activity
- **Channels** — View/add/remove YouTube channels
- **Logs** — Live log output
- **Cookies** — Paste YouTube cookies from your browser
- **Settings** — Quality profile, format, metadata toggles

### Schedule (automatic)

| Task | Schedule | What it does |
|------|----------|-------------|
| ⚡ Fast check | Every 6 hours | Scans last 50 videos per channel for new uploads |
| 📚 Full index | Sundays 3 AM | Deep scan of all videos on all channels |
| 🔄 yt-dlp update | Daily 1 AM | Updates yt-dlp to latest nightly build |

### Manual Controls

- **⚡ Fast Check** button — Run a fast check immediately
- **📚 Full Index** button — Run a full scan immediately
- **⏸️ Pause / ▶️ Resume** — Pause/resume all downloads (scheduled checks skip when paused)

---

## Adding Channels

### From the UI
Go to **Channels** → paste a YouTube URL → click **Add Channel**

### Manually
Edit `C:\SlugTube\channels.txt` — one URL per line:

```
# --- Science & Education ---
https://www.youtube.com/@smartereveryday
https://www.youtube.com/@Vsauce

# --- Music ---
https://www.youtube.com/@VEVO
```

Lines starting with `#` are comments. Section headers use `# --- Name ---` format.

---

## Cookies

Some videos require authentication (age-restricted, members-only, etc.).

1. Install a browser extension like "Get cookies.txt LOCALLY"
2. Go to YouTube while logged in
3. Export cookies
4. Go to **Cookies** page → paste → **Save**

Or manually place the file at `C:\SlugTube\Cookie\cookies.txt`

---

## File Structure

SlugTube organizes downloads as Jellyfin TV shows:

```
G:\Youtube\shows\
├── Channel Name/
│   ├── poster.jpg              ← Channel avatar
│   ├── tvshow.nfo              ← Jellyfin metadata
│   ├── Season 2024/
│   │   ├── s2024e20240115 - Video Title [dQw4w9WgXcQ].mp4
│   │   ├── s2024e20240115 - Video Title [dQw4w9WgXcQ].en.srt
│   │   ├── s2024e20240115 - Video Title [dQw4w9WgXcQ].info.json
│   │   └── s2024e20240115 - Video Title [dQw4w9WgXcQ].nfo
│   └── Season 2025/
│       └── ...
```

**Episode naming:** `s{year}e{uploaddate} - Title [videoID].ext`

This is compatible with Jellyfin alongside existing PinchFlat downloads.

---

## Settings

Configurable from the **Settings** page or `C:\SlugTube\slugtube-config.json`:

| Setting | Default | Description |
|---------|---------|-------------|
| Quality | 1080p | Max resolution (480p/720p/1080p/1440p/4K) |
| Format | mp4 | Container format (mp4/mkv/webm) |
| Embed Subs | ✅ | Embed subtitles in video file |
| Embed Metadata | ✅ | Embed title/description/tags |
| Embed Thumbnail | ✅ | Embed thumbnail in video file |
| Write NFO | ✅ | Jellyfin-compatible metadata file |
| Write Thumbnail | ✅ | Separate thumbnail image |
| Write Subs File | ✅ | Separate .srt subtitle file |
| Skip Shorts | ✅ | Skip videos under 60 seconds |

---

## Updating SlugTube

```powershell
cd C:\SlugTube
docker compose down
# Replace code files (keep Cookie/, archive/, logs/ folders)
docker compose up -d --build
```

Your data (archive, cookies, logs, config) is preserved — only code files get replaced.

---

## Troubleshooting

### Dashboard won't load
- Check Docker is running: `docker ps`
- Check logs: `docker compose logs slugtube`
- The first library scan runs in the background — dashboard shows "Scanning..." and auto-refreshes

### Downloads not working
- Check cookies are valid (YouTube cookies expire periodically)
- Check logs page for error messages
- Run a manual **⚡ Fast Check** from the dashboard

### Videos being re-downloaded
- The archive file (`C:\SlugTube\archive\downloaded.txt`) tracks completed downloads
- If it's empty or missing, run the archive seeder: the container does this automatically on first start

### Container won't start
- Make sure Docker Desktop is running
- Check the media drive is connected and the path in `docker-compose.yml` is correct
- Run `docker compose logs` to see error messages

---

## Architecture

```
┌─────────────────────────────────────────┐
│  Docker Container                       │
│                                         │
│  ┌─────────┐  ┌──────────────────────┐  │
│  │  Cron   │  │  Flask Web UI (:5000)│  │
│  │ Daemon  │  │  - Dashboard         │  │
│  │         │  │  - Channels          │  │
│  │ Fast 6h │  │  - Logs              │  │
│  │ Full Sun│  │  - Cookies           │  │
│  │ Update  │  │  - Settings          │  │
│  └────┬────┘  └──────────────────────┘  │
│       │                                 │
│  ┌────▼─────────────────────────────┐   │
│  │  download.sh + yt-dlp (nightly)  │   │
│  │  - Rate limited                  │   │
│  │  - Archive tracking              │   │
│  │  - Pause-aware                   │   │
│  └──────────────────────────────────┘   │
│                                         │
├─── /config ── C:\SlugTube\ ─────────────┤
├─── /shows ── G:\Youtube\shows\ ─────────┤
└─────────────────────────────────────────┘
```

The web UI and download scripts are independent — if the UI crashes, downloads continue via cron. If a download fails, it retries on the next scheduled run.

---

*SlugTube v10 — No UI that breaks. 🐌*
