#!/usr/bin/env python3
"""
Archive Audit for SlugTube (v2)
Cross-references downloaded.txt against actual files on disk.
Finds phantom entries (in archive but no file) and orphan files (on disk but not in archive).

Supports BOTH naming conventions:
  - SlugTube: "Title [VIDEO_ID].mp4" (ID extracted from filename)
  - PinchFlat/TubeArchivist: "s2022e020495 - Title.mp4" (ID extracted from .info.json)

Usage as endpoint: POST /api/audit-archive  (read-only report)
                   POST /api/rebuild-archive?confirm=true  (backup + rebuild)
Usage standalone:  python3 archive_audit.py /shows /config/archive/downloaded.txt
"""

import json
import os
import re
import sys
import shutil
from datetime import datetime
from pathlib import Path


# Pattern for SlugTube-style filenames: Title [VIDEO_ID].ext
YT_ID_PATTERN = re.compile(r'\[([a-zA-Z0-9_-]{8,15})\]\.[a-zA-Z0-9]+$')
VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.webm', '.avi', '.mov', '.m4v')


def load_archive(archive_path):
    """Load all video IDs from the archive file."""
    ids = set()
    try:
        with open(archive_path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    parts = line.split()
                    if len(parts) >= 2:
                        ids.add(parts[1])
                    else:
                        ids.add(parts[0])
    except FileNotFoundError:
        pass
    return ids


def scan_disk(shows_dir):
    """Scan all video files on disk and extract YouTube IDs.

    Two strategies:
      1. [VIDEO_ID] in the filename (SlugTube naming)
      2. Matching .info.json sidecar file (PinchFlat/TubeArchivist naming)

    Returns dict mapping video_id -> file_path for traceability.
    """
    found = {}
    scanned = 0
    no_id = 0

    for root, dirs, files in os.walk(shows_dir):
        for fname in files:
            if not fname.endswith(VIDEO_EXTENSIONS):
                continue

            scanned += 1
            full_path = os.path.join(root, fname)

            # Strategy 1: [VIDEO_ID] in filename
            match = YT_ID_PATTERN.search(fname)
            if match:
                vid_id = match.group(1)
                if vid_id not in found:
                    found[vid_id] = full_path
                continue

            # Strategy 2: Check for .info.json sidecar
            base_no_ext = os.path.splitext(full_path)[0]
            info_json_path = base_no_ext + ".info.json"

            if os.path.isfile(info_json_path):
                try:
                    with open(info_json_path, "r", errors="replace") as jf:
                        info = json.load(jf)
                    vid_id = info.get("id", "")
                    if vid_id and len(vid_id) >= 8:
                        if vid_id not in found:
                            found[vid_id] = full_path
                        continue
                except (json.JSONDecodeError, OSError):
                    pass

            no_id += 1

    return found, scanned, no_id


def audit(shows_dir, archive_path):
    """Run the audit. Returns a report dict."""
    archive_ids = load_archive(archive_path)
    disk_files, scanned, no_id = scan_disk(shows_dir)
    disk_ids = set(disk_files.keys())

    # Phantom: in archive but NOT on disk
    phantom_ids = archive_ids - disk_ids

    # Orphan: on disk but NOT in archive
    orphan_ids = disk_ids - archive_ids

    # Matched: in both
    matched_ids = archive_ids & disk_ids

    # Sample some phantoms
    phantom_sample = sorted(phantom_ids)[:50]

    # Sample some orphans with paths
    orphan_sample = []
    for vid_id in sorted(orphan_ids)[:50]:
        orphan_sample.append({
            "id": vid_id,
            "path": disk_files[vid_id]
        })

    return {
        "timestamp": datetime.now().isoformat(),
        "archive_path": archive_path,
        "shows_dir": shows_dir,
        "files_scanned": scanned,
        "files_with_id": scanned - no_id,
        "files_without_id": no_id,
        "archive_total": len(archive_ids),
        "disk_total": len(disk_ids),
        "matched": len(matched_ids),
        "phantoms": len(phantom_ids),
        "orphans": len(orphan_ids),
        "phantom_sample": phantom_sample,
        "orphan_sample": orphan_sample,
        "summary": (
            f"Archive has {len(archive_ids):,} entries. "
            f"Disk has {len(disk_ids):,} videos with IDs "
            f"({no_id:,} files had no ID). "
            f"{len(matched_ids):,} match. "
            f"{len(phantom_ids):,} phantom entries (archive only, no file). "
            f"{len(orphan_ids):,} orphan files (on disk, not in archive)."
        )
    }


def rebuild_archive(shows_dir, archive_path, dry_run=True):
    """Rebuild archive from disk only. Backs up old archive first."""
    report = audit(shows_dir, archive_path)

    if dry_run:
        report["action"] = "dry_run"
        report["message"] = (
            f"Would remove {report['phantoms']:,} phantom entries. "
            f"Would keep {report['matched']:,} matched entries. "
            f"Would add {report['orphans']:,} orphan IDs. "
            f"New archive would have {report['matched'] + report['orphans']:,} entries "
            f"(down from {report['archive_total']:,})."
        )
        return report

    # Back up current archive
    backup_path = archive_path + f".bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    if os.path.exists(archive_path):
        shutil.copy2(archive_path, backup_path)

    # Scan disk for all IDs
    disk_files, _, _ = scan_disk(shows_dir)

    # Write new archive with only IDs that have files on disk
    os.makedirs(os.path.dirname(archive_path), exist_ok=True)
    with open(archive_path, "w") as f:
        for vid_id in sorted(disk_files.keys()):
            f.write(f"youtube {vid_id}\n")

    new_count = len(disk_files)
    report["action"] = "rebuilt"
    report["backup_path"] = backup_path
    report["new_archive_total"] = new_count
    report["message"] = (
        f"Archive rebuilt. Backed up to {backup_path}. "
        f"Old: {report['archive_total']:,} entries. "
        f"New: {new_count:,} entries. "
        f"Removed {report['phantoms']:,} phantoms. "
        f"Added {report['orphans']:,} orphans."
    )
    return report


# -- Flask endpoint registration --

def register_audit_routes(app, archive_file, shows_dir="/shows"):
    """Register audit endpoints on the Flask app."""
    from flask import jsonify, request

    @app.route("/api/audit-archive", methods=["POST"])
    def api_audit_archive():
        """Audit archive vs disk. Returns report with phantom/orphan counts."""
        return jsonify(audit(shows_dir, archive_file))

    @app.route("/api/rebuild-archive", methods=["POST"])
    def api_rebuild_archive():
        """Rebuild archive from disk. ?confirm=true to execute, otherwise dry-run."""
        dry_run = request.args.get("confirm", "").lower() != "true"
        result = rebuild_archive(shows_dir, archive_file, dry_run=dry_run)
        return jsonify(result)


# -- Standalone CLI --

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 archive_audit.py /shows /config/archive/downloaded.txt [--rebuild]")
        sys.exit(1)

    shows = sys.argv[1]
    archive = sys.argv[2]
    do_rebuild = "--rebuild" in sys.argv

    print(f"Scanning {shows} (this may take a minute with .info.json parsing)...")
    result = audit(shows, archive)

    print()
    print("=" * 60)
    print(" SlugTube Archive Audit v2")
    print("=" * 60)
    print(f"  Files scanned:      {result['files_scanned']:>8,}")
    print(f"  Files with ID:      {result['files_with_id']:>8,}")
    print(f"  Files without ID:   {result['files_without_id']:>8,}")
    print(f"  Archive entries:    {result['archive_total']:>8,}")
    print(f"  Disk IDs found:     {result['disk_total']:>8,}")
    print(f"  Matched:            {result['matched']:>8,}")
    print(f"  Phantoms (no file): {result['phantoms']:>8,}")
    print(f"  Orphans (no entry): {result['orphans']:>8,}")
    print("=" * 60)
    print()

    if result['files_without_id'] > 0:
        print(f"NOTE: {result['files_without_id']:,} video files had no ID")
        print("(no [VIDEO_ID] in filename AND no .info.json sidecar)")
        print()

    if result['phantom_sample']:
        show_count = min(20, len(result['phantom_sample']))
        print(f"Sample phantom IDs (first {show_count} of {result['phantoms']:,}):")
        for pid in result['phantom_sample'][:show_count]:
            print(f"  youtube {pid}")
        if result['phantoms'] > show_count:
            print(f"  ... and {result['phantoms'] - show_count:,} more")
        print()

    if result['orphan_sample']:
        show_count = min(10, len(result['orphan_sample']))
        print(f"Sample orphan files (first {show_count} of {result['orphans']:,}):")
        for o in result['orphan_sample'][:show_count]:
            print(f"  {o['id']} -> {o['path']}")
        print()

    if do_rebuild:
        print("Rebuilding archive from disk...")
        rebuild_result = rebuild_archive(shows, archive, dry_run=False)
        print(rebuild_result['message'])
    else:
        dry = rebuild_archive(shows, archive, dry_run=True)
        print(dry['message'])
        print()
        print("To execute: python3 archive_audit.py /shows /config/archive/downloaded.txt --rebuild")
