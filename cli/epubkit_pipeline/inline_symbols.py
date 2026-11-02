"""Conservative image-to-text conversion for reviewed inline glyph assets.

Only exact original asset hashes in the registry are admitted. This is opt-in:
Unicode coverage must be supplied by the selected reader font. Filenames, alt
text, dimensions and OCR never establish a symbol's identity.
"""
import hashlib
from pathlib import Path
from urllib.parse import unquote, urlsplit
from lxml import etree

# Reviewed publisher glyphs from the retained No Game No Life Vol.7 original.
# Double-em units preserve the distinct nominal line lengths (27/49/53/79/104px)
# rather than collapsing every line image to one em dash. The 49px variant is
# approximately four em, like the 53px variant, not the 27px two-em line.
VERIFIED_SYMBOLS = {
    'db8d87c7f9248c3e9a6cbf7c75e70f73f3e1bd623124dce701d6eb8571a89b1c': '♥',
    '7db143adcbee7abcc57056b139ec63da20fe27243ec200a84e15235f1f9cd9e2': '♪',
    '97d5facc8d5a3c21a33d27d975fbcf5b1fe39e1b2b388ec08549abcf04c58205': '☆',
    '530f2f9457c8feb062513e065901e52d4e05d92d616f8c4ff46825b4af2e7d77': '⸺',
    '5b0a727f9ab29ef03ea88cd9ffd82fd42d49337a1e911188c358b034e1f05b79': '⸺⸺',
    '2258ce0b38eedcac3706e4c94ce2dc890e93c4b567e801f30d527ef103b4204f': '⸺⸺',
    '137a89cf6ab0aca6cb2b6af417d80de171a166472c67ba219940818a2e15b124': '⸺⸺⸺',
    '3c1a8c137053f2ab26a565e65ef89916a43b18287982fbc88841ea0741d3e3c9': '⸺⸺⸺⸺',
}


def replace_verified_symbols(work_dir, xhtml_files):
    root = Path(work_dir).resolve()
    replacements = []
    for filename in xhtml_files:
        path = Path(filename)
        parser = etree.XMLParser(resolve_entities=False, no_network=True)
        try:
            tree = etree.parse(str(path), parser)
        except etree.XMLSyntaxError:
            continue  # Do not infer identity or repair malformed content here.
        changed = False
        for image in tree.xpath('//*[local-name()="img"]'):
            source = urlsplit(image.get('src', ''))
            if source.scheme or source.netloc or not source.path:
                continue
            asset = (path.parent / unquote(source.path)).resolve()
            if not asset.is_relative_to(root) or not asset.is_file():
                continue
            digest = hashlib.sha256(asset.read_bytes()).hexdigest()
            text = VERIFIED_SYMBOLS.get(digest)
            if text is None:
                continue
            paragraph = next((p for p in image.iterancestors()
                              if isinstance(p.tag, str) and etree.QName(p).localname in {'p', 'li', 'td', 'th'}), None)
            if paragraph is None or not ''.join(paragraph.itertext()).strip():
                continue  # Preserve standalone ornaments/illustrations.
            namespace = etree.QName(image).namespace
            image.tag = f'{{{namespace}}}span' if namespace else 'span'
            for attribute in ('src', 'srcset', 'alt', 'width', 'height'):
                image.attrib.pop(attribute, None)
            image.text = text
            replacements.append({'xhtml': path.relative_to(root).as_posix(),
                                 'asset': asset.relative_to(root).as_posix(),
                                 'sha256': digest, 'text': text})
            changed = True
        if changed:
            tree.write(str(path), xml_declaration=True, encoding='utf-8', pretty_print=False)
    return replacements
