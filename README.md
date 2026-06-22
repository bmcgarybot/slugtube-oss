# SlugTube

### Your YouTube library, your rules. No cloud. No subscriptions. No compromises.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-green.svg)](https://python.org)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](https://docker.com)
[![yt-dlp](https://img.shields.io/badge/Powered%20by-yt--dlp-red.svg)](https://github.com/yt-dlp/yt-dlp)

SlugTube is a self-hosted YouTube channel archiver with a fully-featured dark-themed web UI. One `docker compose up` gives you automated downloads, a searchable video library, a built-in player with resume, playlist management, and Jellyfin/Kodi-ready output — all running on your own hardware.

**14,000+ lines of code. 70 API endpoints. 14 pages. Zero dependencies on anyone else's servers.**

> Built as a modern alternative to PinchFlat and TubeArchivist — simpler to deploy, easier to use, and designed to get out of your way.

---

## Why SlugTube?

| | SlugTube | TubeArchivist | PinchFlat |
|---|---------|---------------|-----------|
| **Setup** | `docker compose up` | Requires Elasticsearch + Redis | Requires Elixir runtime |
| **Dependencies** | Flask + SQLite | 3 containers minimum | PostgreSQL |
| **Search** | SQLite FTS5 (instant) | Elasticsearch (heavy) | Basic |
| **Live indexing** | Videos appear instantly | Requires re-scan | Requires re-scan |
| **Cookie management** | Built-in web UI | Manual file edit | Manual file edit |
| **Import from others** | TubeArchivist + PinchFlat import | N/A | N/A |
| **Resource usage** | ~50MB RAM | ~2GB+ RAM | ~500MB RAM |
| **Bulk operations** | Select, delete, exclude | Limited | Limited |
| **Self-update** | One click from Settings | Manual | Manual |

---

## Features

### Dashboard
- Live download status with progress indicators
- Job queue with current/pending downloads
- Disk usage stats per drive
- Cookie health indicator with smart log analysis
- Recent activity feed
- Pause/Resume all downloads globally
- Cancel running downloads and clear the queue
- **Sync Archive** — detects video files missing from the archive and adds their IDs so yt-dlp won't re-download them
- **Merge # Folders** — finds `ChannelName#` duplicate folders created by YouTube's naming and merges them into the correct folder

### Channels
- Unified channel table: Active (subscribed + on disk), Subscribed (pending), Library-only (untracked)
- Status badges and category columns
- All channel actions in one row — no page reloads:
  - **Quick Check** — scan for new videos (AJAX, stays on page)
  - **Download** — trigger full channel download
  - **Reindex** — rebuild database entries from disk
  - **Reindex Playlists** — fetch playlist data from YouTube
  - **Fetch Art** — grab poster/banner artwork
  - **Unsubscribe** — remove from active downloads
  - **Delete** — remove channel and all files
- All actions use fetch/AJAX with toast notifications — page never reloads
- Confirmation modals with hover tooltips on destructive actions

### Channel Detail View
- Video grid with thumbnails, titles, dates, durations, view counts
- Sort by: Newest, Oldest, Most Viewed, Shortest, Smallest
- Pagination for large libraries
- Latest video preview with thumbnail
- Playlist link showing count of indexed playlists
- Bulk select mode: select all, delete selected, exclude from future downloads

### Video Player
- Built-in web player with resume playback
- Watch history tracking (watched/in-progress)
- Keyboard shortcuts for playback control
- Add to playlist functionality

### Library
- Full channel grid with poster art
- Browse all videos across all channels
- Full-text search (SQLite FTS5) across video titles and descriptions

### Global Search Bar
- Search input in the sidebar, accessible from every page
- Type and hit Enter — routes to Library with filtered results
- Always visible, no need to navigate to a specific page first

### Playlist Support
- Index a channel's curated YouTube playlists
- Browse videos organized by playlist
- Playlist overview page across all channels
- Smart URL detection — playlist URLs don't get mangled

### Cookie Manager
- Paste and manage YouTube cookies from the web UI
- Tab-fix auto-repair for common formatting issues
- Smart health indicator:
  - Checks cookie file age and expiration
  - Scans yt-dlp logs for YouTube rejection signals
  - Ignores errors from before the last cookie refresh (10-minute grace period)
  - Color-coded dot in sidebar: green (OK), yellow (warning), red (rejected)

### Settings
- Quality profiles: 720p, 1080p, 1440p, 4K
- Format selection (mp4, mkv, webm)
- Metadata toggles (subtitles, thumbnails, descriptions)
- Ghost cleanup: removes database entries for channels no longer in your subscription list
- Self-update: pull latest code from GitHub directly from the UI

### Logs
- Real-time log viewer
- Filter tabs: All / Errors Only / Downloads / Skipped
- Full log view (not just last 200 lines)
- Export logs as timestamped `.txt` file
- Smart error filtering that strips noise

### Watch History
- Track watched and in-progress videos
- Resume positions saved per video
- Recently watched page

### Live Indexing
- Videos are indexed into the database immediately after download completes
- Uses yt-dlp's `--exec after_move` hook to POST to the indexing API
- No need to wait for a full reindex — new videos appear instantly

### Responsive Design
- Full desktop layout with sidebar navigation
- Medium breakpoint with hidden columns
- Mobile card layout
- Dark theme throughout

### Scheduled Automation
- Fast check every 6 hours (latest 50 videos per channel)
- Full index Sundays at 3 AM
- yt-dlp auto-update daily at 1 AM
- All schedules configurable via environment variables

### Safety Features
- Blank/malformed channel entries are skipped (won't crash downloads)
- Channels with `#` in their YouTube-assigned name are handled correctly
- Local `channels.txt` is never overwritten by updates
- Confirmation modals on all destructive actions
- Ghost cleanup only removes DB entries, not files (unless folder is empty)
- Download script handles folder names with spaces using properly quoted arguments
- SQLite connections use 30-second timeout to prevent "database is locked" errors during heavy use

---

## Walkthrough: Your First Hour with SlugTube

This takes you from zero to a working media library, step by step.

### Step 1: Install and Launch

```bash
git clone https://github.com/bmcgarybot/slugtube-oss.git
cd slugtube-oss
cp channels.txt.example channels.txt
docker compose up -d
```

Open `http://localhost:5000` in your browser. You'll see the **Dashboard** — it's empty right now, and that's fine.

### Step 2: Add Your First Channel

Click **Channels** in the sidebar. You'll see an empty table with an input bar at the top.

1. Paste a YouTube channel URL (e.g. `https://www.youtube.com/@veritasium/videos`)
2. Give it a folder name (e.g. `Veritasium`)
3. Click **Add Channel**

The channel appears in the table with an "Active" status badge. A folder is automatically created on disk.

### Step 3: Set Up Cookies (Recommended)

YouTube blocks unauthenticated downloads quickly. Click **Cookies** in the sidebar.

1. Install the [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) browser extension
2. Go to youtube.com while logged in
3. Click the extension to export your cookies
4. Paste them into the text box on the Cookies page and save

The cookie health dot next to the SlugTube logo in the sidebar will turn green when cookies are valid.

### Step 4: Download Your First Videos

Go back to **Channels**. Find your channel in the table and click **Quick Check**. A toast notification pops up confirming the scan started — the page stays put, no redirect.

Switch to the **Dashboard** to watch progress. You'll see:
- Active download count
- Current video being downloaded
- Download speed and progress

Videos appear in the library as soon as each one finishes downloading (live indexing — no need to wait for the full batch).

### Step 5: Browse Your Library

Once some videos are downloaded, click on a channel name to see the **Channel View**:
- Video grid with thumbnails, titles, dates, durations, and view counts
- Sort by Newest, Oldest, Most Viewed, Shortest, or Smallest
- Paginate through large collections

Or use the **Search Bar** at the top of the sidebar — type any keyword and hit Enter to search across all videos in your library.

### Step 6: Watch Something

Click any video thumbnail to open the **Watch** page:
- Built-in web player
- Resume where you left off (position saved automatically)
- Keyboard shortcuts for playback

Your watch history appears on the **History** page in the sidebar.

### Step 7: Clean Up What You Don't Want

On any channel page, click **Select** to enter bulk mode:
- Check individual videos or **Select All**
- **Delete Selected** removes files AND blocks re-download
- **Exclude from Future** blocks future downloads without deleting what's already there

Great for removing shorts, intros, or content that doesn't belong in your library.

### Step 8: Set It and Forget It

SlugTube runs on autopilot:
- **Every 6 hours** — fast check for new videos across all channels
- **Sundays at 3 AM** — full index of complete channel histories
- **Daily at 1 AM** — yt-dlp auto-updates to the latest version

All schedules are configurable via environment variables. Point Jellyfin or Kodi at your shows directory and your media library stays current automatically.

### Step 9: Ongoing Maintenance

- **Cookie health dot turns yellow/red?** Refresh your cookies on the Cookies page
- **Channel disappeared from YouTube?** Use **Clean Ghost Entries** on Settings to remove stale DB records
- **Want the latest SlugTube code?** Click **Update SlugTube** on Settings (pulls from GitHub)
- **Check the Logs page** for download errors, skipped videos, or connection issues

### Optional: Import an Existing Library

Already have videos from TubeArchivist, PinchFlat, or plain yt-dlp? Go to **Settings** and use the import tools:
- **Scan Existing Library** — reads `.info.json` files from any folder
- **TubeArchivist Backup** — upload a backup ZIP
- **PinchFlat Database** — upload your `pinchflat.db`

All imports detect duplicates and never overwrite your existing channels.

---

## Quick Start

```bash
# Clone the repo
git clone https://github.com/bmcgarybot/slugtube-oss.git
cd slugtube-oss

# Copy the example channels file
cp channels.txt.example channels.txt
# Edit channels.txt — add your YouTube channel URLs

# Start SlugTube
docker compose up -d

# Open the dashboard
open http://localhost:5000
```

On first run, SlugTube will:
1. Create default config files
2. Show a "Getting Started" page if no channels are configured
3. Update yt-dlp to the latest nightly build
4. Start the web dashboard on port 5000

## Configuration

All configuration is done through environment variables in `docker-compose.yml` or `.env`.

### Required Paths

| Variable | Default | Description |
|----------|---------|-------------|
| `SHOWS_DIR` | `/shows` | Where videos are stored (mount your media drive here) |
| `CONFIG_DIR` | `/config` | Config, database, logs, cookies, archive |

### Schedule (Cron Expressions)

| Variable | Default | Description |
|----------|---------|-------------|
| `FAST_CRON` | `0 */6 * * *` | Fast check — latest 50 videos per channel (every 6h) |
| `FULL_CRON` | `0 3 * * 0` | Full index — entire channel history (Sundays 3 AM) |
| `UPDATE_CRON` | `0 1 * * *` | yt-dlp auto-update (daily 1 AM) |

### Other

| Variable | Default | Description |
|----------|---------|-------------|
| `TZ` | `UTC` | Container timezone |
| `WEB_PORT` | `5000` | Web UI port (inside container) |
| `PUID` | `1000` | User ID for file permissions |
| `PGID` | `1000` | Group ID for file permissions |

## Adding Channels

### From the Web UI

1. Go to the **Channels** page
2. Paste a YouTube channel URL (e.g. `https://www.youtube.com/@ChannelName/videos`)
3. Optionally set a display name and enable playlist indexing
4. Click **Add Channel**

### From `channels.txt`

Edit `channels.txt` in your config directory:

```
# Format: URL|FolderName
https://www.youtube.com/@SomeChannel/videos|My Channel Name
https://www.youtube.com/@AnotherOne/videos|Another Channel

# Playlists work too:
https://youtube.com/playlist?list=PLxxxxxxxx|Playlist Name
```

### Migrating from TubeArchivist or PinchFlat

SlugTube has built-in import tools on the **Settings** page:

1. **Scan Existing Library** — Point SlugTube at a folder of yt-dlp downloads. It reads `.info.json` files to find channels.
2. **TubeArchivist Backup** — Upload a TubeArchivist backup ZIP.
3. **PinchFlat Database** — Upload your `pinchflat.db` file.

All import methods detect duplicates and never overwrite existing channels.

## Cookies

YouTube requires cookies for age-restricted content and to avoid bot detection.

1. Install [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) in Chrome/Edge
2. Go to youtube.com (logged in)
3. Export cookies with the extension
4. Paste them on the **Cookies** page in SlugTube

The cookie health dot in the sidebar tells you when cookies need refreshing — it checks both the file itself and recent download logs for YouTube rejection signals.

## Folder Structure

SlugTube organizes downloads for Jellyfin/Kodi compatibility:

```
/shows/
├── Channel Name/
│   ├── poster.jpg
│   ├── banner.jpg
│   ├── tvshow.nfo
│   ├── Season 2024/
│   │   ├── s2024e20240315 - Video Title [VIDEO_ID].mp4
│   │   ├── s2024e20240315 - Video Title [VIDEO_ID].info.json
│   │   ├── s2024e20240315 - Video Title [VIDEO_ID].jpg
│   │   └── s2024e20240315 - Video Title [VIDEO_ID].en.srt
│   └── Season 2025/
│       └── ...
└── Another Channel/
    └── ...
```

## Bulk Operations

On any channel page, click **Select** to enter bulk mode:

- **Select All** — toggle all visible videos
- **Delete Selected** — removes files and blocks re-download
- **Exclude from Future** — blocks future downloads without deleting
- **Sort by Smallest/Shortest** — find junk content fast

## Tech Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| **Backend** | Python / Flask | Simple, readable, no framework magic |
| **Database** | SQLite + FTS5 | Zero config, full-text search built in |
| **Downloads** | yt-dlp + ffmpeg | Industry standard, constantly updated |
| **Frontend** | Vanilla HTML/CSS/JS | No React, no build step, no node_modules |
| **Deployment** | Docker | One command, runs anywhere |
| **Media output** | Jellyfin/Kodi compatible | Season folders, NFO files, embedded metadata |

### Architecture

```
Docker Container
├── Flask Web Server (port 5000)
│   ├── 57 API endpoints
│   ├── 13 page templates
│   └── SQLite database + FTS5 search index
├── yt-dlp download engine
│   ├── --exec after_move hook → live indexing API
│   └── Archive tracking (no re-downloads)
├── Cron scheduler
│   ├── Fast check (6h) — latest 50 videos/channel
│   ├── Full index (weekly) — complete channel history
│   └── yt-dlp auto-update (daily)
└── Channel art fetcher (poster + banner scraping)
```

## Pages

| Page | Path | Description |
|------|------|-------------|
| Dashboard | `/` | Status overview, controls, recent activity |
| Channels | `/channels` | Channel management table with all actions |
| Channel View | `/channel/<name>` | Video grid for a specific channel |
| Watch | `/watch/<id>` | Video player with resume |
| History | `/history` | Recently watched videos |
| Library | `/library` | Browse all videos, full-text search |
| Playlists | `/playlists` | Overview of all indexed playlists |
| Recent | `/recent` | Recently downloaded videos |
| Logs | `/logs` | Download logs with filters |
| Cookies | `/cookies` | Cookie management |
| Settings | `/settings` | Quality, imports, cleanup, updates |

## Development

```bash
# Run without Docker (Python 3.10+, yt-dlp, ffmpeg required)
cd web
pip install flask
python app.py

# Environment variables:
export SHOWS_DIR=/path/to/your/shows
export CONFIG_DIR=/path/to/your/config
```

## Contributing

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/cool-thing`)
3. Commit your changes
4. Push and open a Pull Request

## License

[MIT](LICENSE) — do whatever you want with it.

---

## Star History

If SlugTube saves you from another TubeArchivist migration headache, consider starring the repo. It helps others find it.

---

*14,406 lines of code. 70 endpoints. 14 pages. 10 automation scripts. Zero JavaScript frameworks. Built with yt-dlp, Flask, SQLite, and stubbornness.*
