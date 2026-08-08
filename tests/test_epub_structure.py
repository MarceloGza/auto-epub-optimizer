import tempfile
import unittest
from pathlib import Path

from lxml import etree

import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = PROJECT_ROOT / 'cli' / 'epubkit_pipeline'
sys.path.insert(0, str(PIPELINE_DIR))

from epub_structure import ensure_cover_meta, fix_svg_covers  # noqa: E402


class EpubStructureTests(unittest.TestCase):
    def test_cover_meta_is_added_from_cover_image_property(self):
        with tempfile.TemporaryDirectory() as tmp:
            opf_path = Path(tmp) / 'content.opf'
            opf_path.write_text('''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata/>
  <manifest>
    <item id="cover-art" href="Images/front.jpg" media-type="image/jpeg" properties="cover-image"/>
  </manifest>
  <spine/>
</package>''', encoding='utf-8')

            changed = ensure_cover_meta(str(opf_path))

            opf = etree.parse(str(opf_path))
            meta = opf.find('.//{http://www.idpf.org/2007/opf}meta')
            self.assertTrue(changed)
            self.assertEqual(meta.get('name'), 'cover')
            self.assertEqual(meta.get('content'), 'cover-art')

    def test_svg_images_are_unwrapped_outside_the_spine(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            opf_dir = root / 'OEBPS'
            opf_dir.mkdir()
            opf_path = opf_dir / 'content.opf'
            xhtml_path = opf_dir / 'art.xhtml'
            opf_path.write_text('''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="art" href="art.xhtml" media-type="application/xhtml+xml" properties="svg"/>
  </manifest>
  <spine/>
</package>''', encoding='utf-8')
            xhtml_path.write_text('''<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:svg="http://www.w3.org/2000/svg"
      xmlns:xlink="http://www.w3.org/1999/xlink">
  <head><title>Art</title></head>
  <body><svg:svg><svg:image xlink:href="Images/art.png"/></svg:svg></body>
</html>''', encoding='utf-8')

            fixed = fix_svg_covers(str(root), str(opf_path))

            output = xhtml_path.read_text(encoding='utf-8')
            opf = etree.parse(str(opf_path))
            item = opf.find('.//{http://www.idpf.org/2007/opf}item')
            self.assertEqual(fixed, 1)
            self.assertIn('<div><img src="Images/art.png"', output)
            self.assertNotIn('<svg:svg', output)
            self.assertIsNone(item.get('properties'))


if __name__ == '__main__':
    unittest.main()