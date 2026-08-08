"""
EPUB packager for Xteink X4 EPUB Optimizer.
Handles: EPUB extraction, repackaging with correct mimetype-first ZIP structure, OS artifact cleanup.
"""

import os
import zipfile
from pathlib import Path
from urllib.parse import unquote

from lxml import etree

# Files/dirs to exclude from packaged EPUB
OS_ARTIFACTS = {
    '.DS_Store', 'Thumbs.db', 'desktop.ini', '._.DS_Store',
}
OS_ARTIFACT_DIRS = {
    '__MACOSX', '.git', '.svn',
}
NS_CONTAINER = 'urn:oasis:names:tc:opendocument:xmlns:container'
NS_OPF = 'http://www.idpf.org/2007/opf'
NS_XHTML = 'http://www.w3.org/1999/xhtml'
NS_EPUB = 'http://www.idpf.org/2007/ops'
NS_XMLENC = 'http://www.w3.org/2001/04/xmlenc#'


def extract_epub(epub_path: str, dest_dir: str) -> None:
    """
    Extract an EPUB file to a directory.
    Validates ZIP structure and prevents zip-slip attacks.
    """
    dest = os.path.abspath(dest_dir)

    with zipfile.ZipFile(epub_path, 'r') as zf:
        for entry in zf.namelist():
            # Zip-slip prevention
            target = os.path.abspath(os.path.join(dest, entry))
            if not target.startswith(dest + os.sep) and target != dest:
                raise ValueError(f"Unsafe path in EPUB: {entry}")

        zf.extractall(dest)


def package_epub(source_dir: str, output_path: str) -> None:
    """
    Create a valid EPUB ZIP file from a directory.
    - mimetype is first entry, stored (uncompressed), no extra field
    - All other files are deflated
    - OS artifacts are excluded
    """
    source = Path(source_dir)
    mimetype_path = source / 'mimetype'

    with zipfile.ZipFile(output_path, 'w') as zf:
        # 1. Write mimetype first, uncompressed, no extra field
        info = zipfile.ZipInfo('mimetype')
        info.compress_type = zipfile.ZIP_STORED
        info.extra = b''
        if mimetype_path.exists():
            mimetype_content = mimetype_path.read_text().strip()
        else:
            mimetype_content = 'application/epub+zip'
        zf.writestr(info, mimetype_content)

        # 2. Write META-INF/container.xml next (convention)
        container_path = source / 'META-INF' / 'container.xml'
        if container_path.exists():
            arcname = 'META-INF/container.xml'
            zf.write(str(container_path), arcname, compress_type=zipfile.ZIP_DEFLATED)

        # 3. Write everything else
        for root, dirs, files in os.walk(source):
            # Filter out OS artifact directories
            dirs[:] = [d for d in dirs if d not in OS_ARTIFACT_DIRS]

            for filename in sorted(files):
                filepath = Path(root) / filename
                arcname = str(filepath.relative_to(source))

                # Skip mimetype (already written)
                if arcname == 'mimetype':
                    continue

                # Skip META-INF/container.xml (already written)
                if arcname == os.path.join('META-INF', 'container.xml'):
                    continue

                # Skip OS artifacts
                if filename in OS_ARTIFACTS:
                    continue

                zf.write(str(filepath), arcname, compress_type=zipfile.ZIP_DEFLATED)


def remove_os_artifacts(directory: str) -> int:
    """
    Remove OS artifacts from extracted EPUB directory.
    Returns count of removed files.
    """
    removed = 0
    dir_path = Path(directory)

    # Remove artifact files
    for artifact in OS_ARTIFACTS:
        for found in dir_path.rglob(artifact):
            found.unlink()
            removed += 1

    # Remove artifact directories
    for artifact_dir in OS_ARTIFACT_DIRS:
        for found in dir_path.rglob(artifact_dir):
            if found.is_dir():
                import shutil
                shutil.rmtree(found)
                removed += 1

    return removed


def is_valid_epub(epub_path: str) -> tuple[bool, str]:
    """
    Quick validation of an EPUB file.
    Returns (is_valid, error_message).
    """
    try:
        with zipfile.ZipFile(epub_path, 'r') as zf:
            names = zf.namelist()

            # Check mimetype is first entry
            if not names or names[0] != 'mimetype':
                return False, "mimetype is not the first entry in the ZIP"

            # Check mimetype content
            mimetype = zf.read('mimetype').decode('utf-8').strip()
            if mimetype != 'application/epub+zip':
                return False, f"Invalid mimetype: {mimetype}"

            # Check mimetype is stored (uncompressed)
            info = zf.getinfo('mimetype')
            if info.compress_type != zipfile.ZIP_STORED:
                return False, "mimetype entry is compressed (should be stored)"

            # Check container.xml exists
            if 'META-INF/container.xml' not in names:
                return False, "Missing META-INF/container.xml"

            archive_entries = set(names)
            container_tree = etree.fromstring(zf.read('META-INF/container.xml'))
            rootfile = container_tree.find(f'.//{{{NS_CONTAINER}}}rootfile')
            if rootfile is None:
                for el in container_tree.iter():
                    if isinstance(el.tag, str) and (el.tag.endswith('}rootfile') or el.tag == 'rootfile'):
                        rootfile = el
                        break
            if rootfile is None:
                return False, "container.xml does not reference an OPF rootfile"

            opf_path = rootfile.get('full-path')
            if not opf_path or opf_path not in archive_entries:
                return False, f"Referenced OPF is missing: {opf_path or '<empty>'}"

            opf_tree = etree.fromstring(zf.read(opf_path))
            opf_dir = Path(opf_path).parent
            manifest = opf_tree.find(f'.//{{{NS_OPF}}}manifest')
            if manifest is None:
                for el in opf_tree.iter():
                    if isinstance(el.tag, str) and (el.tag.endswith('}manifest') or el.tag == 'manifest'):
                        manifest = el
                        break
            if manifest is None:
                return False, "OPF manifest is missing"

            package_version = opf_tree.get('version', '2.0')
            nav_item_href = None
            for item in manifest:
                if not isinstance(item.tag, str):
                    continue
                href = item.get('href')
                if not href:
                    continue
                full_path = _archive_relpath(opf_dir, href)
                if full_path not in archive_entries:
                    return False, f"Manifest item missing from archive: {full_path}"
                if package_version.startswith('3'):
                    properties = set((item.get('properties') or '').split())
                    if 'nav' in properties:
                        nav_item_href = href

            if package_version.startswith('3'):
                if not nav_item_href:
                    return False, "EPUB 3 package is missing a manifest nav document"
                nav_path = _archive_relpath(opf_dir, nav_item_href)
                try:
                    nav_tree = etree.fromstring(zf.read(nav_path))
                except Exception as exc:
                    return False, f"Nav document is not parseable XML: {exc}"
                if not _has_toc_nav(nav_tree):
                    return False, "EPUB 3 nav document is missing nav epub:type=\"toc\""

            encryption_path = 'META-INF/encryption.xml'
            if encryption_path in archive_entries:
                try:
                    enc_tree = etree.fromstring(zf.read(encryption_path))
                except Exception as exc:
                    return False, f"encryption.xml is invalid XML: {exc}"
                for cipher in enc_tree.findall(f'.//{{{NS_XMLENC}}}CipherReference'):
                    uri = cipher.get('URI', '')
                    if not uri:
                        continue
                    if not _encryption_target_exists(archive_entries, uri):
                        return False, f"encryption.xml references missing file: {uri}"

            return True, ""

    except zipfile.BadZipFile:
        return False, "Not a valid ZIP file"
    except Exception as e:
        return False, str(e)


def has_drm(epub_path: str) -> bool:
    """Check if an EPUB file contains DRM encryption."""
    try:
        with zipfile.ZipFile(epub_path, 'r') as zf:
            if 'META-INF/encryption.xml' in zf.namelist():
                # Read encryption.xml to confirm it's actual DRM
                enc_content = zf.read('META-INF/encryption.xml').decode('utf-8', errors='ignore')
                # Font obfuscation is not DRM - check for actual encryption methods
                if 'http://www.w3.org/2001/04/xmlenc' in enc_content:
                    # Check if it's only font obfuscation
                    if 'http://www.idpf.org/2008/embedding' in enc_content or \
                       'http://ns.adobe.com/pdf/enc' in enc_content:
                        # Could be font obfuscation only - check for other encryption
                        if 'http://ns.adobe.com/adept' in enc_content or \
                           'EncryptedData' in enc_content:
                            # Count encrypted items - if only fonts, likely just obfuscation
                            from lxml import etree
                            try:
                                tree = etree.fromstring(enc_content.encode('utf-8'))
                                encrypted = tree.findall('.//{http://www.w3.org/2001/04/xmlenc#}EncryptedData')
                                # If we have encrypted content files (not just fonts), it's DRM
                                for item in encrypted:
                                    cipher = item.find('.//{http://www.w3.org/2001/04/xmlenc#}CipherReference')
                                    if cipher is not None:
                                        uri = cipher.get('URI', '')
                                        ext = Path(uri).suffix.lower()
                                        if ext not in {'.ttf', '.otf', '.woff', '.woff2'}:
                                            return True
                            except Exception:
                                return True
                    else:
                        return True
            return False
    except Exception:
        return False


def find_opf_path(epub_dir: str) -> str:
    """
    Find the OPF file path by reading META-INF/container.xml.
    Returns the path relative to the EPUB root directory.
    """
    container_path = os.path.join(epub_dir, 'META-INF', 'container.xml')

    if not os.path.exists(container_path):
        # Fallback: search for .opf file
        for root, dirs, files in os.walk(epub_dir):
            for f in files:
                if f.endswith('.opf'):
                    return os.path.relpath(os.path.join(root, f), epub_dir)
        raise FileNotFoundError("No OPF file found in EPUB")

    tree = etree.parse(container_path)
    root = tree.getroot()

    # Find rootfile element
    ns = {'container': 'urn:oasis:names:tc:opendocument:xmlns:container'}
    rootfile = root.find('.//container:rootfile', ns)
    if rootfile is None:
        # Try without namespace
        rootfile = root.find('.//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile')
    if rootfile is None:
        # Wildcard fallback
        for child in root.iter():
            tag = child.tag if isinstance(child.tag, str) else ''
            if tag.endswith('}rootfile') or tag == 'rootfile':
                rootfile = child
                break

    if rootfile is None:
        raise FileNotFoundError("No rootfile found in container.xml")

    return rootfile.get('full-path')


def _archive_relpath(base_dir: Path, href: str) -> str:
    clean_href = unquote((href or '').split('#', 1)[0])
    return os.path.normpath(os.path.join(str(base_dir), clean_href)).replace(os.sep, '/')


def _has_toc_nav(nav_tree) -> bool:
    nav_elements = nav_tree.findall(f'.//{{{NS_XHTML}}}nav')
    for nav in nav_elements:
        epub_type = nav.get(f'{{{NS_EPUB}}}type') or nav.get('epub:type')
        if epub_type and 'toc' in epub_type.split():
            return True
    return False


def _encryption_target_exists(archive_entries: set[str], href: str) -> bool:
    return any(
        candidate in archive_entries
        for candidate in _candidate_archive_paths(Path('META-INF'), href)
    )


def _candidate_archive_paths(base_dir: Path, href: str) -> list[str]:
    clean_href = unquote((href or '').split('#', 1)[0])
    if not clean_href:
        return []
    candidates = [
        os.path.normpath(clean_href).replace(os.sep, '/'),
        os.path.normpath(os.path.join(str(base_dir), clean_href)).replace(os.sep, '/'),
    ]
    return list(dict.fromkeys(candidates))
