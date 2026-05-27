#!/bin/bash
# ============================================================
# SlugTube Duplicate Channel Folder Merger
# 
# Merges variant channel folders into the canonical (largest) one.
# Non-destructive: moves files, only removes empty folders after.
#
# Usage:
#   merge-duplicates.sh --dry-run   (preview only, no changes)
#   merge-duplicates.sh --go        (actually merge)
# ============================================================

SHOWS_DIR="/shows"
MODE="${1:---dry-run}"

# ── Known duplicate groups ──
# Format: "canonical|variant1|variant2|..."
# Canonical = the folder with the most videos (keep this one)
DUPES=(
    "Creeps McPasta|CreepsMcPasta"
    "CreepyPasta JR|CreepyPastaJr"
    "Dark Somnium|The Dark Somnium"
    "Dr Creepen|Dr. Creepen"
    "Haunt TV|HauntTV"
)

# ── Empty folders to remove ──
EMPTY=(
    "Chris Williamson"
    "College Humor"
    "Dropout"
    "Operation Insomnia"
)

MOVED=0
ERRORS=0

echo "🔀 SlugTube Duplicate Folder Merger"
echo "   Mode: $MODE"
echo "   Shows: $SHOWS_DIR"
echo ""

# ── Merge duplicate groups ──
for group in "${DUPES[@]}"; do
    IFS='|' read -ra FOLDERS <<< "$group"
    CANONICAL="${FOLDERS[0]}"
    CANONICAL_PATH="${SHOWS_DIR}/${CANONICAL}"

    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "📁 Canonical: ${CANONICAL}"

    if [ ! -d "$CANONICAL_PATH" ]; then
        echo "   ⚠️  Canonical folder not found! Skipping group."
        continue
    fi

    for ((i=1; i<${#FOLDERS[@]}; i++)); do
        VARIANT="${FOLDERS[$i]}"
        VARIANT_PATH="${SHOWS_DIR}/${VARIANT}"

        if [ ! -d "$VARIANT_PATH" ]; then
            echo "   ℹ️  Variant '${VARIANT}' not found, skipping."
            continue
        fi

        # Count files in variant
        FILE_COUNT=$(find "$VARIANT_PATH" -type f | wc -l)
        echo "   🔄 Merging '${VARIANT}' (${FILE_COUNT} files) → '${CANONICAL}'"

        if [ "$FILE_COUNT" -eq 0 ]; then
            if [ "$MODE" = "--go" ]; then
                rmdir "$VARIANT_PATH" 2>/dev/null && echo "   🗑️  Removed empty folder '${VARIANT}'" || true
            else
                echo "   [DRY RUN] Would remove empty folder '${VARIANT}'"
            fi
            continue
        fi

        # Move Season folders (preserve structure)
        while IFS= read -r -d '' season_dir; do
            season_name=$(basename "$season_dir")
            target_dir="${CANONICAL_PATH}/${season_name}"

            if [ "$MODE" = "--go" ]; then
                mkdir -p "$target_dir"

                # Move each file, skip if already exists at destination
                while IFS= read -r -d '' file; do
                    filename=$(basename "$file")
                    if [ -e "${target_dir}/${filename}" ]; then
                        echo "   ⚠️  Already exists, skipping: ${filename}"
                    else
                        mv "$file" "$target_dir/" && ((MOVED++)) || ((ERRORS++))
                    fi
                done < <(find "$season_dir" -type f -print0)
            else
                file_count=$(find "$season_dir" -type f | wc -l)
                echo "   [DRY RUN] Would move ${file_count} files from ${season_name}"
                MOVED=$((MOVED + file_count))
            fi
        done < <(find "$VARIANT_PATH" -mindepth 1 -maxdepth 1 -type d -print0)

        # Also move any loose files in the variant root (poster.jpg, etc.)
        while IFS= read -r -d '' file; do
            filename=$(basename "$file")
            if [ "$MODE" = "--go" ]; then
                if [ -e "${CANONICAL_PATH}/${filename}" ]; then
                    echo "   ⚠️  Root file already exists, skipping: ${filename}"
                else
                    mv "$file" "$CANONICAL_PATH/" && ((MOVED++)) || ((ERRORS++))
                fi
            else
                echo "   [DRY RUN] Would move root file: ${filename}"
                ((MOVED++))
            fi
        done < <(find "$VARIANT_PATH" -maxdepth 1 -type f -print0)

        # Remove empty variant folder tree
        if [ "$MODE" = "--go" ]; then
            # Remove now-empty subdirs, then the variant folder itself
            find "$VARIANT_PATH" -type d -empty -delete 2>/dev/null
            if [ -d "$VARIANT_PATH" ]; then
                remaining=$(find "$VARIANT_PATH" -type f | wc -l)
                if [ "$remaining" -eq 0 ]; then
                    rm -rf "$VARIANT_PATH"
                    echo "   🗑️  Removed empty variant folder '${VARIANT}'"
                else
                    echo "   ⚠️  ${remaining} files still in '${VARIANT}' — not removed"
                fi
            fi
        else
            echo "   [DRY RUN] Would remove empty variant folder '${VARIANT}'"
        fi
    done
done

echo ""

# ── Remove empty channel folders ──
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🗑️  Empty folders:"
for folder in "${EMPTY[@]}"; do
    folder_path="${SHOWS_DIR}/${folder}"
    if [ -d "$folder_path" ]; then
        file_count=$(find "$folder_path" -type f | wc -l)
        if [ "$file_count" -eq 0 ]; then
            if [ "$MODE" = "--go" ]; then
                rm -rf "$folder_path"
                echo "   ✅ Removed '${folder}'"
            else
                echo "   [DRY RUN] Would remove '${folder}' (empty)"
            fi
        else
            echo "   ⚠️  '${folder}' has ${file_count} files — NOT empty, skipping!"
        fi
    else
        echo "   ℹ️  '${folder}' not found, already gone."
    fi
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ "$MODE" = "--go" ]; then
    echo "✅ Done! Moved ${MOVED} files. Errors: ${ERRORS}"
    echo ""
    echo "📊 Remaining channel count:"
    ls -d "${SHOWS_DIR}"/*/ 2>/dev/null | wc -l
else
    echo "🔍 DRY RUN complete. Would move ~${MOVED} files."
    echo "   Run with --go to execute for real."
fi
