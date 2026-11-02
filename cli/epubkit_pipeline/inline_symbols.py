"""Conservative image-to-text conversion for reviewed inline glyph assets.

Only exact original asset hashes in the registry are admitted. This is opt-in:
Unicode coverage must be supplied by the selected reader font. Filenames, alt
text, dimensions and OCR never establish a symbol's identity.
"""
import hashlib
from pathlib import Path
from urllib.parse import unquote, urlsplit
from lxml import etree

# Reviewed publisher glyphs across the retained NGNL Vols.1–12 and
# Practical War Game. Exact hashes only: distinct nominal dash lengths and
# filled/outline hearts/stars and note types remain distinct. Text, ornaments
# and ambiguous line-length assets are deliberately absent.
VERIFIED_SYMBOLS = {
    '01c9c073f69f7a9cf58cce7d941e2046fb45d38ae45582dd99fd3668bc066e6f': '♥',
    '01db66b8f669645a58fd65229785d3fbd6ee56e6de6f120cce0a11c348c61866': '♪',
    '0471103e98ea7fc25d139146fc3169292e1dd04c0f36d9655208385452518cf6': '♥',
    '05f5b431967b1ef77af21e43d001d7f14ee4d2e011d1d456e897d5077b26de8d': '★',
    '06a80ca8174f00af949033f3b1835fdede6f6f82a4afad64a58de01ac523ab56': '⸺⸺',
    '08612b57fe1442995fefc45c53ef830a01fa2f4946b44555110187ede5678854': '♪',
    '09b2994809ccba4e44c84079fe3227410d1d5637676b0d748c956ac5e35da715': '♡',
    '0d82c82d425da44504cce2ae54ff9eab163a39cd2b84e5bc7eeac0408e699cb1': '⸺⸺⸺⸺⸺',
    '11010d9759a33e9aacc59b8c5694a8c4b6e7521cb1dae0ce8da71a1a93da75d2': '⸺⸺⸺',
    '137a89cf6ab0aca6cb2b6af417d80de171a166472c67ba219940818a2e15b124': '⸺⸺⸺',
    '1440b03bbd70327565646e85c068fd8e19215ed81b0b5359af61693699393f12': '⸺⸺⸺',
    '14e9361260c1b7297e258403b0faba2a3214b034e5b90737d92885627e6beb41': '⸺⸺⸺—',
    '1980ea829dfe792ca8667061cb0afa8168fa77f4b1fa6c69057b253691d16373': '⸺⸺⸺',
    '19bf9445ceb4e3da4e56078b16ad49f1a3ac6e8ca5c673942de58269edb3081a': '♪',
    '1bc8de5642de73cf9136a319605afdbdfba1544629ab6b0799854717eed3a490': '♪',
    '21394b4838a08fafe1e43ad432243eaa9ebfdbd03ec668aeb12e14e181cfad21': '♪',
    '2241c0c98bff09a8155c941ccd18eec22e804ce1206e1a863755892caa51db56': '♥',
    '2258ce0b38eedcac3706e4c94ce2dc890e93c4b567e801f30d527ef103b4204f': '⸺⸺',
    '2c2ade14a0880480cb4151fcee7e2b1af4e1cdddffdda03b7c167de2b5ea24cb': '♥',
    '2f82e8a0ba247a302ceabd419a9db021bf541481c4b674f8f6f2f0d0c14b7d86': '⸺⸺',
    '3132cd63cf9acb0deaaba1137f5b0d0931a15724079177ebe3651e171477ef73': '♫',
    '33bf8e7db457ac981d772617b776361d3426675d16aeb44104479423e35bb1d0': '⸺⸺⸺',
    '38d5908c9dffc92b740b9e229739a4d4924873637fd9e3222c1b2ee4e34c15ea': '⸺⸺—',
    '3b0b52f86045e219c0db5556b79968a67a65f2afb1b90eeb175697e61bd7cda3': '♪',
    '3c1a8c137053f2ab26a565e65ef89916a43b18287982fbc88841ea0741d3e3c9': '⸺⸺⸺⸺',
    '3c4e468e6f4cec441b461631689ec5d70524201a621b98ddf81b17655545c6a2': '⸺',
    '3c715da2b5ff3219cea28c6b8cfe0415de1481d419ec860e5113d64214732f26': '♪',
    '3d6ef8ddc14e4f7a9db60c9f4c9cfaca0c4873ae1bc0f9d742f25003e041f4b8': '⸺⸺⸺⸺',
    '415edda8e0e25a1cd8fef7bcca0de485dd99c7cc8e872dd9ef85bf6e3ca535dd': '⸺⸺⸺⸺—',
    '4267ea4c5a39ae45446fd707018158d3a494239d80c15f86b0ee7dc0ece5e5e7': '⸺—',
    '499aec393c64d1d70b5efaca9e1fab07f42aa5d9474e6b4d95c7139131da2d35': '⸺⸺—',
    '4a56c3acb87f7201bc7062111bff278b428bce692d0e9bf972ed9aa05a130973': '⸺⸺—',
    '4b728f481bfd6a2a3f0b550a9a336efc33b5dd550d669261659fe63177c2ae83': '⸺—',
    '4c5f5631d936a870ee2539e47282983dad9ff78feb0eac415e77f66bb3a1ba99': '☆',
    '500ba3f138c2424aedcec7498ff177ea20ab1cbc80565af5fc6e863d178f072a': '⸺',
    '530f2f9457c8feb062513e065901e52d4e05d92d616f8c4ff46825b4af2e7d77': '⸺',
    '54d2868bbb6291cbf89fc175a7d5ab689fad3b4b3f7d97d2108950a67a142357': '♥',
    '569dd8ce4a78d0dac557725da89398956ff94e7cccc90571fb52075cab99fb66': '⸺⸺',
    '5b0a727f9ab29ef03ea88cd9ffd82fd42d49337a1e911188c358b034e1f05b79': '⸺⸺',
    '60b4f3c690812efac02bcfb5b404a1ca309f4028ae5796cd3a08c18a9649f871': '⸺—',
    '69c0dc456bfcecda3e4aa9667bee8f3c307d9a88f50d2551911e8e26bb156856': '⸺—',
    '6a371c39719423380da867207b69dcb577a782d459665bcf3af36c898d09ac4d': '♪',
    '6baa0e10dec8eec3aeaafc194b15739487876b4ba3249840836a2d1927b1fbd1': '⸺',
    '6e1938aa4146384be9ac4b77e2876ffb9d5b1e092ab6111e07b8283c98b1f79c': '★',
    '6eeab75602be2bccbae221929d972e5f8492a4c69ca0910f2462ecca3416a19e': '⸺⸺⸺—',
    '727f0a6b9b7eddf4321b684933a962d391be9330f8f2251d81d0fc430c97da74': '⸺—',
    '7327ebc1b1fd14420adddaaafdc56279a867cbb9396a4ae773b5e71060595ec6': '⸺—',
    '77b05089d60d1db689bb5332a34bd6bf873ca9fcf2f85684e718609a43b55515': '⸺⸺⸺',
    '7d8b92fc68d788eeaf3c380fc93fb0f56f8b6fdb1edea62c7185ee217a9accff': '⸺',
    '7db143adcbee7abcc57056b139ec63da20fe27243ec200a84e15235f1f9cd9e2': '♪',
    '84f6e36d839b7813a15bb0f261ec8a77533833555071f980330abf1c687b5513': '⸺⸺',
    '86525702d991e83284d90f9d494b8dcde3743c31c47e6356a556040f6f7dbca1': '⸺—',
    '89730e87067d9e2bcd8aa6861f7b1e52d7e11d163aac7a34b242cfa5ff52766b': '⸺⸺⸺⸺',
    '8db6b7db36ba616d0bf4ad84f6287efe215cf9f81b2c72bfa428cc3b3666de9c': '⸺⸺',
    '8f75d89675872b4b40bb1114243e27f52a1b314ae1822624a19ca48e94d5eb5f': '♥',
    '919695919a6fdedd8feca59acffffd6290718c7ecd2d8b5199cc6a993e097960': '⸺⸺—',
    '97d5facc8d5a3c21a33d27d975fbcf5b1fe39e1b2b388ec08549abcf04c58205': '☆',
    '980c643d8590e07361b17e6674b8aaef96268207fc97586a4797c3246a118521': '⸺',
    '9f7603e54777299be01b40d0f1292bc0c5b6f560feb11ebd9fb0df13a211320c': '⸺⸺',
    'a25a6bd3633e4d4500bd4345feae70cf4d4b598c72eb01ef22a876920ddc8fae': '♥',
    'a2b42ace0606cbc3e76286971b4cdb5f7750882ff792703f34ad99c93b1e739b': '⸺',
    'a50b2b0b90b651bc5d4f3466fe2222d63553e35c9c8eaad8bdc8a8c6fbc73e0e': '⸺⸺—',
    'a6b22a201c29b753ae273b4ad4a3fa625b8f966fcc77e8fb47d3ea13e73e9b39': '♥',
    'a88a55ee3c1ff780950a5db4f85fc840d4899bca94150d4c5c9bf20088f1a393': '⸺⸺',
    'ab81aad3cb33a248b5d66dd7f7dd3da4b1493c6a6b39872d4e0a487da0a9a9d9': '⸺⸺—',
    'b37b279cd177d9558b684966bf4ad1e7b0a9448b01593064702378af9515f08b': '★',
    'b6cbb585c47b40bc42184b1d0a27e28a9ff8bb96256f19b3c06822d94e1908b2': '⸺⸺⸺',
    'bc5345e070f6a73662d558a9bd5e73cb2e25b5c5ded07f57867c693c1ad6b438': '⸺⸺',
    'bd4aca2eac17f775e36f93bd447d471d8cb2030355d4f037a5fb58ffe4f1c9e8': '♥',
    'c2a29d7a2c5e174566dba834dc8726474c9f0ded783bb981792e405f4a9957c6': '⸺⸺⸺⸺',
    'c61688876c1337e774f32638223204132d256fd8c040741db608cd24fd7dd30a': '⸺⸺',
    'c7f45908a65b629263f0a323db8ac41200e0ca2a5ada5e351d1af277466959e3': '♥',
    'd80ffe10665035ad8d1a66cf64171de149f9e3adc766e71e542dd0b3d9a33dc1': '⸺—',
    'd8ab75d3e93c005182117010844b65ee317c4a0951c771893750bd44e8659e26': '☆',
    'db8d87c7f9248c3e9a6cbf7c75e70f73f3e1bd623124dce701d6eb8571a89b1c': '♥',
    'dde32687a974cf6b805ad2a6613bc876f8a6f170bffebd12be612cada9c44180': '⸺⸺⸺⸺⸺⸺⸺',
    'e390cd114ef67c38fbfb60ed8a47809711f6a0593f8bb74cad455955b20f6f47': '⸺',
    'e96fb04de24b8cceb4c4a8a4afaa62a6e06ad47837b2a270407b6e24cc8f3ca1': '⸺⸺⸺',
    'ec248ccdf14f78be790197aa379f77a25295f185fc9e30b9534e1b10b7c90067': '⸺',
    'ee2801ded0aacdb3c1fa897105f9a7f827ca53dd45f6d99747fe8773ded2e847': '⸺⸺',
    'f0be0b48d490d8200a771cdca5b2b7c72c29fedf3e8f712c967828b5a0152157': '♪',
    'f8afa999459abd704e699591ab4cc76d2315e6d43e548ca657cc5df4b1cb7fdc': '⸺—',
    'f9feaf07bcc886cdb22c06c7be798ea6e71b260d443689d33ef049f9ac5dde71': '♪',
    'fc718fdb86dda8b6b195c5cd501392948e0c9ed8522a9030fe19f5cf87b6dac7': '⸺⸺',
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
