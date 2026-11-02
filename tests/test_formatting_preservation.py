import sys
import unittest
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'cli' / 'epubkit_pipeline'))
from html_cleaner import apply_crosspoint_xhtml_fixes


class FormattingPreservationTests(unittest.TestCase):
    def test_only_image_sizing_guard_is_injected(self):
        source = b'''<html xmlns="http://www.w3.org/1999/xhtml"><head>
<style type="text/css">table{table-layout:auto}pre{white-space:pre}.caps{font-variant:small-caps}</style>
</head><body style="overflow-wrap:normal"><h1 class="caps">Heading</h1>
<table style="border:1px solid black"><tr><td>Cell</td></tr></table>
<pre>  spaced\ntext</pre><p>A <span style="font-size:150%">large</span> word.</p>
<img src="image.jpg" width="1400" height="1000"/></body></html>'''
        output, changes = apply_crosspoint_xhtml_fixes(source)
        doc = etree.fromstring(output)
        styles = doc.xpath('//*[local-name()="style"]/text()')
        self.assertEqual(styles[0], 'table{table-layout:auto}pre{white-space:pre}.caps{font-variant:small-caps}')
        self.assertEqual(styles[1:], ['img,svg{max-width:100%;height:auto}'])
        self.assertEqual(doc.xpath('//*[local-name()="body"]/@style'), ['overflow-wrap:normal'])
        self.assertEqual(doc.xpath('//*[local-name()="pre"]/text()'), ['  spaced\ntext'])
        self.assertEqual(doc.xpath('//*[local-name()="table"]/@style'), ['border:1px solid black'])
        self.assertEqual(doc.xpath('//*[local-name()="span"]/@style'), ['font-size:150%'])
        self.assertFalse(doc.xpath('//*[local-name()="img"]/@width'))
        second, _ = apply_crosspoint_xhtml_fixes(output)
        self.assertEqual(second, output)


if __name__ == '__main__':
    unittest.main()
