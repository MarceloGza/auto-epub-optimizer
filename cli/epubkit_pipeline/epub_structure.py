"""
EPUB structure handler for Xteink X4 EPUB Optimizer.
Handles: OPF/NCX/XHTML reference updates, SVG cover fix, TOC repair/regeneration.
"""

import os
import re
from pathlib import Path
from urllib.parse import unquote, quote

from lxml import etree

NAMESPACES = {
    'opf': 'http://www.idpf.org/2007/opf',
    'dc': 'http://purl.org/dc/elements/1.1/',
    'ncx': 'http://www.daisy.org/z3986/2005/ncx/',
    'xhtml': 'http://www.w3.org/1999/xhtml',
    'epub': 'http://www.idpf.org/2007/ops',
    'svg': 'http://www.w3.org/2000/svg',
    'xlink': 'http://www.w3.org/1999/xlink',
    'container': 'urn:oasis:names:tc:opendocument:xmlns:container',
}

NS_OPF = 'http://www.idpf.org/2007/opf'
NS_XHTML = 'http://www.w3.org/1999/xhtml'
NS_SVG = 'http://www.w3.org/2000/svg'
NS_XLINK = 'http://www.w3.org/1999/xlink'
NS_NCX = 'http://www.daisy.org/z3986/2005/ncx/'
NS_EPUB = 'http://www.idpf.org/2007/ops'
MEDIA_TYPE_BY_EXTENSION = {
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.gif': 'image/gif',
    '.svg': 'image/svg+xml',
    '.webp': 'image/webp',
    '.bmp': 'image/bmp',
    '.avif': 'image/avif',
}


def _is_element(node):
    """Check if a node is a real element (not a comment or PI)."""
    return isinstance(node.tag, str)


def _find_element(root, local_name):
    """Find an element by local name, trying namespaced then unnamespaced."""
    # Try with OPF namespace first
    el = root.find(f'.//{{{NS_OPF}}}{local_name}')
    if el is not None:
        return el
    # Try without namespace (some EPUBs omit it)
    el = root.find(f'.//{local_name}')
    if el is not None:
        return el
    # Try wildcard namespace match
    for child in root.iter():
        tag = child.tag if isinstance(child.tag, str) else ''
        if tag.endswith('}' + local_name) or tag == local_name:
            return child
    return None


def build_rename_map(epub_dir: str, processed_images: dict) -> dict:
    """
    Build a mapping of old image paths to new paths.
    processed_images: {old_relative_path: new_filename}
    Returns: {old_path: new_path} with paths relative to EPUB root.
    """
    rename_map = {}
    for old_path, new_filename in processed_images.items():
        old_dir = str(Path(old_path).parent)
        new_path = str(Path(old_dir) / new_filename) if old_dir != '.' else new_filename
        if old_path != new_path:
            rename_map[old_path] = new_path
    return rename_map


def update_opf(opf_path: str, rename_map: dict) -> None:
    """Update manifest entries in OPF when images are renamed."""
    tree = etree.parse(opf_path)
    root = tree.getroot()

    manifest = _find_element(root, 'manifest')
    if manifest is None:
        return

    for item in manifest:
        if not _is_element(item):
            continue
        href = item.get('href', '')
        decoded_href = unquote(href)

        for old_path, new_path in rename_map.items():
            if decoded_href == old_path or href == old_path:
                item.set('href', quote(new_path, safe='/:@'))
                media_type = _guess_media_type(new_path)
                if media_type:
                    item.set('media-type', media_type)
                break

    tree.write(opf_path, xml_declaration=True, encoding='utf-8', pretty_print=True)


def update_opf_remove_fonts(opf_path: str, font_files: list[str]) -> int:
    """Remove font file entries from OPF manifest. Returns count removed."""
    tree = etree.parse(opf_path)
    root = tree.getroot()

    manifest = _find_element(root, 'manifest')
    if manifest is None:
        return 0

    removed = 0
    font_basenames = {Path(f).name for f in font_files}

    to_remove = []
    for item in manifest:
        if not _is_element(item):
            continue
        href = unquote(item.get('href', ''))
        if Path(href).name in font_basenames:
            to_remove.append(item)

    for item in to_remove:
        manifest.remove(item)
        removed += 1

    if removed > 0:
        tree.write(opf_path, xml_declaration=True, encoding='utf-8', pretty_print=True)

    return removed


def add_image_to_opf(opf_path: str, image_href: str, image_id: str) -> None:
    """Add a new image entry to the OPF manifest."""
    tree = etree.parse(opf_path)
    root = tree.getroot()

    manifest = _find_element(root, 'manifest')
    if manifest is None:
        return

    item = etree.SubElement(manifest, f'{{{NS_OPF}}}item')
    item.set('id', image_id)
    item.set('href', image_href)
    item.set('media-type', _guess_media_type(image_href) or 'image/jpeg')

    tree.write(opf_path, xml_declaration=True, encoding='utf-8', pretty_print=True)


def update_xhtml_references(xhtml_path: str, rename_map: dict) -> int:
    """
    Update image references in an XHTML file.
    Returns count of updated references.
    """
    try:
        tree = etree.parse(xhtml_path)
    except etree.XMLSyntaxError:
        parser = etree.HTMLParser(recover=True)
        tree = etree.parse(xhtml_path, parser)

    root = tree.getroot()
    updated = 0

    # Update <img src="...">
    for img in root.iter():
        if not _is_element(img):
            continue
        tag = img.tag.split('}')[-1] if '}' in str(img.tag) else str(img.tag)

        if tag == 'img':
            src = img.get('src', '')
            new_src = _resolve_reference(src, rename_map)
            if new_src != src:
                img.set('src', new_src)
                updated += 1

        elif tag == 'image':
            # SVG <image xlink:href="...">
            href = img.get(f'{{{NS_XLINK}}}href', '') or img.get('href', '')
            new_href = _resolve_reference(href, rename_map)
            if new_href != href:
                if img.get(f'{{{NS_XLINK}}}href') is not None:
                    img.set(f'{{{NS_XLINK}}}href', new_href)
                else:
                    img.set('href', new_href)
                updated += 1

    # Update inline style background-image references
    for el in root.iter():
        if not _is_element(el):
            continue
        style = el.get('style') or ''
        if 'url(' in style:
            new_style = _update_css_urls(style, rename_map)
            if new_style != style:
                el.set('style', new_style)
                updated += 1

    if updated > 0:
        tree.write(xhtml_path, xml_declaration=True, encoding='utf-8', pretty_print=True)

    return updated


def update_css_references(css_path: str, rename_map: dict) -> int:
    """Update url() references in a CSS file. Returns count of updates."""
    with open(css_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    new_content = _update_css_urls(content, rename_map)
    updated = 1 if new_content != content else 0

    if updated:
        with open(css_path, 'w', encoding='utf-8') as f:
            f.write(new_content)

    return updated


def _update_css_urls(css_text: str, rename_map: dict) -> str:
    """Replace url() references in CSS text."""
    def replacer(match):
        url = match.group(1).strip("'\"")
        decoded = unquote(url)
        for old, new in rename_map.items():
            old_name = Path(old).name
            new_name = Path(new).name
            decoded_name = Path(decoded).name
            if decoded_name == old_name:
                return f'url({decoded.replace(old_name, new_name)})'
        return match.group(0)

    return re.sub(r"url\(([^)]+)\)", replacer, css_text)


def _resolve_reference(ref: str, rename_map: dict) -> str:
    """Try to match a reference against the rename map."""
    decoded = unquote(ref)
    ref_name = Path(decoded).name

    for old_path, new_path in rename_map.items():
        old_name = Path(old_path).name
        if ref_name == old_name:
            return decoded.replace(old_name, Path(new_path).name)

    return ref


def fix_svg_covers(epub_dir: str, opf_path: str) -> int:
    """
    Find XHTML files that wrap cover images in SVG and replace with simple <img> tags.
    Returns count of fixed covers.
    """
    tree = etree.parse(opf_path)
    root = tree.getroot()
    fixed = 0

    # Find spine items
    spine = _find_element(root, 'spine')
    manifest = _find_element(root, 'manifest')
    if spine is None or manifest is None:
        return 0

    opf_dir = str(Path(opf_path).parent)

    # Build id->href map from manifest
    id_to_href = {}
    for item in manifest:
        if not _is_element(item):
            continue
        id_to_href[item.get('id', '')] = item.get('href', '')

    # Check first few spine items for SVG cover wrappers
    spine_items = [s for s in spine if _is_element(s)]
    for itemref in spine_items[:3]:
        idref = itemref.get('idref', '')
        href = id_to_href.get(idref, '')
        if not href:
            continue

        xhtml_path = os.path.join(opf_dir, unquote(href))
        if not os.path.exists(xhtml_path):
            continue

        try:
            doc_tree = etree.parse(xhtml_path)
        except Exception:
            continue

        doc_root = doc_tree.getroot()

        # Look for SVG elements containing a single <image>
        svgs = doc_root.findall(f'.//{{{NS_SVG}}}svg')
        if not svgs:
            svgs = doc_root.findall('.//svg')

        for svg in svgs:
            images = svg.findall(f'{{{NS_SVG}}}image')
            if not images:
                images = svg.findall('image')

            if len(images) == 1:
                image = images[0]
                img_href = (image.get(f'{{{NS_XLINK}}}href', '') or
                           image.get('href', ''))

                if not img_href:
                    continue

                # Replace SVG with simple <img>
                parent = svg.getparent()
                if parent is None:
                    continue

                # Determine namespace
                ns_prefix = ''
                if '}' in str(parent.tag):
                    ns_prefix = parent.tag.split('}')[0] + '}'

                img_el = etree.Element(f'{ns_prefix}img' if ns_prefix else 'img')
                img_el.set('src', img_href)
                img_el.set('alt', 'Cover')
                img_el.set('style', 'max-width:100%;max-height:100%;display:block;margin:auto')

                # Replace SVG with img
                idx = list(parent).index(svg)
                parent.remove(svg)
                parent.insert(idx, img_el)
                fixed += 1

        if fixed > 0:
            doc_tree.write(xhtml_path, xml_declaration=True, encoding='utf-8', pretty_print=True)

    return fixed


def fix_toc(epub_dir: str, opf_path: str) -> tuple[bool, str]:
    """
    Check and repair/regenerate the Table of Contents.
    Returns (was_fixed, description).
    """
    tree = etree.parse(opf_path)
    root = tree.getroot()
    opf_dir = str(Path(opf_path).parent)

    # Check EPUB version
    version = root.get('version', '2.0')
    is_epub3 = version.startswith('3')

    # Check for existing NCX
    manifest = _find_element(root, 'manifest')
    spine = _find_element(root, 'spine')
    if manifest is None or spine is None:
        return False, "No manifest or spine found"

    metadata = _opf_book_metadata(root)

    # Find NCX file
    ncx_href = None
    ncx_id = None
    nav_href = None
    nav_id = None
    for item in manifest:
        if not _is_element(item):
            continue
        media_type = item.get('media-type', '')
        if media_type == 'application/x-dtbncx+xml':
            ncx_href = item.get('href', '')
            ncx_id = item.get('id', '')
        properties = (item.get('properties') or '').split()
        if 'nav' in properties:
            nav_href = item.get('href', '')
            nav_id = item.get('id', '')

    if is_epub3 and nav_href is None:
        discovered_nav = _discover_nav_document(manifest, opf_dir)
        if discovered_nav is not None:
            nav_id = discovered_nav.get('id', '')
            nav_href = discovered_nav.get('href', '')

    # Build spine reading order
    id_to_href = {}
    for item in manifest:
        if not _is_element(item):
            continue
        id_to_href[item.get('id', '')] = item.get('href', '')

    spine_hrefs = []
    for itemref in spine:
        if not _is_element(itemref):
            continue
        idref = itemref.get('idref', '')
        href = id_to_href.get(idref, '')
        if href:
            spine_hrefs.append((idref, href))

    if not spine_hrefs:
        return False, "Empty spine"

    chapters = _extract_chapter_info(opf_dir, spine_hrefs)
    changed = False
    messages = []

    if ncx_href:
        ncx_path = os.path.join(opf_dir, unquote(ncx_href))
    else:
        ncx_path = os.path.join(opf_dir, 'toc.ncx')
        ncx_href = 'toc.ncx'
        ncx_id = 'ncx'

    if not _ncx_matches_spine(ncx_path, chapters, metadata['uid']):
        _generate_ncx(ncx_path, chapters, metadata['uid'], metadata['title'], metadata['author'])
        changed = True
        messages.append(f"generated NCX with {len(chapters)} entries")

    # Ensure NCX is in manifest
    if _manifest_item_by_id(manifest, ncx_id) is None:
        item = etree.SubElement(manifest, f'{{{NS_OPF}}}item')
        item.set('id', ncx_id)
        item.set('href', ncx_href)
        item.set('media-type', 'application/x-dtbncx+xml')
        changed = True
    else:
        item = _manifest_item_by_id(manifest, ncx_id)
        if item is not None:
            if item.get('href') != ncx_href:
                item.set('href', ncx_href)
                changed = True
            if item.get('media-type') != 'application/x-dtbncx+xml':
                item.set('media-type', 'application/x-dtbncx+xml')
                changed = True

    if spine.get('toc') != ncx_id:
        spine.set('toc', ncx_id)
        changed = True

    if is_epub3:
        if nav_href:
            nav_path = os.path.join(opf_dir, unquote(nav_href))
        else:
            nav_href = 'nav.xhtml'
            nav_id = nav_id or 'nav'
            nav_path = os.path.join(opf_dir, nav_href)

        if not _nav_document_matches_spine(nav_path, chapters):
            _generate_nav_document(nav_path, chapters, metadata['title'], metadata['author'])
            changed = True
            messages.append(f"updated nav document with {len(chapters)} entries")

        nav_item = _find_nav_manifest_item(manifest)
        if nav_item is None:
            nav_item = etree.SubElement(manifest, f'{{{NS_OPF}}}item')
            nav_item.set('id', nav_id or 'nav')
            nav_item.set('href', nav_href)
            nav_item.set('media-type', 'application/xhtml+xml')
            nav_item.set('properties', 'nav')
            changed = True
        else:
            if nav_item.get('href') != nav_href:
                nav_item.set('href', nav_href)
                changed = True
            if nav_item.get('media-type') != 'application/xhtml+xml':
                nav_item.set('media-type', 'application/xhtml+xml')
                changed = True
            properties = set((nav_item.get('properties') or '').split())
            if 'nav' not in properties:
                properties.add('nav')
                nav_item.set('properties', ' '.join(sorted(properties)))
                changed = True

    if changed:
        tree.write(opf_path, xml_declaration=True, encoding='utf-8', pretty_print=True)
        return True, "; ".join(messages) if messages else "Updated TOC metadata"

    return False, "TOC is valid"


def _check_ncx_references(nav_points, opf_dir: str, ncx_path: str) -> list:
    """Check if NCX navPoint references point to existing files."""
    broken = []
    ncx_dir = str(Path(ncx_path).parent)

    for np in nav_points:
        content = np.find(f'{{{NS_NCX}}}content')
        if content is not None:
            src = content.get('src', '')
            src_path = src.split('#')[0]  # Remove fragment
            full_path = os.path.join(ncx_dir, unquote(src_path))
            if src_path and not os.path.exists(full_path):
                broken.append(np)

    return broken


def _fix_ncx_references(nav_points, opf_dir: str, ncx_path: str, spine_hrefs: list) -> None:
    """Attempt to fix broken NCX references by matching to spine items."""
    pass  # Complex matching logic - for now regeneration handles this


def _extract_chapter_info(opf_dir: str, spine_hrefs: list) -> list[dict]:
    """Extract chapter titles from spine XHTML files."""
    chapters = []

    for i, (idref, href) in enumerate(spine_hrefs):
        xhtml_path = os.path.join(opf_dir, unquote(href))
        title = f"Chapter {i + 1}"

        if os.path.exists(xhtml_path):
            try:
                tree = etree.parse(xhtml_path)
                root = tree.getroot()

                # Try <title> tag
                title_el = root.find(f'.//{{{NS_XHTML}}}title')
                if title_el is None:
                    title_el = root.find('.//title')
                if title_el is not None and title_el.text and title_el.text.strip():
                    title = title_el.text.strip()
                else:
                    # Try first heading
                    for tag in ['h1', 'h2', 'h3']:
                        h = root.find(f'.//{{{NS_XHTML}}}{tag}')
                        if h is None:
                            h = root.find(f'.//{tag}')
                        if h is not None:
                            text = ''.join(h.itertext()).strip()
                            if text:
                                title = text
                                break
            except Exception:
                pass

        chapters.append({
            'href': href,
            'path': xhtml_path,
            'title': title,
            'id': idref,
        })

    return chapters


def _generate_ncx(ncx_path: str, chapters: list[dict], uid: str, title: str, author: str) -> None:
    """Generate an NCX file from chapter info."""
    ncx = etree.Element(f'{{{NS_NCX}}}ncx', nsmap={None: NS_NCX})
    ncx.set('version', '2005-1')

    head = etree.SubElement(ncx, f'{{{NS_NCX}}}head')
    for name, content_value in (
        ('dtb:uid', uid or 'unknown'),
        ('dtb:depth', '1'),
        ('dtb:totalPageCount', '0'),
        ('dtb:maxPageNumber', '0'),
    ):
        meta = etree.SubElement(head, f'{{{NS_NCX}}}meta')
        meta.set('name', name)
        meta.set('content', content_value)

    doc_title = etree.SubElement(ncx, f'{{{NS_NCX}}}docTitle')
    doc_text = etree.SubElement(doc_title, f'{{{NS_NCX}}}text')
    doc_text.text = title or (chapters[0]['title'] if chapters else 'Unknown')

    if author:
        doc_author = etree.SubElement(ncx, f'{{{NS_NCX}}}docAuthor')
        author_text = etree.SubElement(doc_author, f'{{{NS_NCX}}}text')
        author_text.text = author

    nav_map = etree.SubElement(ncx, f'{{{NS_NCX}}}navMap')
    ncx_dir = Path(ncx_path).parent

    for i, chapter in enumerate(chapters):
        nav_point = etree.SubElement(nav_map, f'{{{NS_NCX}}}navPoint')
        nav_point.set('id', f'navPoint-{i + 1}')
        nav_point.set('playOrder', str(i + 1))

        nav_label = etree.SubElement(nav_point, f'{{{NS_NCX}}}navLabel')
        text = etree.SubElement(nav_label, f'{{{NS_NCX}}}text')
        text.text = chapter['title']

        content = etree.SubElement(nav_point, f'{{{NS_NCX}}}content')
        rel_src = os.path.relpath(Path(chapter['path']), ncx_dir).replace(os.sep, '/')
        content.set('src', quote(rel_src, safe='/:@'))

    tree = etree.ElementTree(ncx)
    tree.write(ncx_path, xml_declaration=True, encoding='utf-8', pretty_print=True)


def _guess_media_type(href: str) -> str | None:
    return MEDIA_TYPE_BY_EXTENSION.get(Path(unquote(href)).suffix.lower())


def _manifest_item_by_id(manifest, item_id: str | None):
    if not item_id:
        return None
    for item in manifest:
        if _is_element(item) and item.get('id') == item_id:
            return item
    return None


def _find_nav_manifest_item(manifest):
    for item in manifest:
        if not _is_element(item):
            continue
        properties = set((item.get('properties') or '').split())
        if 'nav' in properties:
            return item
    return None


def _opf_book_metadata(root) -> dict[str, str]:
    identifier = root.find('.//dc:identifier', NAMESPACES)
    title = root.find('.//dc:title', NAMESPACES)
    creator = root.find('.//dc:creator', NAMESPACES)
    return {
        'uid': (identifier.text or '').strip() if identifier is not None and identifier.text else 'unknown',
        'title': (title.text or '').strip() if title is not None and title.text else '',
        'author': (creator.text or '').strip() if creator is not None and creator.text else '',
    }


def _ncx_matches_spine(ncx_path: str, chapters: list[dict], uid: str) -> bool:
    if not os.path.exists(ncx_path):
        return False
    try:
        tree = etree.parse(ncx_path)
    except Exception:
        return False

    head = tree.getroot().find(f'.//{{{NS_NCX}}}head')
    if head is None:
        return False

    meta_values = {
        meta.get('name'): meta.get('content')
        for meta in head.findall(f'{{{NS_NCX}}}meta')
        if meta.get('name')
    }
    if meta_values.get('dtb:uid') != (uid or 'unknown'):
        return False
    if meta_values.get('dtb:totalPageCount') != '0' or meta_values.get('dtb:maxPageNumber') != '0':
        return False

    nav_points = tree.getroot().findall(f'.//{{{NS_NCX}}}navPoint')
    if len(nav_points) != len(chapters):
        return False

    ncx_dir = Path(ncx_path).parent.resolve()
    for nav_point, chapter in zip(nav_points, chapters):
        content = nav_point.find(f'{{{NS_NCX}}}content')
        if content is None or not content.get('src'):
            return False
        src_path = (ncx_dir / unquote(content.get('src', '').split('#', 1)[0])).resolve()
        if src_path != Path(chapter['path']).resolve():
            return False
    return True


def _nav_document_matches_spine(nav_path: str, chapters: list[dict]) -> bool:
    if not os.path.exists(nav_path):
        return False
    try:
        tree = etree.parse(nav_path)
    except Exception:
        return False

    nav = tree.getroot().find('.//xhtml:nav[@epub:type="toc"]', NAMESPACES)
    if nav is None:
        for candidate in tree.getroot().findall('.//xhtml:nav', NAMESPACES):
            epub_type = candidate.get(f'{{{NS_EPUB}}}type') or candidate.get('epub:type')
            if epub_type and 'toc' in epub_type.split():
                nav = candidate
                break
    if nav is None:
        return False

    hrefs = [
        (Path(nav_path).parent / unquote(link.get('href', '').split('#', 1)[0])).resolve()
        for link in nav.findall('.//xhtml:a', NAMESPACES)
        if link.get('href')
    ]
    expected = [Path(chapter['path']).resolve() for chapter in chapters]
    return hrefs == expected


def _generate_nav_document(nav_path: str, chapters: list[dict], title: str, author: str) -> None:
    html = etree.Element(
        f'{{{NS_XHTML}}}html',
        nsmap={None: NS_XHTML, 'epub': NS_EPUB},
    )
    head = etree.SubElement(html, f'{{{NS_XHTML}}}head')
    title_el = etree.SubElement(head, f'{{{NS_XHTML}}}title')
    title_el.text = title or 'Table of Contents'

    body = etree.SubElement(html, f'{{{NS_XHTML}}}body')
    nav = etree.SubElement(body, f'{{{NS_XHTML}}}nav')
    nav.set(f'{{{NS_EPUB}}}type', 'toc')
    nav.set('role', 'doc-toc')
    heading = etree.SubElement(nav, f'{{{NS_XHTML}}}h1')
    heading.text = 'Table of Contents'
    ol = etree.SubElement(nav, f'{{{NS_XHTML}}}ol')
    nav_dir = Path(nav_path).parent
    for chapter in chapters:
        li = etree.SubElement(ol, f'{{{NS_XHTML}}}li')
        link = etree.SubElement(li, f'{{{NS_XHTML}}}a')
        rel_href = os.path.relpath(Path(chapter['path']), nav_dir).replace(os.sep, '/')
        link.set('href', quote(rel_href, safe='/:@'))
        link.text = chapter['title']

    etree.ElementTree(html).write(nav_path, xml_declaration=True, encoding='utf-8', pretty_print=True, doctype='<!DOCTYPE html>')


def _discover_nav_document(manifest, opf_dir: str):
    for item in manifest:
        if not _is_element(item):
            continue
        media_type = (item.get('media-type') or '').lower()
        href = item.get('href', '')
        if media_type != 'application/xhtml+xml' or not href:
            continue
        nav_path = os.path.join(opf_dir, unquote(href))
        if not os.path.exists(nav_path):
            continue
        try:
            tree = etree.parse(nav_path)
        except Exception:
            continue
        if _has_toc_nav(tree.getroot()):
            return item
    return None


def _has_toc_nav(root) -> bool:
    for candidate in root.findall('.//xhtml:nav', NAMESPACES):
        epub_type = candidate.get(f'{{{NS_EPUB}}}type') or candidate.get('epub:type')
        if epub_type and 'toc' in epub_type.split():
            return True
    return False


def find_content_files(epub_dir: str, opf_path: str) -> dict:
    """
    Find all content files referenced in the OPF manifest.
    Returns dict with keys: xhtml, css, images, fonts, ncx, other
    """
    tree = etree.parse(opf_path)
    root = tree.getroot()
    opf_dir = str(Path(opf_path).parent)

    files = {
        'xhtml': [],
        'css': [],
        'images': [],
        'fonts': [],
        'ncx': [],
        'other': [],
    }

    manifest = _find_element(root, 'manifest')
    if manifest is None:
        return files

    for item in manifest:
        if not _is_element(item):
            continue
        href = unquote(item.get('href', ''))
        media_type = item.get('media-type', '').lower()
        full_path = os.path.join(opf_dir, href)

        if media_type in ('application/xhtml+xml', 'text/html'):
            files['xhtml'].append(full_path)
        elif media_type == 'text/css':
            files['css'].append(full_path)
        elif media_type.startswith('image/'):
            files['images'].append(full_path)
        elif media_type == 'application/x-dtbncx+xml':
            files['ncx'].append(full_path)
        elif media_type in ('application/font-woff', 'application/font-woff2',
                           'font/woff', 'font/woff2', 'font/ttf', 'font/otf',
                           'application/vnd.ms-opentype', 'application/x-font-ttf'):
            files['fonts'].append(full_path)
        else:
            ext = Path(href).suffix.lower()
            if ext in ('.ttf', '.otf', '.woff', '.woff2'):
                files['fonts'].append(full_path)
            else:
                files['other'].append(full_path)

    return files
