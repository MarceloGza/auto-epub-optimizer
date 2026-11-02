import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'cli' / 'epubkit_pipeline'))
sys.path.insert(0, str(ROOT / 'tests'))
from image_processor import ImageOptions, _handle_light_novel, process_image
from epub_processor import ProcessingOptions, process_epub
from test_image_processor import write_spread_epub


class ArtworkDetailTests(unittest.TestCase):
    def test_grayscale_is_single_channel_baseline_jpeg_at_requested_quality(self):
        source = io.BytesIO()
        Image.new('RGB', (40, 60), (60, 120, 180)).save(source, format='PNG')
        result = process_image(source.getvalue(), 'art.png', ImageOptions(quality=95))[0]
        expected = io.BytesIO()
        Image.new('L', (40, 60), 100).save(expected, format='JPEG', quality=95)
        with Image.open(io.BytesIO(result.output_bytes)) as output:
            self.assertEqual(output.mode, 'L')
            self.assertEqual(output.layers, 1)
            self.assertFalse(output.info.get('progressive', False))
            with Image.open(io.BytesIO(expected.getvalue())) as quality_reference:
                self.assertEqual(output.quantization[0], quality_reference.quantization[0])

    def test_rotation_candidate_has_two_detail_halves_then_full_overview(self):
        image = Image.new('RGB', (1401, 1000), 'white')
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 699, 999), fill='black')
        draw.point((1400, 0), fill='red')
        views = _handle_light_novel(image, True, 528, 792)
        self.assertEqual(len(views), 3)
        self.assertEqual([v.size for v in views], [(701, 1000), (700, 1000), (1000, 1401)])
        self.assertEqual(views[0].tobytes(), image.crop((700, 0, 1401, 1000)).tobytes())
        self.assertEqual(views[1].tobytes(), image.crop((0, 0, 700, 1000)).tobytes())
        self.assertEqual(views[2].tobytes(), image.rotate(90, expand=True).tobytes())

    def test_rotation_direction_changes_overview_not_detail_order(self):
        image = Image.new('RGB', (1400, 1000), 'white')
        ImageDraw.Draw(image).rectangle((0, 0, 699, 999), fill='black')
        views = _handle_light_novel(image, False, 528, 792)
        self.assertEqual(len(views), 3)
        self.assertEqual(views[2].tobytes(), image.rotate(-90, expand=True).tobytes())
        self.assertEqual(views[0].getpixel((0, 0)), (255, 255, 255))

    def test_three_views_survive_epub_manifest_and_reference_updates(self):
        image = Image.new('RGB', (1400, 1000), 'white')
        source = io.BytesIO()
        image.save(source, format='PNG')
        with tempfile.TemporaryDirectory() as tmp:
            source_path = Path(tmp) / 'source.epub'
            output_path = Path(tmp) / 'result.epub'
            write_spread_epub(source_path, source.getvalue())
            report = process_epub(str(source_path), str(output_path), ProcessingOptions(
                max_width=528, max_height=792, light_novel_mode=True, quality=95))
            self.assertTrue(report.success, report.error)
            with zipfile.ZipFile(output_path) as z:
                self.assertIsNone(z.testzip())
                chapter = etree.fromstring(z.read('OEBPS/chapter.xhtml'))
                opf = etree.fromstring(z.read('OEBPS/content.opf'))
                sources = chapter.xpath('//*[local-name()="img"]/@src')
                self.assertEqual(sources, [f'Images/spread_part{i}.jpg' for i in (1, 2, 3)])
                manifested = opf.xpath('//*[local-name()="item" and @media-type="image/jpeg"]/@href')
                self.assertEqual(set(manifested), set(sources))
                for src in sources:
                    with Image.open(io.BytesIO(z.read('OEBPS/' + src))) as view:
                        self.assertLessEqual(view.width, 528)
                        self.assertLessEqual(view.height, 792)


if __name__ == '__main__':
    unittest.main()
