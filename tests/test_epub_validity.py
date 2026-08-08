import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from lxml import etree


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = PROJECT_ROOT / 'cli' / 'epubkit_pipeline'
sys.path.insert(0, str(PIPELINE_DIR))

from epub_packager import is_valid_epub  # noqa: E402
from epub_processor import ProcessingOptions, process_epub  # noqa: E402
from epub_structure import add_image_to_opf, build_rename_map, fix_toc, update_opf  # noqa: E402
from html_cleaner import repair_html  # noqa: E402
from textsplit import split_epub_text, visible_text  # noqa: E402


class EpubValidityTests(unittest.TestCase):
    maxDiff = None

    def test_image_rename_map_always_uses_epub_paths(self):
        rename_map = build_rename_map('', {r'Images\09.gif': '09.jpg'})

        self.assertEqual(rename_map, {'Images/09.gif': 'Images/09.jpg'})

    def test_epub3_nav_semantics_survive_processing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / 'input.epub'
            output_path = root / 'output.epub'
            self._write_epub3_fixture(input_path)

            report = process_epub(
                str(input_path),
                str(output_path),
                ProcessingOptions(
                    remove_fonts=False,
                    remove_unused_css=False,
                    generate_missing_cover=False,
                    clean_metadata=False,
                    text_cleanup=False,
                ),
            )

            self.assertTrue(report.success, report.error)
            with zipfile.ZipFile(output_path) as zf:
                self.assertNotIn('OEBPS/toc.ncx', zf.namelist())
                nav_tree = etree.fromstring(zf.read('OEBPS/nav.xhtml'))
                ns = {
                    'xhtml': 'http://www.w3.org/1999/xhtml',
                    'epub': 'http://www.idpf.org/2007/ops',
                }
                nav = nav_tree.find('.//xhtml:nav', ns)
                self.assertIsNotNone(nav)
                self.assertEqual(nav.get('{http://www.idpf.org/2007/ops}type'), 'toc')
                self.assertEqual(nav.get('role'), 'doc-toc')
                self.assertEqual(nav_tree.get('lang'), 'en')
                self.assertEqual(nav_tree.get('{http://www.w3.org/XML/1998/namespace}lang'), 'en')
                body = nav_tree.find('.//xhtml:body', ns)
                self.assertEqual(body.get('dir'), 'ltr')

            valid, error = is_valid_epub(str(output_path))
            self.assertTrue(valid, error)

    def test_repair_html_outputs_xml_well_formed_xhtml(self):
        repaired = repair_html(
            b'<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
            b'<body><p>Line<br><img src="cover.png"></p><hr></body></html>'
        )

        self.assertTrue(repaired.startswith(b"<?xml"))
        self.assertIn(b'<!DOCTYPE html>', repaired)
        self.assertIn(b'<br/>', repaired)
        self.assertIn(b'<img src="cover.png"/>', repaired)
        self.assertIn(b'<meta charset="utf-8"/>', repaired)
        root = etree.fromstring(repaired)
        self.assertEqual(root.tag, '{http://www.w3.org/1999/xhtml}html')

    def test_encryption_manifest_is_pruned_after_font_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / 'input.epub'
            output_path = root / 'output.epub'
            self._write_font_obfuscation_fixture(input_path)

            report = process_epub(
                str(input_path),
                str(output_path),
                ProcessingOptions(
                    remove_fonts=True,
                    remove_unused_css=False,
                    generate_missing_cover=False,
                    clean_metadata=False,
                    text_cleanup=False,
                ),
            )

            self.assertTrue(report.success, report.error)
            with zipfile.ZipFile(output_path) as zf:
                self.assertNotIn('META-INF/encryption.xml', zf.namelist())
            valid, error = is_valid_epub(str(output_path))
            self.assertTrue(valid, error)

    def test_fix_toc_generates_ncx_uid_and_relative_src(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_opf_tree(
                root,
                opf_rel='OEBPS/content.opf',
                opf_xml='''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="BookId">book-uid</dc:identifier>
    <dc:title>Sample Book</dc:title>
    <dc:creator>Sample Author</dc:creator>
  </metadata>
  <manifest>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
    <item id="ncx" href="../toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine>
    <itemref idref="chapter"/>
  </spine>
</package>''',
                extra_files={
                    'OEBPS/chapter.xhtml': self._chapter_xhtml('Chapter 1'),
                },
            )

            changed, _ = fix_toc(str(root), str(root / 'OEBPS' / 'content.opf'))
            self.assertTrue(changed)

            ncx = etree.parse(str(root / 'toc.ncx'))
            ns = {'ncx': 'http://www.daisy.org/z3986/2005/ncx/'}
            meta = {
                item.get('name'): item.get('content')
                for item in ncx.findall('.//ncx:meta', ns)
            }
            self.assertEqual(meta.get('dtb:uid'), 'book-uid')
            self.assertEqual(meta.get('dtb:totalPageCount'), '0')
            self.assertEqual(meta.get('dtb:maxPageNumber'), '0')
            self.assertEqual(
                ncx.find('.//ncx:content', ns).get('src'),
                'OEBPS/chapter.xhtml',
            )

    def test_fix_toc_sets_spine_toc_for_existing_ncx(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_opf_tree(
                root,
                opf_rel='OEBPS/content.opf',
                opf_xml='''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="BookId">book-uid</dc:identifier>
    <dc:title>Sample Book</dc:title>
  </metadata>
  <manifest>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
    <item id="existing-ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine>
    <itemref idref="chapter"/>
  </spine>
</package>''',
                extra_files={
                    'OEBPS/chapter.xhtml': self._chapter_xhtml('Chapter 1'),
                    'OEBPS/toc.ncx': '''<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="book-uid"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>Sample Book</text></docTitle>
  <navMap>
    <navPoint id="navPoint-1" playOrder="1">
      <navLabel><text>Chapter 1</text></navLabel>
      <content src="chapter.xhtml"/>
    </navPoint>
  </navMap>
</ncx>''',
                },
            )

            changed, _ = fix_toc(str(root), str(root / 'OEBPS' / 'content.opf'))
            self.assertTrue(changed)
            opf = etree.parse(str(root / 'OEBPS' / 'content.opf'))
            spine = opf.find('.//{http://www.idpf.org/2007/opf}spine')
            self.assertEqual(spine.get('toc'), 'existing-ncx')

    def test_png_manifest_media_type_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            opf_path = root / 'content.opf'
            opf_path.write_text(
                '''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
  <manifest>
    <item id="img-old" href="images/old.png" media-type="image/png"/>
  </manifest>
  <spine/>
</package>''',
                encoding='utf-8',
            )

            add_image_to_opf(str(opf_path), 'images/cover.png', 'cover-image')
            update_opf(str(opf_path), {'images/old.png': 'images/new.png'})

            opf = etree.parse(str(opf_path))
            items = {
                item.get('id'): item
                for item in opf.findall('.//{http://www.idpf.org/2007/opf}item')
            }
            self.assertEqual(items['img-old'].get('href'), 'images/new.png')
            self.assertEqual(items['img-old'].get('media-type'), 'image/png')
            self.assertEqual(items['cover-image'].get('media-type'), 'image/png')

    @unittest.skipUnless(shutil.which('epubcheck'), 'epubcheck is not installed')
    def test_optional_epubcheck_accepts_processed_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / 'input.epub'
            output_path = root / 'output.epub'
            self._write_epub3_fixture(input_path)

            report = process_epub(
                str(input_path),
                str(output_path),
                ProcessingOptions(
                    remove_fonts=False,
                    remove_unused_css=False,
                    generate_missing_cover=False,
                    clean_metadata=False,
                    text_cleanup=False,
                ),
            )
            self.assertTrue(report.success, report.error)

            result = subprocess.run(
                ['epubcheck', str(output_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_chapter_metadata_preserved_through_optimization(self):
        """Existing NCX navMap entries (labels, hierarchy) must survive processing."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / 'input.epub'
            output_path = root / 'output.epub'
            self._write_custom_navmap_fixture(input_path)

            report = process_epub(
                str(input_path),
                str(output_path),
                ProcessingOptions(
                    remove_fonts=False,
                    remove_unused_css=False,
                    generate_missing_cover=False,
                    clean_metadata=False,
                    text_cleanup=False,
                ),
            )

            self.assertTrue(report.success, report.error)
            with zipfile.ZipFile(output_path) as zf:
                ncx = zf.read('OEBPS/toc.ncx').decode('utf-8')
            # Custom labels and nested navPoint hierarchy preserved verbatim
            self.assertIn('<text>Prologue: A Custom Title</text>', ncx)
            self.assertIn('<text>Part One</text>', ncx)
            self.assertIn('<text>Nested Section</text>', ncx)
            self.assertIn('id="custom-np-1"', ncx)
            self.assertIn('id="custom-np-2"', ncx)
            self.assertIn('id="custom-np-2-1"', ncx)
            self.assertIn('chapter1.xhtml#section-2', ncx)

    def test_mimetype_is_first_entry_in_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / 'input.epub'
            output_path = root / 'output.epub'
            self._write_epub3_fixture(input_path)

            report = process_epub(
                str(input_path),
                str(output_path),
                ProcessingOptions(
                    remove_fonts=False,
                    remove_unused_css=False,
                    generate_missing_cover=False,
                    clean_metadata=False,
                    text_cleanup=False,
                ),
            )

            self.assertTrue(report.success, report.error)
            with zipfile.ZipFile(output_path) as zf:
                names = zf.namelist()
                self.assertEqual(names[0], 'mimetype')
                info = zf.getinfo('mimetype')
                self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
                self.assertEqual(zf.read('mimetype').decode('utf-8'), 'application/epub+zip')

    def test_default_pipeline_preserves_large_spine_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / 'input.epub'
            output_path = root / 'output.epub'
            chapter = self._chapter_xhtml('Large Chapter').replace('Body text.', 'word ' * 2500)
            self._write_epub(
                input_path,
                {
                    'mimetype': ('application/epub+zip', zipfile.ZIP_STORED),
                    'META-INF/container.xml': (self._container_xml('OEBPS/content.opf'), zipfile.ZIP_DEFLATED),
                    'OEBPS/content.opf': ('''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="BookId">large-section-fixture</dc:identifier>
    <dc:title>Fixture</dc:title>
  </metadata>
  <manifest>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>''', zipfile.ZIP_DEFLATED),
                    'OEBPS/chapter.xhtml': (chapter, zipfile.ZIP_DEFLATED),
                },
            )

            report = process_epub(
                str(input_path),
                str(output_path),
                ProcessingOptions(
                    remove_fonts=False,
                    remove_unused_css=False,
                    generate_missing_cover=False,
                    clean_metadata=False,
                    text_cleanup=False,
                ),
            )

            self.assertTrue(report.success, report.error)
            with zipfile.ZipFile(output_path) as zf:
                content_files = [
                    name for name in zf.namelist()
                    if name.lower().endswith(('.xhtml', '.html', '.htm'))
                ]
                opf = etree.fromstring(zf.read('OEBPS/content.opf'))
            spine = opf.find('.//{http://www.idpf.org/2007/opf}spine')
            self.assertEqual(content_files, ['OEBPS/chapter.xhtml'])
            self.assertEqual(len(spine), 1)

    def test_textsplit_splits_oversized_paragraphs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            epub_path = root / 'book.epub'

            sentences = ' '.join(f'This is sentence number {i} of a very long paragraph.'
                                 for i in range(80))
            chapter = (
                '<?xml version="1.0" encoding="utf-8"?>'
                '<html xmlns="http://www.w3.org/1999/xhtml">'
                '<head><title>Big</title></head>'
                f'<body><p>{sentences}</p></body></html>'
            )
            self._write_epub(
                epub_path,
                {
                    'mimetype': ('application/epub+zip', zipfile.ZIP_STORED),
                    'META-INF/container.xml': (self._container_xml('OEBPS/content.opf'), zipfile.ZIP_DEFLATED),
                    'OEBPS/content.opf': ('''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="BookId">split-fixture</dc:identifier>
    <dc:title>Fixture</dc:title>
  </metadata>
  <manifest>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="chapter"/>
  </spine>
</package>''', zipfile.ZIP_DEFLATED),
                    'OEBPS/chapter.xhtml': (chapter, zipfile.ZIP_DEFLATED),
                },
            )

            before_text = visible_text(chapter)
            result = split_epub_text(str(epub_path))
            self.assertGreaterEqual(result['paras'], 1)

            with zipfile.ZipFile(epub_path) as zf:
                names = zf.namelist()
                self.assertEqual(names[0], 'mimetype')
                out = zf.read('OEBPS/chapter.xhtml').decode('utf-8')

            # Paragraph was split into multiple siblings, none exceeding PARA_LIMIT
            from textsplit import PARA_LIMIT
            paras = re.findall(r'<p\b[^>]*>(.*?)</p>', out, re.S)
            self.assertGreater(len(paras), 1)
            for p in paras:
                self.assertLessEqual(len(p), PARA_LIMIT)
            # Visible text is unchanged
            self.assertEqual(visible_text(out), before_text)

    def _write_epub3_fixture(self, path: Path) -> None:
        self._write_epub(
            path,
            {
                'mimetype': ('application/epub+zip', zipfile.ZIP_STORED),
                'META-INF/container.xml': (self._container_xml('OEBPS/content.opf'), zipfile.ZIP_DEFLATED),
                'OEBPS/content.opf': ('''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="BookId">epub3-fixture</dc:identifier>
    <dc:title>Fixture</dc:title>
    <dc:creator>Author</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
  </manifest>
  <spine>
    <itemref idref="chapter"/>
  </spine>
</package>''', zipfile.ZIP_DEFLATED),
                'OEBPS/chapter.xhtml': (self._chapter_xhtml('Chapter 1'), zipfile.ZIP_DEFLATED),
                'OEBPS/nav.xhtml': ('''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en">
  <head>
    <title>TOC</title>
    <meta charset="utf-8"/>
  </head>
  <body dir="ltr">
    <nav epub:type="toc" role="doc-toc">
      <ol><li><a href="chapter.xhtml">Chapter 1</a></li></ol>
    </nav>
  </body>
</html>''', zipfile.ZIP_DEFLATED),
            },
        )

    def _write_custom_navmap_fixture(self, path: Path) -> None:
        self._write_epub(
            path,
            {
                'mimetype': ('application/epub+zip', zipfile.ZIP_STORED),
                'META-INF/container.xml': (self._container_xml('OEBPS/content.opf'), zipfile.ZIP_DEFLATED),
                'OEBPS/content.opf': ('''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="BookId">navmap-fixture</dc:identifier>
    <dc:title>Fixture</dc:title>
  </metadata>
  <manifest>
    <item id="prologue" href="prologue.xhtml" media-type="application/xhtml+xml"/>
    <item id="chapter1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="prologue"/>
    <itemref idref="chapter1"/>
  </spine>
</package>''', zipfile.ZIP_DEFLATED),
                'OEBPS/prologue.xhtml': (self._chapter_xhtml('Prologue'), zipfile.ZIP_DEFLATED),
                'OEBPS/chapter1.xhtml': ('''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Chapter 1</title></head>
  <body><h1>Chapter 1</h1><p>Text.</p><p id="section-2">Section two.</p></body>
</html>''', zipfile.ZIP_DEFLATED),
                'OEBPS/toc.ncx': ('''<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="navmap-fixture"/>
    <meta name="dtb:depth" content="2"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>Fixture</text></docTitle>
  <navMap>
    <navPoint id="custom-np-1" playOrder="1">
      <navLabel><text>Prologue: A Custom Title</text></navLabel>
      <content src="prologue.xhtml"/>
    </navPoint>
    <navPoint id="custom-np-2" playOrder="2">
      <navLabel><text>Part One</text></navLabel>
      <content src="chapter1.xhtml"/>
      <navPoint id="custom-np-2-1" playOrder="3">
        <navLabel><text>Nested Section</text></navLabel>
        <content src="chapter1.xhtml#section-2"/>
      </navPoint>
    </navPoint>
  </navMap>
</ncx>''', zipfile.ZIP_DEFLATED),
            },
        )

    def _write_font_obfuscation_fixture(self, path: Path) -> None:
        self._write_epub(
            path,
            {
                'mimetype': ('application/epub+zip', zipfile.ZIP_STORED),
                'META-INF/container.xml': (self._container_xml('OEBPS/content.opf'), zipfile.ZIP_DEFLATED),
                'META-INF/encryption.xml': ('''<?xml version="1.0" encoding="utf-8"?>
<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
            xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
  <enc:EncryptedData>
    <enc:EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/>
    <enc:CipherData><enc:CipherReference URI="../OEBPS/fonts/font.otf"/></enc:CipherData>
  </enc:EncryptedData>
  <enc:EncryptedData>
    <enc:EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/>
    <enc:CipherData><enc:CipherReference URI="../OEBPS/fonts/missing.otf"/></enc:CipherData>
  </enc:EncryptedData>
</encryption>''', zipfile.ZIP_DEFLATED),
                'OEBPS/content.opf': ('''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="BookId">font-fixture</dc:identifier>
    <dc:title>Fixture</dc:title>
  </metadata>
  <manifest>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
    <item id="font" href="fonts/font.otf" media-type="font/otf"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="chapter"/>
  </spine>
</package>''', zipfile.ZIP_DEFLATED),
                'OEBPS/chapter.xhtml': (self._chapter_xhtml('Chapter 1'), zipfile.ZIP_DEFLATED),
                'OEBPS/toc.ncx': ('''<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="font-fixture"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>Fixture</text></docTitle>
  <navMap>
    <navPoint id="navPoint-1" playOrder="1">
      <navLabel><text>Chapter 1</text></navLabel>
      <content src="chapter.xhtml"/>
    </navPoint>
  </navMap>
</ncx>''', zipfile.ZIP_DEFLATED),
                'OEBPS/fonts/font.otf': (b'fake-font-data', zipfile.ZIP_DEFLATED),
            },
        )

    def _write_opf_tree(self, root: Path, opf_rel: str, opf_xml: str, extra_files: dict[str, str]) -> None:
        (root / 'META-INF').mkdir(parents=True, exist_ok=True)
        (root / 'META-INF' / 'container.xml').write_text(self._container_xml(opf_rel), encoding='utf-8')
        opf_path = root / opf_rel
        opf_path.parent.mkdir(parents=True, exist_ok=True)
        opf_path.write_text(opf_xml, encoding='utf-8')
        for rel_path, content in extra_files.items():
            file_path = root / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding='utf-8')

    def _write_epub(self, path: Path, entries: dict[str, tuple[bytes | str, int]]) -> None:
        with zipfile.ZipFile(path, 'w') as zf:
            for name, (content, compression) in entries.items():
                zf.writestr(name, content, compress_type=compression)

    def _container_xml(self, opf_rel: str) -> str:
        return f'''<?xml version="1.0" encoding="utf-8"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles><rootfile full-path="{opf_rel}" media-type="application/oebps-package+xml"/></rootfiles>
</container>'''

    def _chapter_xhtml(self, title: str) -> str:
        return f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>{title}</title></head>
  <body><h1>{title}</h1><p>Body text.</p></body>
</html>'''


if __name__ == '__main__':
    unittest.main()
