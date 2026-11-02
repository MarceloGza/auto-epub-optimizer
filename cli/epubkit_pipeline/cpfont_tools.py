"""Lossless additive overlay for CrossPoint v4, uncompressed 2-bit fonts.

Existing glyph metrics/bitmaps, line metrics, kerning and ligatures win.
Supplemental regular symbols also fill missing bold/italic styles; they do not
replace any existing style glyph. This is a host-only tool, not firmware code.
"""
import argparse
import struct
from pathlib import Path

HEADER = struct.Struct('<8sHHB19s')
TOC = struct.Struct('<B3xIIBhhHHBBBI4x')
GLYPH = struct.Struct('<BBHhhH2xI')
INTERVAL = struct.Struct('<III')


def parse_font(data):
    if len(data) < HEADER.size:
        raise ValueError('Truncated font header')
    magic, version, flags, count, _ = HEADER.unpack_from(data)
    if magic != b'CPFONT\x00\x00' or version != 4 or flags != 1 or not 1 <= count <= 4:
        raise ValueError('Expected v4 uncompressed 2-bit cpfont')
    if len(data) < HEADER.size + TOC.size * count:
        raise ValueError('Truncated font style table')
    styles = {}
    entries = [TOC.unpack_from(data, HEADER.size + TOC.size * i) for i in range(count)]
    for index, row in enumerate(entries):
        style, ni, ng, advance, asc, desc, kl, kr, lc, rc, lig, offset = row
        end = entries[index + 1][-1] if index + 1 < count else len(data)
        if style in styles or style > 3 or ni > ng or ng > 65535:
            raise ValueError('Invalid style or glyph count')
        glyph_start = offset + ni * INTERVAL.size
        aux_start = glyph_start + ng * GLYPH.size
        bitmap_start = aux_start + (kl + kr) * 3 + lc * rc + lig * 8
        if not HEADER.size + TOC.size * count <= offset <= glyph_start <= aux_start <= bitmap_start <= end <= len(data):
            raise ValueError('Invalid font section bounds')
        glyphs = {}
        previous = -1
        expected_index = 0
        for i in range(ni):
            first, last, start = INTERVAL.unpack_from(data, offset + i * INTERVAL.size)
            if not previous < first <= last <= 0x10ffff or start != expected_index or start + last - first >= ng:
                raise ValueError('Invalid coverage intervals')
            for cp in range(first, last + 1):
                values = GLYPH.unpack_from(data, glyph_start + (start + cp - first) * GLYPH.size)
                length, position = values[-2:]
                if bitmap_start + position + length > end:
                    raise ValueError('Glyph bitmap exceeds style section')
                glyphs[cp] = (values[:-1], data[bitmap_start + position:bitmap_start + position + length])
            previous = last
            expected_index += last - first + 1
        if expected_index != ng:
            raise ValueError('Coverage/glyph count mismatch')
        styles[style] = {'glyphs': glyphs, 'metrics': (advance, asc, desc),
                         'aux_counts': (kl, kr, lc, rc, lig), 'aux': data[aux_start:bitmap_start]}
    return styles


def extend_font(primary, supplements):
    styles = parse_font(primary)
    extras = [parse_font(data) for data in supplements]
    sections, rows = [], []
    offset = HEADER.size + TOC.size * len(styles)
    for style, original in styles.items():
        glyphs = dict(original['glyphs'])
        for extra in extras:
            fallback = extra.get(style, extra.get(0))
            if fallback:
                for cp, glyph in fallback['glyphs'].items():
                    if cp not in glyphs:
                        glyphs[cp] = glyph
        if len(glyphs) > 65535:
            raise ValueError('Too many glyphs for reader font')
        points = sorted(glyphs)
        intervals = []
        for index, cp in enumerate(points):
            if intervals and cp == intervals[-1][1] + 1:
                intervals[-1][1] = cp
            else:
                intervals.append([cp, cp, index])
        coverage = b''.join(INTERVAL.pack(*row) for row in intervals)
        table, bitmap = bytearray(), bytearray()
        for cp in points:
            metrics, pixels = glyphs[cp]
            table.extend(GLYPH.pack(*metrics, len(bitmap)))
            bitmap.extend(pixels)
        section = coverage + table + original['aux'] + bitmap
        rows.append(TOC.pack(style, len(intervals), len(points), *original['metrics'],
                             *original['aux_counts'], offset))
        sections.append(section)
        offset += len(section)
    return HEADER.pack(b'CPFONT\x00\x00', 4, 1, len(styles), bytes(19)) + b''.join(rows) + b''.join(sections)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('primary', type=Path)
    parser.add_argument('supplements', nargs='+', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve() in [p.resolve() for p in [args.primary, *args.supplements]]:
        parser.error('Output must be a new file, not an input')
    result = extend_font(args.primary.read_bytes(), [p.read_bytes() for p in args.supplements])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('xb') as target:
        target.write(result)


if __name__ == '__main__':
    main()
