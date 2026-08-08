"""
EPUB structure handler for Xteink X4 EPUB Optimizer.
Handles: OPF/NCX/XHTML reference updates, SVG cover fix, TOC repair/regeneration.
"""

import os
import re
from pathlib import Path, PurePosixPath
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
        normalized_old_path = old_path.replace('\\', '/')
        old_dir = PurePosixPath(normalized_old_path).parent
        new_path = str(old_dir / new_filename) if str(old_dir) != '.' else new_filename
        if normalized_old_path != new_path:
            rename_map[normalized_old_path] = new_path
    return rename_map


def build_split_image_map(processed_images: dict[str, list[str]]) -> dict[str, list[str]]:
    """Build EPUB-relative output paths for images converted into multiple parts."""
    split_map = {}
    for old_path, new_filenames in processed_images.items():
        normalized_old_path = old_path.replace('\\', '/')
        old_dir = PurePosixPath(normalized_old_path).parent
        split_map[normalized_old_path] = [
            str(old_dir / filename) if str(old_dir) != '.' else filename
            for filename in new_filenames
        ]
    return split_map


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


def add_split_images_to_opf(opf_path: str, split_images: dict[str, list[str]]) -> int:
    """Add manifest items for split image parts after each part-one rename."""
    if not split_images:
        return 0

    tree = etree.parse(opf_path)
    root = tree.getroot()
    manifest = _find_element(root, 'manifest')
    if manifest is None:
        return 0

    items = [item for item in manifest if _is_element(item)]
    used_ids = {item.get('id', '') for item in items}
    used_hrefs = {unquote(item.get('href', '')) for item in items}
    added = 0

    for new_paths in split_images.values():
        if len(new_paths) < 2:
            continue
        first_item = next(
            (item for item in items if unquote(item.get('href', '')) == new_paths[0]),
            None,
        )
        base_id = first_item.get('id', 'split-image') if first_item is not None else 'split-image'
        item_tag = first_item.tag if first_item is not None else f'{{{NS_OPF}}}item'

        for part_number, href in enumerate(new_paths[1:], start=2):
            if href in used_hrefs:
                continue
            item_id = f'{base_id}-part-{part_number}'
            suffix = 2
            while item_id in used_ids:
                item_id = f'{base_id}-part-{part_number}-{suffix}'
                suffix += 1

            item = etree.SubElement(manifest, item_tag)
            item.set('id', item_id)
            item.set('href', quote(href, safe='/:@'))
            item.set('media-type', 'image/jpeg')
            used_ids.add(item_id)
            used_hrefs.add(href)
            items.append(item)
            added += 1

    if added:
        tree.write(opf_path, xml_declaration=True, encoding='utf-8', pretty_print=True)
    return added


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


def ensure_cover_meta(opf_path: str) -> bool:
    """Ensure EPUB 2 cover metadata points to the best manifested cover image."""
    tree = etree.parse(opf_path)
    root = tree.getroot()
    metadata = _find_element(root, 'metadata')
    manifest = _find_element(root, 'manifest')
    if metadata is None or manifest is None:
        return False

    image_items = [
        item for item in manifest
        if _is_element(item) and (item.get('media-type') or '').startswith('image/')
    ]
    cover_item = next(
        (item for item in image_items
         if 'cover-image' in (item.get('properties') or '').split()),
        None,
    )
    if cover_item is None:
        cover_item = next(
            (item for item in image_items
             if 'cover' in (item.get('id') or '').lower()
             or 'cover' in unquote(item.get('href') or '').lower()),
            None,
        )
    if cover_item is None or not cover_item.get('id'):
        return False

    cover_id = cover_item.get('id')
    cover_meta = next(
        (element for element in metadata
         if _is_element(element)
         and etree.QName(element).localname == 'meta'
         and element.get('name') == 'cover'),
        None,
    )
    if cover_meta is not None:
        if cover_meta.get('content') == cover_id:
            return False
        cover_meta.set('content', cover_id)
    else:
        namespace = etree.QName(metadata).namespace or NS_OPF
        cover_meta = etree.SubElement(metadata, f'{{{namespace}}}meta')
        cover_meta.set('name', 'cover')
        cover_meta.set('content', cover_id)

    tree.write(opf_path, xml_declaration=True, encoding='utf-8', pretty_print=True)
    return True


def update_xhtml_references(xhtml_path: str, rename_map: dict) -> int:
    """
    Update image references in an XHTML file using targeted regex replacement,
    preserving the original document byte-for-byte otherwise.
    Returns count of updated references.
    """
    with open(xhtml_path, 'rb') as f:
        raw = f.read()
    text = raw.decode('utf-8', 'replace')
    original = text
    updated = 0

    for old_path, new_path in rename_map.items():
        old_name = Path(old_path).name
        new_name = Path(new_path).name
        if old_name == new_name:
            continue

        # Replace in src="...", href="..." and xlink:href="..." attributes
        def replacer(m, old=old_name, new=new_name):
            attr, val = m.group(1), m.group(2)
            decoded = unquote(val)
            if Path(decoded).name == old:
                new_val = decoded.replace(old, new)
                return f'{attr}="{new_val}"'
            return m.group(0)

        new_text = re.sub(r'((?:xlink:)?(?:src|href))="([^"]+)"', replacer, text)
        if new_text != text:
            text = new_text
            updated += 1

    # Update inline style background-image references
    def style_replacer(m):
        style = m.group(1)
        if 'url(' not in style:
            return m.group(0)
        new_style = _update_css_urls(style, rename_map)
        return f'style="{new_style}"'

    new_text = re.sub(r'style="([^"]*)"', style_replacer, text)
    if new_text != text:
        text = new_text
        updated += 1

    if text != original:
        with open(xhtml_path, 'wb') as f:
            f.write(text.encode('utf-8'))

    return updated


def update_xhtml_split_references(xhtml_path: str, opf_path: str,
                                  split_images: dict[str, list[str]]) -> int:
    """Replace one XHTML image with all of its generated split parts."""
    if not split_images:
        return 0

    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True)
        tree = etree.parse(xhtml_path, parser)
    except (OSError, etree.XMLSyntaxError):
        return 0

    root = tree.getroot()
    xhtml_dir = Path(xhtml_path).parent.resolve()
    opf_dir = Path(opf_path).parent.resolve()
    split_lookup = {
        (opf_dir.joinpath(*PurePosixPath(old_path).parts)).resolve(): new_paths
        for old_path, new_paths in split_images.items()
    }
    namespace = etree.QName(root).namespace or NS_XHTML
    safe_containers = {'div', 'p', 'figure', 'aside', 'section'}
    updated = 0

    for image in list(root.iter()):
        if not _is_element(image) or etree.QName(image).localname.lower() != 'img':
            continue
        src = image.get('src', '')
        if not src or src.startswith(('data:', 'http:', 'https:')):
            continue
        decoded_src = unquote(src.split('#', 1)[0].split('?', 1)[0])
        if not decoded_src or decoded_src.startswith('/'):
            continue
        source_path = xhtml_dir.joinpath(*PurePosixPath(decoded_src).parts).resolve()
        new_paths = split_lookup.get(source_path)
        if not new_paths:
            continue

        new_sources = []
        for new_path in new_paths:
            absolute_path = opf_dir.joinpath(*PurePosixPath(new_path).parts).resolve()
            relative_path = os.path.relpath(absolute_path, xhtml_dir).replace(os.sep, '/')
            new_sources.append(quote(relative_path, safe='/:@'))

        image.set('src', new_sources[0])
        for attr in ('width', 'height', 'class'):
            image.attrib.pop(attr, None)
        image.set('style', 'max-width:100%;height:auto')

        insert_target = image
        container = image.getparent()
        while container is not None:
            if _is_element(container) and etree.QName(container).localname.lower() in safe_containers:
                insert_target = container
                container.attrib.pop('class', None)
                container.attrib.pop('style', None)
                break
            if _is_element(container) and etree.QName(container).localname.lower() == 'body':
                break
            container = container.getparent()

        insert_parent = insert_target.getparent()
        if insert_parent is not None:
            insert_at = insert_parent.index(insert_target) + 1
            for part_src in new_sources[1:]:
                wrapper = etree.Element(f'{{{namespace}}}div')
                new_image = etree.SubElement(wrapper, f'{{{namespace}}}img')
                new_image.set('src', part_src)
                new_image.set('alt', '')
                new_image.set('style', 'max-width:100%;height:auto')
                insert_parent.insert(insert_at, wrapper)
                insert_at += 1
        updated += 1

    if updated:
        tree.write(
            xhtml_path,
            xml_declaration=True,
            encoding='utf-8',
            doctype=tree.docinfo.doctype or None,
        )
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
    Replace SVG-wrapped images in manifested XHTML with simple <img> tags.
    Returns count of fixed covers.
    """
    tree = etree.parse(opf_path)
    root = tree.getroot()
    fixed = 0

    manifest = _find_element(root, 'manifest')
    if manifest is None:
        return 0

    opf_dir = str(Path(opf_path).parent)
    svg_block_re = re.compile(r'<(?:\w+:)?svg\b[^>]*>.*?</(?:\w+:)?svg>', re.S | re.I)
    image_tag_re = re.compile(r'<(?:\w+:)?image\b[^>]*/?>', re.I)
    href_attr_re = re.compile(r'''\b(?:xlink:)?href=["']([^"']*)["']''', re.I)
    opf_changed = False

    for item in manifest:
        if not _is_element(item):
            continue
        if (item.get('media-type') or '').lower() not in ('application/xhtml+xml', 'text/html'):
            continue
        href = item.get('href', '')
        if not href:
            continue

        xhtml_path = os.path.join(opf_dir, unquote(href))
        if not os.path.exists(xhtml_path):
            continue

        with open(xhtml_path, 'rb') as f:
            text = f.read().decode('utf-8', 'replace')
        original = text
        doc_fixed = 0

        def unwrap(m):
            nonlocal doc_fixed
            block = m.group(0)
            images = image_tag_re.findall(block)
            if len(images) != 1:
                return block
            href_match = href_attr_re.search(images[0])
            if not href_match or not href_match.group(1):
                return block
            doc_fixed += 1
            return ('<div><img src="%s" alt="" '
                    'style="max-width:100%%;height:auto"/></div>'
                    % href_match.group(1))

        text = svg_block_re.sub(unwrap, text)

        if doc_fixed > 0 and text != original:
            with open(xhtml_path, 'wb') as f:
                f.write(text.encode('utf-8'))
            fixed += doc_fixed
            properties = (item.get('properties') or '').split()
            if 'svg' in properties:
                properties = [prop for prop in properties if prop != 'svg']
                if properties:
                    item.set('properties', ' '.join(properties))
                else:
                    item.attrib.pop('properties', None)
                opf_changed = True

    if opf_changed:
        tree.write(opf_path, xml_declaration=True, encoding='utf-8', pretty_print=True)

    return fixed


def fix_toc(epub_dir: str, opf_path: str) -> tuple[bool, str]:
    """
    Check and repair the Table of Contents.

    Matches the official CrossPoint plugin behavior: if an NCX exists on disk,
    only sync its dtb:uid to the OPF identifier (never rebuild the navMap, so
    existing chapter metadata is preserved). A basic NCX is generated only when
    none exists. The EPUB 3 nav document is never touched.

    Returns (was_fixed, description).
    """
    tree = etree.parse(opf_path)
    root = tree.getroot()
    opf_dir = str(Path(opf_path).parent)

    manifest = _find_element(root, 'manifest')
    spine = _find_element(root, 'spine')
    if manifest is None or spine is None:
        return False, "No manifest or spine found"

    metadata = _opf_book_metadata(root)

    # Find NCX file
    ncx_href = None
    ncx_id = None
    for item in manifest:
        if not _is_element(item):
            continue
        if item.get('media-type', '') == 'application/x-dtbncx+xml':
            ncx_href = item.get('href', '')
            ncx_id = item.get('id', '')

    if ncx_href:
        ncx_path = os.path.join(opf_dir, unquote(ncx_href))
    else:
        ncx_path = os.path.join(opf_dir, 'toc.ncx')
        ncx_href = 'toc.ncx'
        ncx_id = 'ncx'

    changed = False
    messages = []

    if os.path.exists(ncx_path):
        # NCX exists: only sync dtb:uid (preserve navMap/chapter metadata)
        if _sync_ncx_uid(ncx_path, metadata['uid']):
            changed = True
            messages.append("synced NCX dtb:uid")
    else:
        # No NCX on disk: generate a basic one from the spine
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

    if changed:
        tree.write(opf_path, xml_declaration=True, encoding='utf-8', pretty_print=False)
        return True, "; ".join(messages) if messages else "Updated TOC metadata"

    return False, "TOC is valid"


def _sync_ncx_uid(ncx_path: str, uid: str) -> bool:
    """Sync the NCX dtb:uid meta content to the OPF identifier via targeted regex.

    Returns True if the file was modified.
    """
    target = uid or 'unknown'
    with open(ncx_path, 'rb') as f:
        text = f.read().decode('utf-8', 'replace')

    new_text = re.sub(
        r'(<meta\b[^>]*\bname="dtb:uid"[^>]*\bcontent=")[^"]*(")',
        lambda m: m.group(1) + target + m.group(2),
        text,
    )
    if new_text == text:
        new_text = re.sub(
            r'(<meta\b[^>]*\bcontent=")[^"]*("[^>]*\bname="dtb:uid")',
            lambda m: m.group(1) + target + m.group(2),
            text,
        )
    if new_text == text:
        return False
    with open(ncx_path, 'wb') as f:
        f.write(new_text.encode('utf-8'))
    return True


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
