#!/usr/bin/env python3
"""
Merge ChannelName# folders into ChannelName base folders.
Moves all files from Channel# → Channel, then removes the empty Channel# folder.
Run with --dry-run first to preview changes.
"""

import os
import sys
import shutil
from pathlib import Path

SHOWS_DIR = os.environ.get("SHOWS_DIR", "/shows")
DRY_RUN = "--dry-run" in sys.argv

def main():
    shows = Path(SHOWS_DIR)
    if not shows.is_dir():
        print(f"ERROR: {SHOWS_DIR} not found")
        sys.exit(1)

    merged = 0
    moved_files = 0
    errors = 0

    for d in sorted(shows.iterdir()):
        if not d.is_dir():
            continue
        if not d.name.endswith('#'):
            continue
        # Skip #playlists folders
        if d.name.endswith('#playlists'):
            continue

        base_name = d.name.rstrip('#')
        base_path = shows / base_name

        if not base_path.is_dir():
            # Only # exists — just rename it
            if DRY_RUN:
                print(f"RENAME: {d.name} → {base_name} (no base folder)")
            else:
                try:
                    d.rename(base_path)
                    print(f"RENAMED: {d.name} → {base_name}")
                    merged += 1
                except Exception as e:
                    print(f"ERROR renaming {d.name}: {e}")
                    errors += 1
            continue

        print(f"\nMERGE: {d.name} → {base_name}")

        # Walk all files in the # folder
        for root, dirs, files in os.walk(str(d)):
            rel_root = os.path.relpath(root, str(d))
            dest_root = base_path / rel_root

            for f in files:
                src = Path(root) / f
                dest = dest_root / f

                if dest.exists():
                    print(f"  SKIP (exists): {rel_root}/{f}")
                    continue

                if DRY_RUN:
                    print(f"  WOULD MOVE: {rel_root}/{f}")
                else:
                    dest_root.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.move(str(src), str(dest))
                        print(f"  MOVED: {rel_root}/{f}")
                        moved_files += 1
                    except Exception as e:
                        print(f"  ERROR: {rel_root}/{f} — {e}")
                        errors += 1

        # Remove the now-empty # folder
        if not DRY_RUN:
            try:
                shutil.rmtree(str(d))
                print(f"  DELETED: {d.name}/")
                merged += 1
            except Exception as e:
                print(f"  ERROR deleting {d.name}: {e}")
                errors += 1
        else:
            print(f"  WOULD DELETE: {d.name}/")
            merged += 1

    print(f"\n{'DRY RUN — ' if DRY_RUN else ''}Summary: {merged} folders merged, {moved_files} files moved, {errors} errors")

if __name__ == "__main__":
    main()
