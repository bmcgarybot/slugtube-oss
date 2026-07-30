# SlugTube — what's new, and the one-time catch-up

Two parts: a **do this once** list to bring existing data up to date, then a
reference for the new features.

---

## Part 1 — Do this once, in this order

Order matters. Each step depends on the one before it.

### 0. After pulling: hard-refresh the browser once
`Cmd/Ctrl + Shift + R`

Static JS is now cache-busted automatically, but the copy already sitting in
your browser needs one manual clear. If the UI looks unchanged after an
update, this is almost always why.

### 1. Repair video IDs — dry run first
Videos imported without metadata (TubeArchivist's `<channel-id>/<video-id>.mp4`,
or Pinchflat files whose output template omits the ID) were stored with a
placeholder ID like `f_a1b2c3d4e5`. Those can't be archived and can't match
playlists.

```
curl -X POST http://<host>:5000/api/repair-video-ids
```

Read the report: `scanned`, `recoverable`, `unresolved`, plus sample
before/after IDs. Nothing is changed yet. When it looks right:

```
curl -X POST "http://<host>:5000/api/repair-video-ids?apply=true"
```

Watch history and playlist links follow each ID across the rename. Videos with
no recoverable ID are left alone.

**Do this before step 2** — playlists match on real YouTube IDs, so repairing
first means more videos link up.

### 2. Fix playlist flags
Settings → **Playlist Maintenance** → **Fix Flags**

Instant and local, no YouTube requests. Flags every channel that already has
playlists indexed, so the scheduled refresh stops skipping them. (Channels
indexed with the old one-off button were never flagged.)

### 3. Populate playlists
**This is the step that makes empty playlists fill in.**

- One channel: its playlist page → **Reindex Playlists**
- All flagged channels: Settings → **Refresh Flagged Now**

Every video you already have gets linked into its playlists during this run.
Videos you *don't* have yet are remembered (see "playlist roster" below) and
link themselves automatically once downloaded — no further action.

For a channel with hundreds of playlists (A&E has 482) this takes a while and
hits YouTube once per playlist. It's cancellable via **Cancel All**.

### 4. Sync the archive
Dashboard → **Sync Archive**

Adds every known video ID to the never-re-download ledger. Expect a real
number now; it used to silently do nothing. Check Logs for
`📦 Archive sync: … added N`.

---

## Part 2 — New features

### Finding things
- **In-channel search** — search box on the channel page filters that
  channel's videos as you type. Word-based, so `48 hours` matches
  "48 Hours: The Missing Woman" and "Live PD Presents 48 hours of chaos".
- **Duration filter** — presets (under 2 min, 2–10, 10–30, 30–60, over 30,
  over 60) plus a custom min/max range.
- Both **compose**: `48 hours` + `Over 30 min` = just the full episodes.
- With any filter active the grid shows **all** matches rather than paginating,
  so **Select All really means all of them**. Without that, Select All would
  silently grab only the visible 60.

### Selecting many at once
- **Select** toggle → checkboxes, plus Select All / Select None.
- **Shift-click a range**: click one video, Shift-click another, everything
  between is selected. Works everywhere.
- **Drag a box** over the grid also selects (Pointer Events based).
- Playlist detail pages have the same multi-select, where **Remove only takes
  videos out of the playlist — it never deletes files.**

### Two useful workflows
- **Build a themed playlist**: search `48 hours` → Over 30 min → Select →
  Select All → Add to Playlist.
- **Sweep out junk clips**: no search → Under 2 min → Select → Select All →
  Delete Selected.

### Playlists
- **Never deletes.** Playlist indexing is purely additive. If YouTube drops a
  playlist, renames it, removes a video, or a fetch fails halfway, nothing you
  already had is removed.
- **Playlist roster** — every video YouTube reports for a playlist is
  remembered, including ones not downloaded yet. Each video index links
  whatever has since arrived, locally, with zero YouTube requests. Downloads
  populate playlists on their own.
- **Scheduled refresh** — Settings → Playlist Indexing. Defaults to daily at
  4am; interval and on/off are configurable like the other schedules.
- Manual **Reindex Playlists** now also flags the channel, so anything you
  index once stays tracked.

### Playback
- **Download to device** — `⭳ Download` on the watch page, and on hover in the
  channel grid. Serves the original file with a proper `<title>.<ext>` name.
- **Autoplay** no longer stalls: the countdown can't run past zero, falls back
  to the next video in the playlist, and closes itself if there genuinely is
  no next video.
- **Fullscreen survives autoplay.** In fullscreen, the next video loads into
  the same player instead of navigating — a page load always drops fullscreen
  and browsers won't let a script restore it without a click.

### Reliability
- **Cancel All** now stops downloads, the file scan, **and** playlist indexing
  (that last one is the loop that hits YouTube). It stops at the next channel
  or playlist boundary, so expect a few more log lines, not an instant halt.
- **Database lock errors** that silently skipped channels during a scan are
  fixed: the scan commits periodically instead of holding the write lock for a
  whole channel.
- **API errors return JSON**, never an HTML error page. The old behavior caused
  `Unexpected token '<' … is not valid JSON`. Lock contention now says
  "the library is busy — try again in a moment".
- **Static files are cache-busted** by modification time, so code updates
  actually take effect.

### Imports
Verified against current TubeArchivist and Pinchflat docs:
- TubeArchivist `<channel-id>/<video-id>.mp4` bare-ID filenames are recognized.
- Pinchflat templates without an ID in the filename are read from the
  `.info.json` sidecar.
- Sync Archive reads sidecars too, so those files become archivable.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| UI looks unchanged after update | Hard-refresh once (`Cmd/Ctrl+Shift+R`) |
| Playlists show 0 videos | Run Part 1 steps 1–3, in order |
| `Unexpected token '<'` | Fixed; if seen, a scan is running — retry shortly |
| Channels skipped during scan | Fixed (DB locking). Re-run the scan |
| Cancel All doesn't stop it | Stops at the next channel/playlist boundary |
| Sync Archive adds 0 | Correct if the archive already covers everything. Check Logs for the count |
| Archive smaller than library | Expected: videos without a known YouTube ID can't be archived. Run step 1 to recover IDs |
