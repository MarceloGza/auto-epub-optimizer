import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLI_DIR = PROJECT_ROOT / 'cli'
sys.path.insert(0, str(CLI_DIR))

from optimize import apply_device_defaults, build_options, build_parser  # noqa: E402


class CliOptionTests(unittest.TestCase):
    def test_defaults_match_crosspoint_x4(self):
        args = build_parser().parse_args(['book.epub'])
        apply_device_defaults(args)
        options = build_options(args)

        self.assertEqual((options.max_width, options.max_height), (480, 800))
        self.assertEqual(options.quality, 85)
        self.assertFalse(options.eink_quantize)
        self.assertFalse(options.auto_crop)
        self.assertFalse(options.remove_fonts)
        self.assertFalse(options.remove_unused_css)
        self.assertFalse(options.generate_missing_cover)
        self.assertFalse(options.clean_metadata)
        self.assertFalse(options.text_cleanup)

    def test_x3_profile_and_dimension_override(self):
        args = build_parser().parse_args([
            '--device', 'x3', '--max-width', '500', '--auto-crop', 'book.epub',
        ])
        apply_device_defaults(args)
        options = build_options(args)

        self.assertEqual((options.max_width, options.max_height), (500, 792))
        self.assertTrue(options.auto_crop)


if __name__ == '__main__':
    unittest.main()