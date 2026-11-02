import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'cli/epubkit_pipeline'))
sys.path.insert(0, str(ROOT / 'tests'))
from epub_processor import ProcessingOptions, process_epub
from test_image_processor import write_spread_epub

FIXTURE = ROOT / 'tests/fixtures/inline_symbols'


def symbol_epub(path):
    write_spread_epub(path, (FIXTURE / 'Art_heart.jpg').read_bytes())
    with zipfile.ZipFile(path) as book:
        files = {n: book.read(n) for n in book.namelist()}
    files['OEBPS/chapter.xhtml'] = b'''<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Symbols</title></head><body><p>Before <em><img id="heart" src="Images/spread.png" alt="image"/></em> after.</p></body></html>'''
    with zipfile.ZipFile(path, 'w') as book:
        for name, data in files.items():
            book.writestr(name, data, compress_type=zipfile.ZIP_STORED if name == 'mimetype' else zipfile.ZIP_DEFLATED)


class InlineSymbolTests(unittest.TestCase):
    def test_cli_explicitly_opts_in_and_defaults_to_preservation(self):
        sys.path.insert(0, str(ROOT / 'cli'))
        from optimize import build_parser, build_options, apply_device_defaults
        parser = build_parser()
        for flags, expected in [([], False), (['--unicode-symbols'], True)]:
            args = parser.parse_args(['fixture.epub', *flags])
            apply_device_defaults(args)
            self.assertEqual(build_options(args).unicode_symbols, expected)

    def test_opt_in_pipeline_replaces_verified_heart_without_breaking_text_or_emphasis(self):
        options = ProcessingOptions()
        options.unicode_symbols = True
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / 'source.epub', Path(tmp) / 'output.epub'
            symbol_epub(source)
            report = process_epub(str(source), str(target), options)
            self.assertTrue(report.success, report.error)
            with zipfile.ZipFile(target) as book:
                tree = etree.fromstring(book.read('OEBPS/chapter.xhtml'))
                self.assertEqual(' '.join(''.join(tree.xpath('//*[local-name()="body"]')[0].itertext()).split()), 'Before ♥ after.')
                self.assertEqual(tree.xpath('string(//*[local-name()="em"]/*[@id="heart"])'), '♥')
                self.assertEqual(tree.xpath('//*[local-name()="img"]'), [])
            self.assertEqual(report.unicode_symbols_replaced, 1)


    def test_known_registry_allows_renamed_assets_but_not_lookalikes_or_ornaments(self):
        from inline_symbols import replace_verified_symbols, VERIFIED_SYMBOLS
        expected = {'Art_heart.jpg': '♥', 'Art_music.jpg': '♪', 'Art_star.jpg': '☆',
                    'Art_mdash2.jpg': '⸺', 'Art_mdash2a.jpg': '⸺⸺', 'Art_mdash4.jpg': '⸺⸺',
                    'Art_mdash6.jpg': '⸺⸺⸺', 'Art_mdash8.jpg': '⸺⸺⸺⸺'}
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            tags = []
            for i, (name, value) in enumerate(expected.items()):
                (folder / f'{i}.jpg').write_bytes((FIXTURE / name).read_bytes())
                tags.append(f'<img src="{i}.jpg"/>')
            (folder / 'unknown.jpg').write_bytes((FIXTURE / 'Art_heart.jpg').read_bytes() + b'changed')
            page = folder / 'chapter.xhtml'
            page.write_text('<html><body><p>Start' + ''.join(tags) + 'End<img src="unknown.jpg"/></p><p><img src="0.jpg"/></p></body></html>')
            report = replace_verified_symbols(folder, [page])
            self.assertEqual([x['text'] for x in report], list(expected.values()))
            tree = etree.parse(str(page))
            self.assertEqual(tree.xpath('//img/@src'), ['unknown.jpg', '0.jpg'])
            snapshot = page.read_bytes()
            self.assertEqual(replace_verified_symbols(folder, [page]), [])
            self.assertEqual(page.read_bytes(), snapshot)
            self.assertTrue(set(expected.values()).issubset(set(VERIFIED_SYMBOLS.values())))

    def test_reviewed_ngnl_variants_keep_fill_note_types_and_dash_lengths(self):
        import hashlib
        import json
        from inline_symbols import replace_verified_symbols
        variants = FIXTURE / 'variants'
        cases = json.loads((variants / 'reviewed.json').read_text())
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            tags, expected, remaining = [], [], []
            for i, case in enumerate(cases):
                data = (variants / (case['sha256'] + '.jpg')).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), case['sha256'])
                name = f'renamed-{i}.jpg'
                (folder / name).write_bytes(data)
                tags.append(f'<img src="{name}"/>')
                if case['text'] is None:
                    remaining.append(name)
                else:
                    expected.append(case['text'])
            # Mutation of a known variant must not acquire its identity.
            known_data = (variants / (cases[0]['sha256'] + '.jpg')).read_bytes()
            (folder / 'mutated.jpg').write_bytes(known_data + b'not-identical')
            page = folder / 'chapter.xhtml'
            page.write_text('<html><body><p>Start' + ''.join(tags) + '<img src="mutated.jpg"/>End</p></body></html>')
            report = replace_verified_symbols(folder, [page])
            self.assertEqual([x['text'] for x in report], expected)
            self.assertEqual(etree.parse(str(page)).xpath('//img/@src'), remaining + ['mutated.jpg'])
            self.assertTrue({'♥', '♡', '♪', '♫', '☆', '★', '⸺—', '⸺⸺⸺⸺⸺⸺⸺'}.issubset(set(expected)))

    def test_default_pipeline_preserves_symbol_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / 'source.epub', Path(tmp) / 'output.epub'
            symbol_epub(source)
            report = process_epub(str(source), str(target), ProcessingOptions())
            self.assertTrue(report.success, report.error)
            self.assertEqual(report.unicode_symbols_replaced, 0)
            with zipfile.ZipFile(target) as book:
                self.assertEqual(len(etree.fromstring(book.read('OEBPS/chapter.xhtml')).xpath('//*[local-name()="img"]')), 1)

    def test_external_and_outside_root_sources_are_not_read(self):
        from inline_symbols import replace_verified_symbols
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'work'
            root.mkdir()
            (Path(tmp) / 'outside.jpg').write_bytes((FIXTURE / 'Art_heart.jpg').read_bytes())
            page = root / 'chapter.xhtml'
            page.write_text('<html><body><p>A<img src="../outside.jpg"/><img src="https://example.com/heart.jpg"/>B</p></body></html>')
            before = page.read_bytes()
            self.assertEqual(replace_verified_symbols(root, [page]), [])
            self.assertEqual(page.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
