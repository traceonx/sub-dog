"""Extract app bytecode only; never copy Windows runtime libraries.
Run with CPython 3.12. Input: the original PyInstaller executable.
"""
import hashlib
import importlib.util
import json
import marshal
from pathlib import Path
import struct
import sys
import zlib


def extract(executable, target):
    if sys.version_info[:2] != (3, 12):
        raise SystemExit('Requires CPython 3.12')
    raw = Path(executable).read_bytes()
    cookie = raw.rfind(b'MEI\x0c\x0b\x0a\x0b\x0e')
    if cookie < 0:
        raise ValueError('Not a PyInstaller archive')
    _, length, offset, size, version, _ = struct.unpack('!8sIIII64s', raw[cookie:cookie+88])
    if version != 312:
        raise ValueError('Expected Python 3.12 archive')
    start = cookie + 88 - length
    toc = raw[start+offset:start+offset+size]
    files = {}
    i = 0
    while i < len(toc):
        n, pos, packed, unpacked, compressed, kind = struct.unpack('!IIIIBc', toc[i:i+18])
        if n < 18:
            raise ValueError('Invalid archive entry')
        name = toc[i+18:i+n].rstrip(b'\0').decode().replace('\\', '/')
        i += n
        if name not in ('PYZ.pyz', 'zimuku_dl/data/glyph_bank.json'):
            continue
        data = raw[start+pos:start+pos+packed]
        files[name] = zlib.decompress(data) if compressed else data
        if len(files[name]) != unpacked:
            raise ValueError('Invalid archive size')
    pyz = files['PYZ.pyz']
    if pyz[4:8] != importlib.util.MAGIC_NUMBER:
        raise ValueError('Python bytecode magic mismatch')
    entries = dict(marshal.loads(pyz[struct.unpack('!I', pyz[8:12])[0]:]))
    target = Path(target)
    modules = []
    for name, (package, pos, size) in entries.items():
        if not (name == 'zimuku_dl' or name.startswith('zimuku_dl.')) or name == 'zimuku_dl.gui':
            continue
        parts = name.split('.')
        if not all(p.isidentifier() for p in parts):
            raise ValueError('Invalid module path')
        code = zlib.decompress(pyz[pos:pos+size])
        dest = target.joinpath(*parts, '__init__.pyc') if package else target.joinpath(*parts).with_suffix('.pyc')
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(importlib.util.MAGIC_NUMBER + struct.pack('<III', 0, 0, 0) + code)
        modules.append(name)
    bank = target / 'zimuku_dl/data/glyph_bank.json'
    bank.parent.mkdir(parents=True, exist_ok=True)
    bank.write_bytes(files['zimuku_dl/data/glyph_bank.json'])
    (target / 'provenance.json').write_text(json.dumps({
        'origin': Path(executable).name,
        'sha256': hashlib.sha256(raw).hexdigest(),
        'python': '3.12', 'modules': sorted(modules),
        'note': 'Recovered bytecode, not reconstructed source. Original GUI excluded.'
    }, ensure_ascii=False, indent=2) + '\n')
    print(f'Extracted {len(modules)} application modules into {target}')

if __name__ == '__main__':
    extract(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else 'engine')
