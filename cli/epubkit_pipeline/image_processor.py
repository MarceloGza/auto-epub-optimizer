"""
Image processor for Xteink X4 EPUB Optimizer.
Handles: baseline JPEG conversion, resize, 4-level grayscale quantization,
contrast boost, Light Novel mode.

X4 specs (SSD1677 controller):
  - Display: 800x480, 4-level grayscale (black, dark gray, light gray, white)
  - Max image: 1024x1024
  - RAM: 380KB — smaller images = faster rendering
"""

import io
import re
from pathlib import Path
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageEnhance, ImageOps, ImageDraw, ImageFont, ImageStat


# Device profiles use portrait orientation (short edge x long edge).
X4_WIDTH = 480
X4_HEIGHT = 800

# X3 screen dimensions (528x792 portrait panel)
X3_WIDTH = 528
X3_HEIGHT = 792

# Device profiles: name -> (width, height)
DEVICE_PROFILES = {
    'x4': (X4_WIDTH, X4_HEIGHT),
    'x3': (X3_WIDTH, X3_HEIGHT),
}

# Hard limit per X4 JPEG spec
MAX_IMAGE_DIMENSION = 1024

# SSD1677 supports 4-level grayscale: black, dark gray, light gray, white
EINK_PALETTE_LEVELS = [0, 85, 170, 255]

CROP_WHITE_THRESHOLD = 245
CROP_BACKGROUND_TOLERANCE = 28
CROP_BACKGROUND_MAX_SPREAD = 24
CROP_EDGE_SAMPLE_SIZE = 12
CROP_PADDING_PX = 8
MIN_CROP_SAVINGS_RATIO = 0.08
MIN_COLOR_CROP_SAVINGS_RATIO = 0.20
MIN_CROP_DIMENSION = 240

SUPPORTED_EXTENSIONS = {'.png', '.gif', '.webp', '.bmp', '.jpeg', '.jpg', '.tif', '.tiff'}


@dataclass
class ImageOptions:
    grayscale: bool = True
    contrast_boost: bool = False
    contrast_factor: float = 1.0
    quality: int = 85
    max_width: int = X4_WIDTH
    max_height: int = X4_HEIGHT
    eink_quantize: bool = False
    auto_crop: bool = False
    light_novel_mode: bool = False
    light_novel_rotate_left: bool = True


@dataclass
class ImageResult:
    output_bytes: bytes
    new_filename: str
    original_size: int
    new_size: int
    was_converted: bool
    details: str


def should_process(filename: str) -> bool:
    """Check if a file is a processable image based on extension."""
    return Path(filename).suffix.lower() in SUPPORTED_EXTENSIONS


def is_progressive_jpeg(image_bytes: bytes) -> bool:
    """Check if JPEG data is progressive/interlaced."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        if img.format != 'JPEG':
            return False
        return img.info.get('progressive', False) or img.info.get('progression', False)
    except Exception:
        return False


def _encode_jpeg_bytes(img: Image.Image, quality: int, grayscale: bool) -> bytes:
    """Encode an image as baseline JPEG bytes with the pipeline defaults."""
    buffer = io.BytesIO()
    img.save(
        buffer,
        format='JPEG',
        quality=quality,
        progressive=False,
        optimize=True,
        # 4:2:0 for grayscale (all 3 channels identical, saves ~15-20%)
        # 4:4:4 for color images
        subsampling=2 if grayscale else 0
    )
    return buffer.getvalue()


def _quantize_to_4_levels(img: Image.Image) -> Image.Image:
    """
    Quantize grayscale image to 4 e-ink levels with Floyd-Steinberg dithering.
    Maps to: black (0), dark gray (85), light gray (170), white (255).
    Uses PIL's built-in quantize with a custom 4-color palette for speed.
    """
    # Build a 4-color grayscale palette image
    palette_img = Image.new('P', (1, 1))
    palette = []
    for level in EINK_PALETTE_LEVELS:
        palette.extend([level, level, level])
    # Pad palette to 256 entries (required by PIL)
    palette.extend([0, 0, 0] * (256 - len(EINK_PALETTE_LEVELS)))
    palette_img.putpalette(palette)

    # Quantize with Floyd-Steinberg dithering
    rgb = img.convert('RGB')
    quantized = rgb.quantize(colors=len(EINK_PALETTE_LEVELS),
                             palette=palette_img,
                             dither=Image.Dither.FLOYDSTEINBERG)
    return quantized.convert('L')


def _handle_transparency(img: Image.Image) -> Image.Image:
    """Composite transparent images onto white background."""
    if img.mode in ('RGBA', 'LA', 'PA'):
        background = Image.new('RGB', img.size, (255, 255, 255))
        if img.mode == 'PA':
            img = img.convert('RGBA')
        background.paste(img, mask=img.split()[-1])
        return background
    if img.mode == 'P':
        if 'transparency' in img.info:
            img = img.convert('RGBA')
            background = Image.new('RGB', img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1])
            return background
        return img.convert('RGB')
    return img


def _estimate_crop_background(img: Image.Image) -> tuple[float, float, float] | None:
    width, height = img.size
    sample_size = min(CROP_EDGE_SAMPLE_SIZE, width // 8, height // 8)
    if sample_size < 2:
        return None

    points = (
        (0, 0),
        (width - sample_size, 0),
        (0, height - sample_size),
        (width - sample_size, height - sample_size),
        ((width - sample_size) // 2, 0),
        ((width - sample_size) // 2, height - sample_size),
        (0, (height - sample_size) // 2),
        (width - sample_size, (height - sample_size) // 2),
    )
    samples = []
    for x, y in points:
        region = img.crop((x, y, x + sample_size, y + sample_size))
        samples.append(tuple(ImageStat.Stat(region).mean[:3]))

    average = tuple(sum(sample[channel] for sample in samples) / len(samples)
                    for channel in range(3))
    max_spread = max(
        abs(sample[channel] - average[channel])
        for sample in samples
        for channel in range(3)
    )
    return None if max_spread > CROP_BACKGROUND_MAX_SPREAD else average


def _auto_crop_margins(img: Image.Image, filename: str,
                       protected: bool) -> tuple[Image.Image, bool]:
    width, height = img.size
    if protected or width < MIN_CROP_DIMENSION or height < MIN_CROP_DIMENSION:
        return img, False
    if re.search(r'^(?:cover|thumbnail|thumb|icon)[^/]*\.(?:jpe?g|png|gif|webp|bmp)$',
                 Path(filename).name, re.I):
        return img, False

    rgb = img.convert('RGB')
    background = _estimate_crop_background(rgb)
    if background is not None:
        background_image = Image.new('RGB', rgb.size, tuple(round(value) for value in background))
        channels = ImageChops.difference(rgb, background_image).split()
        masks = [channel.point(lambda value: 255 if value > CROP_BACKGROUND_TOLERANCE else 0)
                 for channel in channels]
    else:
        masks = [channel.point(lambda value: 255 if value < CROP_WHITE_THRESHOLD else 0)
                 for channel in rgb.split()]
    content_mask = ImageChops.lighter(ImageChops.lighter(masks[0], masks[1]), masks[2])
    bounds = content_mask.getbbox()
    if bounds is None:
        return img, False

    left = max(0, bounds[0] - CROP_PADDING_PX)
    top = max(0, bounds[1] - CROP_PADDING_PX)
    right = min(width, bounds[2] + CROP_PADDING_PX)
    bottom = min(height, bounds[3] + CROP_PADDING_PX)
    crop_width = right - left
    crop_height = bottom - top
    saved_ratio = 1 - ((crop_width * crop_height) / (width * height))
    is_near_white = background is not None and all(
        value >= CROP_WHITE_THRESHOLD for value in background
    )
    min_saved_ratio = (
        MIN_COLOR_CROP_SAVINGS_RATIO
        if background is not None and not is_near_white
        else MIN_CROP_SAVINGS_RATIO
    )
    if saved_ratio < min_saved_ratio:
        return img, False

    return rgb.crop((left, top, right, bottom)), True


def _handle_light_novel(img: Image.Image, rotate_left: bool,
                        max_width: int, max_height: int) -> list[Image.Image]:
    """
    Rotate or split oversized landscape artwork for portrait viewing.
    Small banners and section ornaments are intentionally left alone.
    """
    width, height = img.size

    if width <= height or width < max_height or (width <= max_width and height <= max_height):
        return [img]

    aspect = width / height

    if aspect > 1.8:
        # Double-page spread — split into two portrait pages
        mid = width // 2
        right_half = img.crop((mid, 0, width, height))
        left_half = img.crop((0, 0, mid, height))
        return [right_half, left_half]
    else:
        angle = 90 if rotate_left else -90
        rotated = img.rotate(angle, expand=True)
        return [rotated]


def process_image(image_bytes: bytes, filename: str, options: ImageOptions = None,
                  protect_auto_crop: bool = False) -> list[ImageResult]:
    """
    Process a single image for X4 optimization.
    Returns a list of ImageResult (usually 1, but Light Novel mode may split into 2).
    """
    if options is None:
        options = ImageOptions()

    original_size = len(image_bytes)
    original_ext = Path(filename).suffix.lower()
    stem = Path(filename).stem

    try:
        img = Image.open(io.BytesIO(image_bytes))
    except Exception as e:
        return [ImageResult(
            output_bytes=image_bytes,
            new_filename=filename,
            original_size=original_size,
            new_size=original_size,
            was_converted=False,
            details=f"Skipped (corrupt: {e})"
        )]

    # Handle animated GIFs — take first frame
    if getattr(img, 'is_animated', False):
        img.seek(0)

    # Handle CMYK
    if img.mode == 'CMYK':
        img = img.convert('RGB')

    # Handle 1-bit images
    if img.mode == '1':
        img = img.convert('L')

    # Handle transparency
    img = _handle_transparency(img)

    # Ensure RGB mode
    if img.mode not in ('RGB', 'L'):
        img = img.convert('RGB')

    source_size = img.size
    light_novel_eligible = img.width > img.height and img.width >= options.max_height
    source_was_cropped = False
    if options.auto_crop:
        img, source_was_cropped = _auto_crop_margins(img, filename, protect_auto_crop)

    # Light Novel mode — handle landscape images
    if options.light_novel_mode and light_novel_eligible:
        images = _handle_light_novel(
            img,
            options.light_novel_rotate_left,
            options.max_width,
            options.max_height,
        )
    else:
        images = [img]

    results = []
    for i, current_img in enumerate(images):
        details_parts = []

        # Track format conversion
        if original_ext != '.jpg' and original_ext != '.jpeg':
            details_parts.append(f"{original_ext.upper().strip('.')}→JPEG")
        if source_was_cropped:
            details_parts.append(
                f"auto-cropped {source_size[0]}x{source_size[1]}→{img.width}x{img.height}"
            )

        orig_w, orig_h = current_img.size

        # Enforce 1024x1024 hard limit (X4 JPEG spec)
        if orig_w > MAX_IMAGE_DIMENSION or orig_h > MAX_IMAGE_DIMENSION:
            current_img.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION),
                                  Image.Resampling.LANCZOS)
            clamped_w, clamped_h = current_img.size
            details_parts.append(f"clamped {orig_w}x{orig_h}→{clamped_w}x{clamped_h}")
            orig_w, orig_h = clamped_w, clamped_h

        # Cropped content is allowed to scale up; untouched images only scale down.
        if source_was_cropped or orig_w > options.max_width or orig_h > options.max_height:
            scale = min(options.max_width / orig_w, options.max_height / orig_h)
            new_size = (max(1, round(orig_w * scale)), max(1, round(orig_h * scale)))
            current_img = current_img.resize(new_size, Image.Resampling.LANCZOS)
            new_w, new_h = current_img.size
            details_parts.append(f"resized {orig_w}x{orig_h}→{new_w}x{new_h}")

        # Convert to grayscale
        if options.grayscale:
            current_img = current_img.convert('L')

            # Contrast enhancement (before quantization for best results)
            if options.contrast_boost:
                if options.eink_quantize:
                    # Auto-stretch histogram first for better 4-level mapping
                    current_img = ImageOps.autocontrast(current_img, cutoff=1)
                enhancer = ImageEnhance.Contrast(current_img)
                current_img = enhancer.enhance(options.contrast_factor)

            # Quantize to 4 e-ink levels with dithering
            if options.eink_quantize:
                current_img = _quantize_to_4_levels(current_img)
                details_parts.append("4-level grayscale")
            else:
                details_parts.append("grayscale")

            if options.contrast_boost:
                details_parts.append(f"contrast {options.contrast_factor}x")

            # Convert back to RGB for JPEG compatibility
            current_img = current_img.convert('RGB')

        elif options.contrast_boost:
            # Contrast without grayscale
            enhancer = ImageEnhance.Contrast(current_img)
            current_img = enhancer.enhance(options.contrast_factor)
            details_parts.append(f"contrast {options.contrast_factor}x")

        # Save as baseline JPEG
        output_bytes = _encode_jpeg_bytes(current_img, options.quality, options.grayscale)

        # Build filename
        if len(images) > 1:
            new_filename = f"{stem}_part{i + 1}.jpg"
            details_parts.insert(0, f"split part {i + 1}/{len(images)}")
        else:
            new_filename = f"{stem}.jpg"

        results.append(ImageResult(
            output_bytes=output_bytes,
            new_filename=new_filename,
            original_size=original_size if i == 0 else 0,
            new_size=len(output_bytes),
            was_converted=True,
            details=", ".join(details_parts) if details_parts else "baseline JPEG"
        ))

    return results


def generate_cover_image(title: str, author: str,
                         width: int = X4_WIDTH, height: int = X4_HEIGHT) -> bytes:
    """Generate a simple cover image from title and author text."""
    img = Image.new('RGB', (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    title_size = 36
    author_size = 24

    try:
        title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", title_size)
        author_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", author_size)
    except (OSError, IOError):
        try:
            title_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", title_size)
            author_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", author_size)
        except (OSError, IOError):
            title_font = ImageFont.load_default()
            author_font = ImageFont.load_default()

    border = 20
    draw.rectangle(
        [border, border, width - border, height - border],
        outline=(180, 180, 180),
        width=2
    )

    padding = 40
    max_text_width = width - (padding * 2)

    def wrap_text(text, font, max_w):
        words = text.split()
        lines = []
        current_line = ""
        for word in words:
            test = f"{current_line} {word}".strip()
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] <= max_w:
                current_line = test
            else:
                if current_line:
                    lines.append(current_line)
                current_line = word
        if current_line:
            lines.append(current_line)
        return lines

    title_lines = wrap_text(title, title_font, max_text_width)
    title_y = height // 3
    for line in title_lines:
        bbox = draw.textbbox((0, 0), line, font=title_font)
        line_w = bbox[2] - bbox[0]
        x = (width - line_w) // 2
        draw.text((x, title_y), line, fill=(30, 30, 30), font=title_font)
        title_y += bbox[3] - bbox[1] + 8

    if author:
        author_lines = wrap_text(author, author_font, max_text_width)
        author_y = title_y + 40
        for line in author_lines:
            bbox = draw.textbbox((0, 0), line, font=author_font)
            line_w = bbox[2] - bbox[0]
            x = (width - line_w) // 2
            draw.text((x, author_y), line, fill=(100, 100, 100), font=author_font)
            author_y += bbox[3] - bbox[1] + 6

    buffer = io.BytesIO()
    img.save(buffer, format='JPEG', quality=85, progressive=False, optimize=True)
    return buffer.getvalue()
