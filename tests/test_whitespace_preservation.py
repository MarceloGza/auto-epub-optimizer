import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = PROJECT_ROOT / 'cli' / 'epubkit_pipeline'
sys.path.insert(0, str(PIPELINE_DIR))

from html_cleaner import (  # noqa: E402
    CROSSPOINT_DEFENSIVE_CSS,
    apply_crosspoint_xhtml_fixes,
    normalize_whitespace,
)
from metadata_handler import format_filename  # noqa: E402
from text_cleaner import TextCleanOptions, clean_text_content  # noqa: E402


class WhitespacePreservationTests(unittest.TestCase):
    def test_crosspoint_fixes_only_remove_stale_image_dimensions(self):
        xhtml = (
            b'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>T</title></head>'
            b'<body><p data-keep="yes" class="art">Hello     world'
            b'<img src="image.jpg" width="1600" height="700" class="spread"/></p></body></html>'
        )

        fixed, count = apply_crosspoint_xhtml_fixes(xhtml)

        self.assertNotIn(b'width="1600"', fixed)
        self.assertNotIn(b'height="700"', fixed)
        self.assertIn(b'data-keep="yes"', fixed)
        self.assertIn(b'class="spread"', fixed)
        self.assertIn(b'Hello     world', fixed)
        self.assertIn(CROSSPOINT_DEFENSIVE_CSS.encode(), fixed)
        self.assertEqual(count, 3)

    def test_normalize_whitespace_keeps_space_only_leaf_elements(self):
        xhtml = (
            b'<html xmlns="http://www.w3.org/1999/xhtml">'
            b'<body><div>          </div><div></div><div></div></body></html>'
        )

        cleaned, removed = normalize_whitespace(xhtml)

        self.assertIn(b'<div>          </div>', cleaned)
        self.assertEqual(removed, 1)

    def test_clean_text_content_keeps_space_only_span_text(self):
        xhtml = (
            b'<html xmlns="http://www.w3.org/1999/xhtml">'
            b'<body><p><span class="black">          </span></p></body></html>'
        )

        cleaned, report = clean_text_content(xhtml, TextCleanOptions())

        self.assertIn(b'<span class="black">          </span>', cleaned)
        self.assertEqual(report.double_spaces_fixed, 0)

    def test_clean_text_content_still_collapses_regular_extra_spaces(self):
        xhtml = (
            b'<html xmlns="http://www.w3.org/1999/xhtml">'
            b'<body><p>Hello     world</p></body></html>'
        )

        cleaned, report = clean_text_content(xhtml, TextCleanOptions())

        self.assertIn(b'Hello world', cleaned)
        self.assertGreater(report.double_spaces_fixed, 0)

    def test_format_filename_defaults_to_author_then_title(self):
        self.assertEqual(
            format_filename("My Book", "Jane Doe"),
            "Jane Doe - My Book.epub",
        )

    def test_format_filename_can_put_title_first(self):
        self.assertEqual(
            format_filename("My Book", "Jane Doe", "title-author"),
            "My Book - Jane Doe.epub",
        )

    def test_format_filename_can_use_title_only(self):
        self.assertEqual(
            format_filename("My Book", "Jane Doe", "title"),
            "My Book.epub",
        )


if __name__ == '__main__':
    unittest.main()
