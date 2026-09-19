#!/usr/bin/env bash
# Copies a Bookshelf-imported EPUB into the optimizer inbox without changing
# the source that Bookshelf tracks. Intended for a Bookshelf custom-script hook.
set -euo pipefail

source "$(dirname "$0")/load-env.sh"

if [ "$#" -ne 1 ]; then
  echo "Usage: $(basename "$0") /absolute/path/to/book.epub" >&2
  exit 64
fi

source_path=$1
case "$source_path" in
  *.epub|*.EPUB) ;;
  *) echo "Refusing non-EPUB input: $source_path" >&2; exit 64 ;;
esac

if [ ! -f "$source_path" ]; then
  echo "Input EPUB not found: $source_path" >&2
  exit 66
fi

mkdir -p "$BOOKDROP_DIR"
filename=$(basename "$source_path")
destination="$BOOKDROP_DIR/$filename"

if [ -e "$destination" ]; then
  if cmp -s "$source_path" "$destination"; then
    echo "Optimizer inbox already contains identical EPUB: $filename"
    exit 0
  fi
  echo "Refusing to overwrite a different EPUB already in optimizer inbox: $destination" >&2
  exit 73
fi

temporary="$BOOKDROP_DIR/.${filename}.$$.$RANDOM.part"
trap 'rm -f "$temporary"' EXIT
cp --preserve=mode,timestamps "$source_path" "$temporary"
mv "$temporary" "$destination"
trap - EXIT
echo "Copied Bookshelf original to optimizer inbox: $filename"
