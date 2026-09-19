#!/usr/bin/env bash
# Regression: preserve Bookshelf's original while producing one optimized copy.
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
tmp=$(mktemp -d)
cleanup() { rm -rf "$tmp"; }
trap cleanup EXIT

original_dir="$tmp/bookshelf-original"
inbox="$tmp/optimizer-inbox"
output="$tmp/optimized-output"
destination="$tmp/calibre-optimized"
mkdir -p "$original_dir" "$inbox" "$output" "$destination"
printf 'unoptimized fixture\n' > "$original_dir/fixture.epub"

cat > "$tmp/fake-optimizer.sh" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
output=''
while [ "$#" -gt 1 ]; do
  if [ "$1" = '-o' ]; then output=$2; shift 2; continue; fi
  shift
done
input=$1
cp "$input" "$output/$(basename "$input")"
FAKE
chmod +x "$tmp/fake-optimizer.sh"

cat > "$tmp/optimizer.env" <<EOF
BOOKDROP_DIR=$inbox
CALIBRE_WATCH_FOLDER=
EPUB_OUTPUT_DIR=$output
OPTIMIZER_PYTHON=bash
OPTIMIZER_SCRIPT=$tmp/fake-optimizer.sh
WATCHER_DEST_DIR=$destination
OPTIMIZER_LOG_FILE=$tmp/optimizer.log
WATCHER_LOG_FILE=$tmp/watcher.log
POLL_INTERVAL=1
KEEP_DAYS=5
SOURCE_RETENTION=delete
EOF

export EPUB_OPTIMIZER_ENV="$tmp/optimizer.env"
Readarr_EventType=Test bash "$repo/scripts/bookshelf-custom-script.sh"
Readarr_EventType=Download Readarr_AddedBookPaths="$original_dir/fixture.epub" bash "$repo/scripts/bookshelf-custom-script.sh"
if timeout 3 bash "$repo/scripts/epub-optimizer.sh"; then
  echo 'optimizer unexpectedly exited' >&2
  exit 1
fi

cmp "$original_dir/fixture.epub" "$output/fixture.epub"
# The watcher first drains existing output, then blocks waiting for future files.
timeout 1 bash "$repo/scripts/epub-watcher.sh" || true
cmp "$original_dir/fixture.epub" "$destination/fixture.epub"
test ! -e "$output/fixture.epub"
test ! -e "$inbox/fixture.epub"
test ! -e "$inbox/processing/fixture.epub"
test ! -e "$inbox/processed/fixture.epub"
test -f "$original_dir/fixture.epub"

# A deployed Bookshelf custom-script directory carries its own optimizer.env.
# It must use that sidecar configuration even when Bookshelf has no global
# EPUB_OPTIMIZER_ENV configured.
sidecar_dir="$tmp/deployed-custom-scripts"
sidecar_inbox="$tmp/sidecar-inbox"
mkdir -p "$sidecar_dir" "$sidecar_inbox"
cp "$repo/scripts/bookshelf-custom-script.sh" "$repo/scripts/epub-import-hook.sh" "$repo/scripts/load-env.sh" "$sidecar_dir/"
cat > "$sidecar_dir/optimizer.env" <<EOF
BOOKDROP_DIR=$sidecar_inbox
EOF
unset EPUB_OPTIMIZER_ENV
Readarr_EventType=Download Readarr_AddedBookPaths="$original_dir/fixture.epub" bash "$sidecar_dir/bookshelf-custom-script.sh"
test -f "$sidecar_inbox/fixture.epub"

echo 'PASS: original retained; one optimized Calibre output created; no retained staging copy; sidecar config honored'
