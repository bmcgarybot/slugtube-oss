# SlugTube

**A self-hosted YouTube channel archiver with a dark-themed web UI.**

SlugTube automatically downloads, organizes, and indexes YouTube channels into a Jellyfin/Kodi-compatible library. Think PinchFlat or TubeArchivist, but simpler — one `docker compose up` and you're archiving.

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

- **Backend:** Python / Flask
- **Database:** SQLite with FTS5 full-text search
- **Downloads:** yt-dlp with ffmpeg
- **Frontend:** Vanilla HTML/CSS/JS (no frameworks)
- **Deployment:** Docker
- **Media server:** Jellyfin/Kodi compatible output

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

*Built with yt-dlp, Flask, SQLite, and stubbornness.*
