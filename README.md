# <div align="center">Automated EPUB Optimizer Workflow</div>

**<div align="center">Drop EPUB into folder → Automatically gets optimized → File is moved to your library</div>**<br/>

Optimizes EPUB files for e-readers like the Xteink X3/X4 using the same compatibility approach as the official CrossPoint file-transfer optimizer. It converts images to baseline grayscale JPEG, applies device-specific bounds, fixes image references and SVG wrappers, preserves book content and navigation, and repackages the EPUB correctly.

Use it in three ways:

1. Automatically with a watcher via Docker Compose or Systemd
2. Manually by running the Python CLI (`cli/optimize.py`)
3. Manually via the browser-based GUI (`browser/index.html`), no install required

## Features

- Drop an `.epub` into one folder and have it appear in two separately managed libraries automatically
- The original is copied to your Calibre watch folder and handled from there as normal
- A grayscale-optimized copy is written to a separate library, ready to serve via OPDS
- Optional second drop folder supported for "optimize only" runs that skip the Calibre copy
- Single-library mode also supported: leave `CALIBRE_WATCH_FOLDER` unset and only the optimized copy is produced
- Preserves fonts, CSS, metadata, text, and existing spine sections by default
- Optional light-novel mode rotates or splits only oversized landscape artwork

# Usage/Installation
There are four ways to install or use this workflow:
1. [Docker](#docker-compose)
2. [Systemd](#systemd-automated-watcher-linux--wsl2)
3. [Manually via your local Browser](#browser-no-install-required)
4. [Manually via CLI](#cli)

> The default profile is X4 (`480x800`). Set `EPUB_DEVICE=x3` or pass `--device x3` for the X3's `528x792` bounds. `EPUB_MAX_WIDTH` and `EPUB_MAX_HEIGHT` remain available as explicit overrides.

## Docker Compose

The repo includes a `docker-compose.yml` that runs the automated pipeline without installing Python dependencies or `inotify-tools` on the host.

### Setup

```bash
cp .env.example .env
# Edit .env - set BOOKDROP_DIR, WATCHER_DEST_DIR, and optionally CALIBRE_WATCH_FOLDER
```

The containers use fixed internal paths (`/bookdrop`, `/output`, `/destination`, `/calibre`). You only need to set the host-side paths in `.env`. `EPUB_OUTPUT_DIR` is handled internally via a shared Docker volume between the two services.

#### Why two services?

The optimizer writes finished EPUBs to an intermediate Docker volume (`output`), and the watcher moves them from there to `WATCHER_DEST_DIR`. This split exists because `inotify` is unreliable on Windows NTFS paths like `/mnt/c/...` inside Docker on WSL2. Keeping the handoff point on a Linux volume makes the watcher reliable.

If your `WATCHER_DEST_DIR` is a plain Linux path, you can remove the `epub-watcher` service and point `EPUB_OUTPUT_DIR` directly at the destination.

### Run

```bash
docker compose up -d
```

### Logs

```bash
docker compose logs -f epub-optimizer
docker compose logs -f epub-watcher
```

### Stop

```bash
docker compose down
```

### Apply changes

If you change Python code, shell scripts, Dockerfiles, or `docker-compose.yml`, rebuild and recreate the containers:

```bash
docker compose up -d --build
```

If you only change `.env`, re-apply the Compose config:

```bash
docker compose up -d
```

If you want a completely fresh restart:

```bash
docker compose down
docker compose up -d --build
```

## Systemd Automated Watcher (Linux / WSL2)

The `scripts/` folder contains two systemd user services that build a fully automated pipeline:

```
BOOKDROP_DIR  →[epub-optimizer]→  EPUB_OUTPUT_DIR  →[epub-watcher]→  WATCHER_DEST_DIR
```

- **epub-optimizer** - polls a bookdrop folder for `.epub` files, runs `optimize.py` on each, and writes the result to an output folder. Uses polling instead of `inotify` so it works on Windows NTFS mounts (`/mnt/c/`) under WSL2.
- **epub-watcher** - watches the output folder with `inotifywait` and moves finished files to a final destination (e.g. a Calibre/OPDS library folder).

### 1. Configure

Copy the example config and fill in your paths:

```bash
mkdir -p ~/.config/epub-optimizer
cp .env.example ~/.config/epub-optimizer/.env
```

Edit `~/.config/epub-optimizer/.env`:

| Variable               | Description                                                                                                                        |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `BOOKDROP_DIR`         | Drop `.epub` files here to trigger processing                                                                                      |
| `OPTIMIZE_ONLY_DIR`    | Optional second drop folder; files placed here skip the Calibre copy step and only go through optimization                         |
| `CALIBRE_WATCH_FOLDER` | Optional - Calibre watch folder; files are copied here before optimization (for use when you want a separate workflow for Calibre) |
| `OPTIMIZER_PYTHON`     | Python executable used to run the optimizer, e.g. `python3` or a virtualenv path                                                   |
| `OPTIMIZER_SCRIPT`     | Absolute path to `cli/optimize.py` in this repo                                                                                    |
| `EPUB_OUTPUT_DIR`      | Where the optimizer writes finished EPUBs                                                                                          |
| `WATCHER_DEST_DIR`     | Where the watcher moves finished EPUBs (your final X4 library folder)                                                              |
| `OPTIMIZER_LOG_FILE`   | Log path for the optimizer service (default: `~/.local/log/epub-optimizer.log`)                                                    |
| `WATCHER_LOG_FILE`     | Log path for the watcher service (default: `~/.local/log/epub-watcher.log`)                                                        |
| `POLL_INTERVAL`        | Seconds between bookdrop scans (default: `5`)                                                                                      |
| `KEEP_DAYS`            | Days to keep files in `bookdrop/processed/` before auto-deletion (default: `5`)                                                    |
| `EPUB_DEVICE`          | Device profile: `x4` (default, `480x800`) or `x3` (`528x792`)                                                                     |
| `EPUB_QUALITY`         | Optional JPEG quality, default `85`                                                                                                |
| `EPUB_MAX_WIDTH`       | Optional max image width override                                                                                                  |
| `EPUB_MAX_HEIGHT`      | Optional max image height override                                                                                                 |
| `EPUB_CONTRAST`        | Optional - set to `1` to enable contrast boost                                                                                     |
| `EPUB_CONTRAST_FACTOR` | Optional contrast multiplier used when contrast boost is enabled, default `1.0`                                                     |
| `EPUB_LIGHT_NOVEL`     | Optional - set to `1` to rotate/split oversized landscape light-novel artwork                                                      |
| `EPUB_SPLIT_LONG_SECTIONS` | Optional - set to `1` to split oversized XHTML spine items into smaller reader sections                                         |
| `EPUB_SECTION_SPLIT_WORD_THRESHOLD` | Optional visible-word threshold for `EPUB_SPLIT_LONG_SECTIONS`, default `2000`                                       |
| `EPUB_FILENAME_FORMAT` | Optional output name pattern: `author-title`, `title-author`, or `title`                                                           |
| `EPUB_SUFFIX`          | Optional suffix appended before `.epub`, e.g. `-optimized`                                                                         |

### 2. Install

Install the watcher first. `epub-optimizer.service` has `After=epub-watcher.service` in its unit file, so systemd expects the watcher unit to exist before the optimizer is registered.

```bash
# Step 1: watcher (moves optimized files to their final destination)
./scripts/install-epub-watcher.sh

# Step 2: optimizer (polls bookdrop, runs optimize.py)
./scripts/install-epub-optimizer.sh
```

Each installer will:

1. Check for dependencies
2. Create the config file from `.env.example` if it doesn't exist yet
3. Copy scripts to `~/.local/bin/`
4. Register and start the systemd user service

### 3. Use

Drop any `.epub` file into your `BOOKDROP_DIR`. The optimizer picks it up within `POLL_INTERVAL` seconds, copies the original to Calibre if configured, processes it, and the watcher moves the result to `WATCHER_DEST_DIR`.

The optimizer also recurses into **subfolders** of the drop directory, so tools like Readarr or bookshelf apps that create per-author subdirectories (e.g. `BOOKDROP_DIR/Andy Weir/Project Hail Mary.epub`) work automatically. The author folder path is discarded — only the bare epub filename is preserved when the file is moved into `processing/`, `processed/`, or `failed/`. If two author folders contain a file with the same basename, the second file is skipped with a warning in the log to prevent collisions. Empty author folders are removed automatically after their epub has been claimed.

If you set `OPTIMIZE_ONLY_DIR`, files dropped there go through the same optimization flow but skip the Calibre copy entirely. This is useful for books that are already in your Calibre library and only need an optimized X4 copy.

Inside each configured drop folder you'll find three subfolders that track state:

| Subfolder     | Meaning                                                |
| ------------- | ------------------------------------------------------ |
| `processing/` | File is currently being optimized                      |
| `processed/`  | Successfully optimized; auto-deleted after `KEEP_DAYS` |
| `failed/`     | Optimizer returned an error, check the logs            |

### Managing the services

```bash
# Status of both services
systemctl --user status epub-optimizer epub-watcher

# Follow live logs
journalctl --user -u epub-optimizer -f
journalctl --user -u epub-watcher -f

# Restart
systemctl --user restart epub-optimizer epub-watcher

# Stop
systemctl --user stop epub-optimizer epub-watcher
```

### Apply changes

What you need to do depends on what changed:

- If you changed `cli/optimize.py` or files in `cli/epubkit_pipeline/`, restart the services so they pick up the updated repo code.
- If you changed `~/.config/epub-optimizer/.env`, restart the services so they reload the config.
- If you changed files in `scripts/` such as `epub-optimizer.sh`, `epub-watcher.sh`, `load-env.sh`, or either `.service` file, re-run the installers so the copies in `~/.local/bin/` and `~/.config/systemd/user/` are updated.

For code or config changes only:

```bash
systemctl --user restart epub-optimizer epub-watcher
```

For script or service-unit changes:

```bash
./scripts/install-epub-watcher.sh
./scripts/install-epub-optimizer.sh
```

Those installer scripts will copy the updated files, run `systemctl --user daemon-reload`, and restart the services for you.

---

## Browser (no install required)

Open `browser/index.html` directly in a browser. Everything runs locally, no files leave your machine.

This browser page uses a standalone JavaScript implementation. The automated watcher, Docker image, and CLI use the Python implementation.

1. Drop one or more `.epub` files onto the drop zone (or click to select)
2. Adjust settings if needed
3. Click **Optimize & Download**

Main browser settings: JPEG quality, max width/height, split mode, overlap, rotation, and grayscale. Cover contrast enhancement runs automatically.

---

## CLI

### Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Usage

```bash
python3 cli/optimize.py [options] <input.epub ...>
python3 cli/optimize.py [options] <directory>
```

The output filename may be normalized from the EPUB's internal metadata or title, so it will not always exactly match the input basename.

### Options

| Flag                   | Default        | Description                               |
| ---------------------- | -------------- | ----------------------------------------- |
| `-o, --output <dir>`   | `./optimized`  | Output directory                          |
| `-q, --quality <n>`    | `85`           | JPEG quality (1-100)                      |
| `--device <x3|x4>`     | `x4`           | Device image profile                      |
| `--no-grayscale`       | -              | Disable grayscale conversion              |
| `--contrast`           | -              | Enable contrast boost                     |
| `-c, --contrast-factor <n>` | `1.0`     | Contrast multiplier used with `--contrast` |
| `--eink-quantize`      | -              | Opt in to 4-level image quantization      |
| `-W, --max-width <n>`  | profile        | Override max image width                  |
| `-H, --max-height <n>` | profile        | Override max image height                 |
| `--light-novel`        | -              | Rotate/split oversized landscape artwork  |
| `--remove-fonts`       | -              | Opt in to embedded-font removal           |
| `--remove-css`         | -              | Opt in to unused-CSS removal              |
| `--generate-cover`     | -              | Opt in to generated missing cover art     |
| `--clean-metadata`     | -              | Opt in to store-metadata cleanup           |
| `--text-cleanup`       | -              | Opt in to text normalization              |
| `--split-long-sections` | -             | Split oversized XHTML spine items into smaller reader sections |
| `--section-split-word-threshold <words>` | `2000` | Visible-word threshold used with `--split-long-sections` |
| `--filename-format`    | `author-title` | Output filename pattern from metadata     |
| `--suffix <str>`       | empty          | Suffix appended to output filename        |
| `-v, --verbose`        | -              | Print progress and summary details        |
| `--help`               | -              | Show help                                 |

### Pipeline

The Python CLI checks for DRM, extracts the EPUB safely, converts images to baseline grayscale JPEGs for the selected device, updates image references and media types, unwraps SVG images, removes stale image dimensions, injects CrossPoint's defensive image CSS, syncs an existing NCX identifier, and repackages with the EPUB `mimetype` entry first. Fonts, CSS, metadata, text, and spine sections are preserved unless an explicit cleanup option is passed.

### Examples

```bash
# Standard X4 optimization
python3 cli/optimize.py book.epub

# X3 optimization with light-novel handling for large landscape artwork
python3 cli/optimize.py --device x3 --light-novel --output ./out book.epub

# Keep the old filename suffix convention
python3 cli/optimize.py --suffix=-optimized book.epub

# Name outputs as "Title - Author"
python3 cli/optimize.py --filename-format=title-author book.epub

# Explicit custom image bounds
python3 cli/optimize.py -W 600 -H 900 book.epub
```
