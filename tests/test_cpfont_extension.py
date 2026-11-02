import importlib.util
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'cli' / 'epubkit_pipeline'))
TOC = struct.Struct('<B3xIIBhhHHBBBI4x')
GLYPH = struct.Struct('<BBHhhH2xI')


def fixture(points, advance=400, bitmap=b'\x1b'):
    intervals = b''.join(struct.pack('<III', cp, cp, i) for i, cp in enumerate(points))
    glyphs = b''.join(GLYPH.pack(2, 2, advance, 0, 2, len(bitmap), i * len(bitmap))
                      for i in range(len(points)))
    return (struct.pack('<8sHHB19s', b'CPFONT\x00\x00', 4, 1, 1, bytes(19))
            + TOC.pack(0, len(points), len(points), 31, 24, -7, 0, 0, 0, 0, 0, 64)
            + intervals + glyphs + bitmap * len(points))


class CpfontExtensionTests(unittest.TestCase):
    def test_extension_preserves_primary_metrics_bitmaps_and_adds_only_missing_glyphs(self):
        self.assertIsNotNone(importlib.util.find_spec('cpfont_tools'), 'font extension is not implemented')
        from cpfont_tools import extend_font, parse_font
        base = fixture([65, 0x2014])
        fallback = fixture([65, 0x2665, 0x266A], advance=500, bitmap=b'\xe4')
        before = parse_font(base)
        after = parse_font(extend_font(base, [fallback]))
        self.assertEqual(after[0]['glyphs'][65], before[0]['glyphs'][65])
        self.assertEqual(after[0]['glyphs'][0x2014], before[0]['glyphs'][0x2014])
        self.assertEqual(after[0]['glyphs'][0x2665], parse_font(fallback)[0]['glyphs'][0x2665])
        self.assertEqual(after[0]['metrics'], before[0]['metrics'])
        self.assertEqual(set(after[0]['glyphs']), {65, 0x2014, 0x2665, 0x266A})


if __name__ == '__main__':
    unittest.main()
