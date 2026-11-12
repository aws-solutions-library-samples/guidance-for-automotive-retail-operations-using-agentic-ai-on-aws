#!/bin/sh
# Sync the authoritative specs into AI-DLC DocumentKB.
#
# docs/specs/ is the human working home and single source of truth for the
# specs. AI-DLC's DocumentKB requires the originals to live under its own
# user-owned folder (knowledge/documents/), and it does NOT follow symlinks —
# `knowledge sync` materializes any symlink back into a real file. So the specs
# necessarily exist as real copies in both places.
#
# This script is the ONE defined way to reconcile them: run it after editing
# any file in docs/specs/. It copies the specs into the DocumentKB documents
# folder and re-indexes, so agents cite current content.
#
# Usage:  sh scripts/sync_specs_to_documentkb.sh
set -eu

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
SRC="$ROOT/docs/specs"
DEST="$ROOT/aidlc/spaces/default/knowledge/documents/specs"

[ -d "$SRC" ] || { echo "error: $SRC not found" >&2; exit 1; }

mkdir -p "$DEST"

# These destination subdirectories are mirrors maintained by this script. Remove
# a copied Markdown document when its canonical source was deleted so
# `knowledge sync` can tombstone it instead of continuing to index stale text.
remove_stale_markdown() {  # remove_stale_markdown <src-dir> <dest-dir>
  source_dir="$1"; dest_dir="$2"
  for copied in "$dest_dir"/*.md; do
    [ -e "$copied" ] || break
    name=$(basename "$copied")
    if [ ! -e "$source_dir/$name" ]; then
      rm -f "$copied"
      echo "  removed stale $name"
    fi
  done
}

remove_stale_markdown "$SRC" "$DEST"

echo "Copying specs from docs/specs/ into DocumentKB documents/ ..."
for f in "$SRC"/*.md; do
  [ -e "$f" ] || { echo "error: no .md files in $SRC" >&2; exit 1; }
  cp "$f" "$DEST/"
  echo "  $(basename "$f")"
done

# Beyond the specs, index the documents that describe the build as it is, so
# agents can cite the current state and the reasoning as well as the intent:
#   docs/as-built/   bottom-up architecture, use cases, and agent flows
#   docs/adr/        decision records
#   docs/external/   documentation for external audiences (customers, partners, evaluators)
#   docs/DEPLOYMENT.md, docs/DEMO.md, docs/ACCEPTANCE.md, docs/ACCESS_BY_PERSONA.md, docs/QUICK_BUILD.md
copy_dir() {  # copy_dir <src-dir> <dest-subdir> <label>
  src="$1"; dest="$ROOT/aidlc/spaces/default/knowledge/documents/$2"
  [ -d "$src" ] || return 0
  mkdir -p "$dest"
  echo "Copying $3 into DocumentKB documents/$2/ ..."
  remove_stale_markdown "$src" "$dest"
  for f in "$src"/*.md; do
    [ -e "$f" ] || break
    cp "$f" "$dest/"
    echo "  $(basename "$f")"
  done
}
copy_dir "$ROOT/docs/as-built" "as-built" "as-built docs"
copy_dir "$ROOT/docs/adr"      "adr"      "decision records"
copy_dir "$ROOT/docs/external" "external" "external-audience docs"

GUIDES_DEST="$ROOT/aidlc/spaces/default/knowledge/documents/guides"
mkdir -p "$GUIDES_DEST"
echo "Copying operator guides into DocumentKB documents/guides/ ..."
for name in DEPLOYMENT.md DEMO.md ACCEPTANCE.md ACCESS_BY_PERSONA.md QUICK_BUILD.md; do
  if [ -e "$ROOT/docs/$name" ]; then
    cp "$ROOT/docs/$name" "$GUIDES_DEST/"
    echo "  $name"
  fi
done

# Locate the aidlc binary (user-local install or on PATH).
AIDLC=$(command -v aidlc 2>/dev/null || true)
[ -n "$AIDLC" ] || [ ! -x "$HOME/.local/bin/aidlc" ] || AIDLC="$HOME/.local/bin/aidlc"
if [ -z "$AIDLC" ]; then
  echo "error: 'aidlc' not found on PATH or in ~/.local/bin" >&2
  echo "install AI-DLC first, then re-run." >&2
  exit 1
fi

echo "Re-indexing DocumentKB ..."
"$AIDLC" engine knowledge sync

echo "Done. Review with: $AIDLC engine knowledge list"
