# SlugTube (OSS) — Session Update Notes (2026-07-01)
- Sync Archive REWRITTEN: exact 11-char YouTube IDs; unions indexer DB +
  [bracketed] filenames + bare-ID filenames (TubeArchivist style). Old 8-15
  char before-extension regex missed real IDs and archived junk like
  [HDTV-1080p] — why counts never matched the library.
- Self-update: pulls PUBLIC slugtube-oss tarball (old private-repo URL 404'd
  even for the owner). Self-update NO LONGER touches channels.txt or ANY user
  data — code-only (/app/web + /app/scripts). Library/DB/history/archive are
  never modified by updates.
- yt-dlp --no-progress added (log spam); [DEBUG] lines gated behind
  SLUGTUBE_DEBUG=1 (shell + Python).
- fix-update.bat: copies ENTIRE web/ + scripts/ dirs (old hand-list of 9
  files shipped stale UI for months). Step 4 still copies repo channels.txt
  over runtime (owner's design — pending decision to keep/merge/prompt).
- OPEN: settings-page tab redesign, dashboard button grouping, TubeArchivist/
  PinchFlat importer verification, optional auth token, requirements.txt,
  config backup rotation (pending yes), ghcr publish workflow parked in
  slugtube-oss/docs/setup (needs 30-sec web-UI move; push token lacked
  workflow scope).
