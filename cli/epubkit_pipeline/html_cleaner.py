"""
HTML cleaner for Xteink X4 EPUB Optimizer.
Handles: HTML repair, unused CSS removal, embedded font removal, whitespace/page-break normalization.
"""

import copy
import re
from io import BytesIO
from pathlib import Path
from typing import Optional

from lxml import etree
import cssutils
import logging

# Suppress cssutils noisy logging
cssutils.log.setLevel(logging.CRITICAL)

XHTML_NS = 'http://www.w3.org/1999/xhtml'
EPUB_NS = 'http://www.idpf.org/2007/ops'
XML_NS = 'http://www.w3.org/XML/1998/namespace'
DEFAULT_DOCTYPE = '<!DOCTYPE html>'
FONT_EXTENSIONS = {'.ttf', '.otf', '.woff', '.woff2', '.eot'}
FONT_MEDIA_TYPES = {
    'application/font-woff', 'application/font-woff2',
    'font/woff', 'font/woff2', 'font/ttf', 'font/otf',
    'application/vnd.ms-opentype', 'application/x-font-ttf',
    'application/x-font-otf', 'application/font-sfnt',
}
HORIZONTAL_WHITESPACE = frozenset({' ', '\t', '\u00a0'})


def repair_html(html_bytes: bytes) -> bytes:
    """
    Repair malformed HTML/XHTML using lxml's recovery parser.
    Returns well-formed XHTML bytes.
    """
    try:
        tree, doctype = _parse_xhtml_document(html_bytes, allow_html_recovery=True)
    except Exception:
        # Completely broken - return as-is
        return html_bytes

    if tree is None:
        return html_bytes

    return _serialize_xhtml_document(tree, doctype)


def remove_unused_css(css_text: str, used_classes: set, used_ids: set, used_elements: set) -> tuple[str, int]:
    """
    Remove CSS rules that don't match any elements in the EPUB content.
    Returns (cleaned CSS text, count of removed rules).
    """
    try:
        sheet = cssutils.parseString(css_text)
    except Exception:
        return css_text, 0

    removed = 0
    rules_to_remove = []

    for rule in sheet:
        if rule.type != rule.STYLE_RULE:
            continue

        # Check if any selector in this rule matches used elements
        selector_text = rule.selectorText
        if not _selector_matches_used(selector_text, used_classes, used_ids, used_elements):
            rules_to_remove.append(rule)
            removed += 1

    for rule in rules_to_remove:
        sheet.deleteRule(rule)

    return sheet.cssText.decode('utf-8') if isinstance(sheet.cssText, bytes) else sheet.cssText, removed


def _selector_matches_used(selector_text: str, used_classes: set, used_ids: set, used_elements: set) -> bool:
    """Check if a CSS selector potentially matches any used classes, IDs, or elements."""
    # Always keep universal selectors, pseudo-elements, @-rules
    if selector_text.strip() in ('*', 'html', 'body'):
        return True

    # Split compound selectors
    selectors = re.split(r'\s*,\s*', selector_text)

    for sel in selectors:
        # Extract classes
        classes = re.findall(r'\.([a-zA-Z_][\w-]*)', sel)
        if classes and any(c in used_classes for c in classes):
            return True

        # Extract IDs
        ids = re.findall(r'#([a-zA-Z_][\w-]*)', sel)
        if ids and any(i in used_ids for i in ids):
            return True

        # Extract element names
        elements = re.findall(r'(?:^|[\s>+~])([a-zA-Z][\w-]*)', sel.strip())
        if elements and any(e.lower() in used_elements for e in elements):
            return True

        # Keep pseudo-class/element rules and attribute selectors
        if '::' in sel or ':' in sel or '[' in sel:
            return True

    return False


def collect_used_selectors(xhtml_bytes: bytes) -> tuple[set, set, set]:
    """
    Parse XHTML and collect all used CSS classes, IDs, and element names.
    Returns (classes, ids, elements).
    """
    classes = set()
    ids = set()
    elements = set()

    try:
        parser = etree.HTMLParser(recover=True)
        tree = etree.fromstring(xhtml_bytes, parser)
    except Exception:
        return classes, ids, elements

    for el in tree.iter():
        # Element name (strip namespace)
        tag = el.tag
        if isinstance(tag, str):
            tag = tag.split('}')[-1] if '}' in tag else tag
            elements.add(tag.lower())

        # Classes
        class_attr = el.get('class', '')
        if class_attr:
            for cls in class_attr.split():
                classes.add(cls)

        # IDs
        id_attr = el.get('id', '')
        if id_attr:
            ids.add(id_attr)

    return classes, ids, elements


def remove_embedded_fonts_from_css(css_text: str) -> tuple[str, int]:
    """
    Remove @font-face rules from CSS.
    Returns (cleaned CSS, count of removed @font-face rules).
    """
    try:
        sheet = cssutils.parseString(css_text)
    except Exception:
        return css_text, 0

    removed = 0
    rules_to_remove = []

    for rule in sheet:
        if rule.type == rule.FONT_FACE_RULE:
            rules_to_remove.append(rule)
            removed += 1

    for rule in rules_to_remove:
        sheet.deleteRule(rule)

    # Also remove font-family declarations that reference custom fonts
    result = sheet.cssText.decode('utf-8') if isinstance(sheet.cssText, bytes) else sheet.cssText
    return result, removed


def find_font_files(file_list: list[str]) -> list[str]:
    """Find all font files in the EPUB by extension and media type."""
    fonts = []
    for filepath in file_list:
        ext = Path(filepath).suffix.lower()
        if ext in FONT_EXTENSIONS:
            fonts.append(filepath)
    return fonts


def is_font_media_type(media_type: str) -> bool:
    """Check if a media type string indicates a font file."""
    return media_type.lower() in FONT_MEDIA_TYPES


def _has_text_content(text: Optional[str]) -> bool:
    """
    Treat visible text as content, and also preserve pure horizontal whitespace.

    This keeps markup like <span>          </span> intact while still allowing
    newline-only formatting whitespace to count as empty.
    """
    if not text:
        return False

    if text.strip():
        return True

    return all(ch in HORIZONTAL_WHITESPACE for ch in text)


def normalize_whitespace(xhtml_bytes: bytes) -> tuple[bytes, int]:
    """
    Strip excessive blank paragraphs and empty divs from XHTML content.
    Returns (cleaned bytes, count of removed elements).
    """
    try:
        tree, doctype = _parse_xhtml_document(xhtml_bytes, allow_html_recovery=True)
    except Exception:
        return xhtml_bytes, 0

    if tree is None:
        return xhtml_bytes, 0

    removed = 0

    # Find consecutive empty paragraphs (more than 2 in a row)
    empty_streak = []
    for el in tree.getroot().iter():
        tag = el.tag.split('}')[-1] if '}' in str(el.tag) else str(el.tag)

        if tag in ('p', 'div'):
            has_text_content = _has_text_content(el.text)
            has_children = len(el) > 0

            # Check if truly empty (no direct text, no child elements)
            if not has_text_content and not has_children:
                empty_streak.append(el)
            else:
                # Reset streak, remove excess empties (keep max 1)
                if len(empty_streak) > 1:
                    for empty_el in empty_streak[1:]:
                        parent = empty_el.getparent()
                        if parent is not None:
                            # Preserve tail text
                            if empty_el.tail:
                                prev = empty_el.getprevious()
                                if prev is not None:
                                    prev.tail = (prev.tail or '') + empty_el.tail
                                else:
                                    parent.text = (parent.text or '') + empty_el.tail
                            parent.remove(empty_el)
                            removed += 1
                empty_streak = []

    # Handle remaining streak
    if len(empty_streak) > 1:
        for empty_el in empty_streak[1:]:
            parent = empty_el.getparent()
            if parent is not None:
                if empty_el.tail:
                    prev = empty_el.getprevious()
                    if prev is not None:
                        prev.tail = (prev.tail or '') + empty_el.tail
                    else:
                        parent.text = (parent.text or '') + empty_el.tail
                parent.remove(empty_el)
                removed += 1

    return _serialize_xhtml_document(tree, doctype), removed


# Attributes to keep during stripping (essential for EPUB rendering)
KEEP_ATTRS = frozenset({
    'class', 'id', 'href', 'src', 'style', 'alt', 'title',
    'type', 'name', 'content', 'charset', 'http-equiv',
    'xmlns', 'version', 'media-type', 'properties',
    'rel', 'media', 'width', 'height', 'colspan', 'rowspan',
    'scope', 'headers', 'border', 'cellpadding', 'cellspacing',
})

STRIP_ATTR_PREFIXES = ('data-', 'aria-')


def strip_unnecessary_attributes(xhtml_bytes: bytes) -> tuple[bytes, int]:
    """
    Strip decorative/accessibility attributes that e-ink readers ignore.
    Reduces file size and parsing overhead for the 380KB-RAM ESP32-C3.

    Keeps: class, id, href, src, style, alt, title, xmlns, and other
    essential XHTML/EPUB attributes.

    Returns (cleaned bytes, count of removed attributes).
    """
    try:
        tree, doctype = _parse_xhtml_document(xhtml_bytes, allow_html_recovery=True)
    except Exception:
        return xhtml_bytes, 0

    if tree is None:
        return xhtml_bytes, 0

    removed = 0

    for el in tree.getroot().iter():
        if not isinstance(el.tag, str):
            continue

        attrs_to_remove = []
        tag_local = _local_name(el.tag).lower()
        for attr in list(el.attrib):
            # Get local attribute name (strip namespace)
            attr_local = _local_name(attr)
            attr_lower = attr.lower()

            # Skip namespace declarations
            if attr.startswith('{') and attr_local in ('xmlns',):
                continue

            if attr.startswith(f'{{{EPUB_NS}}}') or attr_lower.startswith('epub:'):
                continue

            if attr == f'{{{XML_NS}}}lang' or attr_lower == 'xml:lang':
                continue

            # Check if it's a kept attribute
            if attr_local.lower() in KEEP_ATTRS:
                continue

            # Check for namespace-prefixed essential attrs (xlink:href etc)
            if attr_local in ('href', 'src', 'type', 'lang'):
                continue

            if attr_local.lower() == 'role' and tag_local == 'nav':
                continue

            # Strip known-useless prefixes
            if any(attr_local.lower().startswith(p) for p in STRIP_ATTR_PREFIXES):
                attrs_to_remove.append(attr)
                continue

            # Strip other non-essential attributes
            if attr_local.lower() in ('role', 'tabindex', 'accesskey', 'draggable',
                                       'contenteditable', 'spellcheck', 'autocorrect',
                                       'autocapitalize', 'autofocus',
                                       'translate', 'inputmode', 'enterkeyhint',
                                       'hidden', 'inert', 'popover'):
                attrs_to_remove.append(attr)

        for attr in attrs_to_remove:
            del el.attrib[attr]
            removed += 1

    _sync_lang_attributes(tree.getroot())
    return _serialize_xhtml_document(tree, doctype), removed


def add_chapter_page_breaks(xhtml_bytes: bytes) -> bytes:
    """
    Add CSS page-break-before to chapter headings (h1, h2) if not already present.
    This ensures proper chapter separation on e-readers.
    """
    try:
        tree, doctype = _parse_xhtml_document(xhtml_bytes)
    except Exception:
        return xhtml_bytes

    # Find <head> to inject CSS if needed
    root = tree.getroot()
    head = root.find('.//{http://www.w3.org/1999/xhtml}head')
    if head is None:
        head = root.find('.//head')
    if head is None:
        return xhtml_bytes

    # Check if page-break CSS already exists
    existing_styles = head.findall('.//{http://www.w3.org/1999/xhtml}style')
    if not existing_styles:
        existing_styles = head.findall('.//style')

    has_page_break = False
    for style in existing_styles:
        if style.text and 'page-break-before' in style.text:
            has_page_break = True
            break

    if not has_page_break:
        # Add page-break style
        ns = root.tag.split('}')[0] + '}' if '}' in root.tag else ''
        style_el = etree.SubElement(head, f'{ns}style', type='text/css')
        style_el.text = '\nh1, h2 { page-break-before: always; }\n'

    return _serialize_xhtml_document(tree, doctype)


def _parse_xhtml_document(xhtml_bytes: bytes, allow_html_recovery: bool = False) -> tuple[etree._ElementTree | None, str]:
    """Parse XHTML bytes, optionally recovering malformed HTML into XHTML."""
    try:
        parser = etree.XMLParser(recover=False, remove_blank_text=False)
        tree = etree.parse(BytesIO(xhtml_bytes), parser)
        return _ensure_xhtml_tree(tree), tree.docinfo.doctype or DEFAULT_DOCTYPE
    except etree.XMLSyntaxError:
        if not allow_html_recovery:
            raise

    parser = etree.HTMLParser(recover=True, encoding='utf-8', remove_blank_text=False)
    tree = etree.parse(BytesIO(xhtml_bytes), parser)
    root = tree.getroot()
    if root is None:
        return None, DEFAULT_DOCTYPE
    return _ensure_xhtml_tree(etree.ElementTree(_coerce_tree_to_xhtml(root))), DEFAULT_DOCTYPE


def _serialize_xhtml_document(tree: etree._ElementTree, doctype: str | None) -> bytes:
    """Serialize XHTML with XML declaration, doctype, and XHTML namespace intact."""
    tree = _ensure_xhtml_tree(tree)
    return etree.tostring(
        tree,
        encoding='utf-8',
        pretty_print=True,
        method='xml',
        xml_declaration=True,
        doctype=doctype or DEFAULT_DOCTYPE,
    )


def _ensure_xhtml_tree(tree: etree._ElementTree) -> etree._ElementTree:
    """Ensure the document root is XHTML and lang/xml:lang stay synchronized."""
    root = tree.getroot()
    if root is None:
        return tree

    if _namespace_uri(root.tag) != XHTML_NS or _local_name(root.tag).lower() != 'html':
        root = _coerce_tree_to_xhtml(root)
        tree = etree.ElementTree(root)

    _sync_lang_attributes(root)
    return tree


def _coerce_tree_to_xhtml(node, root_nsmap: dict | None = None):
    """Recursively move a recovered HTML tree into the XHTML namespace."""
    if not isinstance(node.tag, str):
        return copy.deepcopy(node)

    is_root = node.getparent() is None
    if is_root:
        root_nsmap = _collect_root_nsmap(node)

    new_node = etree.Element(
        f'{{{XHTML_NS}}}{_local_name(node.tag)}',
        nsmap=root_nsmap if is_root else None,
    )
    for attr, value in node.attrib.items():
        attr_lower = attr.lower()
        if attr_lower == 'xmlns' or attr_lower.startswith('xmlns:'):
            continue
        if attr_lower == 'xml:lang':
            new_node.set(f'{{{XML_NS}}}lang', value)
        elif ':' in attr and root_nsmap:
            prefix, local = attr.split(':', 1)
            uri = root_nsmap.get(prefix)
            if uri:
                new_node.set(f'{{{uri}}}{local}', value)
            else:
                new_node.set(attr, value)
        else:
            new_node.set(attr, value)

    new_node.text = node.text
    new_node.tail = node.tail
    for child in node:
        new_node.append(_coerce_tree_to_xhtml(child, root_nsmap))
    return new_node


def _sync_lang_attributes(root) -> None:
    """Preserve both lang and xml:lang consistently when either is present."""
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        lang = el.get('lang')
        xml_lang = el.get(f'{{{XML_NS}}}lang')
        if lang and not xml_lang:
            el.set(f'{{{XML_NS}}}lang', lang)
        elif xml_lang and not lang:
            el.set('lang', xml_lang)


def _local_name(name: str) -> str:
    if name.startswith('{'):
        return name.split('}', 1)[1]
    return name


def _namespace_uri(name: str) -> str | None:
    if name.startswith('{'):
        return name[1:].split('}', 1)[0]
    return None


def _collect_root_nsmap(node) -> dict:
    nsmap = {k: v for k, v in (node.nsmap or {}).items() if k is not None}
    for attr, value in node.attrib.items():
        if attr.lower().startswith('xmlns:'):
            nsmap[attr.split(':', 1)[1]] = value
    nsmap[None] = XHTML_NS
    return nsmap
