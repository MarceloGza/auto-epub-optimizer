#!/usr/bin/env bash
# Bookshelf Custom Script entrypoint. It consumes Readarr's post-import
# environment and delegates each newly imported EPUB to the copy-only hook.
set -euo pipefail

script_dir=$(cd "$(dirname "$0")" && pwd)

event_type=${Readarr_EventType:-}
case "$event_type" in
  Test)
    exit 0
    ;;
  Download)
    ;;
  *)
    # This notification can be enabled for other Bookshelf events; only
    # release-import downloads create optimizer inbox copies.
    exit 0
    ;;
esac

added_paths=${Readarr_AddedBookPaths:-}
if [ -z "$added_paths" ]; then
  exit 0
fi

IFS='|' read -r -a paths <<< "$added_paths"
for path in "${paths[@]}"; do
  "$script_dir/epub-import-hook.sh" "$path"
done
