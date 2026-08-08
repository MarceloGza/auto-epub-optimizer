import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw
from lxml import etree


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = PROJECT_ROOT / 'cli' / 'epubkit_pipeline'
sys.path.insert(0, str(PIPELINE_DIR))

from image_processor import ImageOptions, process_image  # noqa: E402
from epub_processor import ProcessingOptions, process_epub  # noqa: E402


def write_spread_epub(path, image_bytes):
    container = '''<?xml version="1.0" encoding="utf-8"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
    <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>'''
    opf = '''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="2.0">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
        <dc:identifier id="BookId">spread-fixture</dc:identifier>
        <dc:title>Spread Fixture</dc:title>
    </metadata>
    <manifest>
        <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
        <item id="spread" href="Images/spread.png" media-type="image/png"/>
    </manifest>
    <spine><itemref idref="chapter"/></spine>
</package>'''
    chapter = '''<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
    <head><title>Spread</title></head>
    <body><div class="spread"><img src="Images/spread.png" width="1600" height="700"/></div></body>
</html>'''
    with zipfile.ZipFile(path, 'w') as epub:
        epub.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        epub.writestr('META-INF/container.xml', container, compress_type=zipfile.ZIP_DEFLATED)
        epub.writestr('OEBPS/content.opf', opf, compress_type=zipfile.ZIP_DEFLATED)
        epub.writestr('OEBPS/chapter.xhtml', chapter, compress_type=zipfile.ZIP_DEFLATED)
        epub.writestr('OEBPS/Images/spread.png', image_bytes, compress_type=zipfile.ZIP_DEFLATED)


class LightNovelImageTests(unittest.TestCase):
    def test_cmyk_jpeg_is_always_reencoded_as_rgb(self):
        image = Image.new('CMYK', (20, 20), (0, 0, 0, 0))
        source = io.BytesIO()
        image.save(source, format='JPEG', quality=20)

        result = process_image(
            source.getvalue(),
            'cmyk.jpg',
            ImageOptions(grayscale=False),
        )[0]

        with Image.open(io.BytesIO(result.output_bytes)) as output:
            self.assertEqual(output.mode, 'RGB')
            self.assertFalse(output.info.get('progressive', False))
        self.assertTrue(result.was_converted)

    def test_small_landscape_separator_is_not_split(self):
        results = process_image(
            self._png((400, 67)),
            'separator.png',
            self._x3_options(),
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(self._size(results[0].output_bytes), (400, 67))

    def test_narrow_banner_is_resized_without_splitting(self):
        results = process_image(
            self._png((600, 116)),
            'banner.png',
            self._x3_options(),
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(self._size(results[0].output_bytes), (528, 102))

    def test_ordinary_landscape_is_resized_without_rotation(self):
        results = process_image(
            self._png((589, 458)),
            'illustration.png',
            self._x3_options(),
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(self._size(results[0].output_bytes), (528, 411))

    def test_auto_crop_trims_uniform_margins_and_scales_content(self):
        image = Image.new('RGB', (412, 450), 'white')
        ImageDraw.Draw(image).rectangle((21, 16, 389, 424), fill='black')

        result = process_image(
            self._encode(image, 'PNG'),
            'illustration.png',
            ImageOptions(max_width=528, max_height=792, auto_crop=True),
        )[0]

        self.assertEqual(self._size(result.output_bytes), (528, 583))
        self.assertIn('auto-cropped 412x450→385x425', result.details)

    def test_auto_crop_preserves_protected_cover(self):
        image = Image.new('RGB', (412, 450), 'white')
        ImageDraw.Draw(image).rectangle((21, 16, 389, 424), fill='black')

        result = process_image(
            self._encode(image, 'PNG'),
            'front.png',
            ImageOptions(max_width=528, max_height=792, auto_crop=True),
            protect_auto_crop=True,
        )[0]

        self.assertEqual(self._size(result.output_bytes), (412, 450))
        self.assertNotIn('auto-cropped', result.details)

    def test_large_double_page_spread_is_split(self):
        results = process_image(
            self._png((1600, 700)),
            'spread.png',
            self._x3_options(),
        )

        self.assertEqual([result.new_filename for result in results], [
            'spread_part1.jpg',
            'spread_part2.jpg',
        ])

    def test_split_epub_images_are_manifested_and_referenced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / 'input.epub'
            output_path = root / 'output.epub'
            write_spread_epub(input_path, self._png((1600, 700)))

            report = process_epub(
                str(input_path),
                str(output_path),
                ProcessingOptions(
                    max_width=528,
                    max_height=792,
                    eink_quantize=False,
                    light_novel_mode=True,
                    remove_fonts=False,
                    remove_unused_css=False,
                    generate_missing_cover=False,
                    clean_metadata=False,
                    text_cleanup=False,
                ),
            )

            self.assertTrue(report.success, report.error)
            with zipfile.ZipFile(output_path) as epub:
                image_entries = {
                    name for name in epub.namelist()
                    if name.lower().endswith(('.jpg', '.jpeg', '.png'))
                }
                opf = etree.fromstring(epub.read('OEBPS/content.opf'))
                chapter = etree.fromstring(epub.read('OEBPS/chapter.xhtml'))

            manifest_images = {
                f"OEBPS/{item.get('href')}"
                for item in opf.findall('.//{http://www.idpf.org/2007/opf}item')
                if item.get('media-type') == 'image/jpeg'
            }
            chapter_sources = [
                image.get('src')
                for image in chapter.findall('.//{http://www.w3.org/1999/xhtml}img')
            ]
            self.assertEqual(image_entries, {
                'OEBPS/Images/spread_part1.jpg',
                'OEBPS/Images/spread_part2.jpg',
            })
            self.assertEqual(manifest_images, image_entries)
            self.assertEqual(chapter_sources, [
                'Images/spread_part1.jpg',
                'Images/spread_part2.jpg',
            ])

    @staticmethod
    def _x3_options():
        return ImageOptions(
            max_width=528,
            max_height=792,
            eink_quantize=False,
            light_novel_mode=True,
        )

    @staticmethod
    def _png(size):
        image = Image.new('RGB', size, 'white')
        return LightNovelImageTests._encode(image, 'PNG')

    @staticmethod
    def _encode(image, image_format):
        output = io.BytesIO()
        image.save(output, format=image_format)
        return output.getvalue()

    @staticmethod
    def _size(image_bytes):
        with Image.open(io.BytesIO(image_bytes)) as image:
            return image.size


if __name__ == '__main__':
    unittest.main()