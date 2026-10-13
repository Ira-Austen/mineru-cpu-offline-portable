"""在任意构建平台读取 Linux ELF 的直接依赖和 glibc 版本要求。"""
import argparse
import json
import re
import struct
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('root')
parser.add_argument('--output', required=True)
args = parser.parse_args()
root = Path(args.root).resolve()


def needed(data):
    if data[:6] != b'\x7fELF\x02\x01':
        return []
    phoff = struct.unpack_from('<Q', data, 32)[0]
    phsize, phcount = struct.unpack_from('<HH', data, 54)
    loads, dynamic = [], None
    for index in range(phcount):
        typ, _, offset, vaddr, _, size, _, _ = struct.unpack_from('<IIQQQQQQ', data, phoff + index * phsize)
        if typ == 1:
            loads.append((offset, vaddr, size))
        if typ == 2:
            dynamic = (offset, size)
    if not dynamic:
        return []
    tags = [struct.unpack_from('<qQ', data, i)
            for i in range(dynamic[0], dynamic[0] + dynamic[1], 16)]
    address = next((value for tag, value in tags if tag == 5), None)
    if address is None:
        return []
    base = next(offset + address - vaddr for offset, vaddr, size in loads if vaddr <= address < vaddr + size)
    result = []
    for tag, value in tags:
        if tag == 1:
            end = data.index(b'\0', base + value)
            result.append(data[base + value:end].decode())
    return result


files, bad = [], []
for path in root.rglob('*'):
    if not path.is_file():
        continue
    with path.open('rb') as f:
        if f.read(4) != b'\x7fELF':
            continue
    data = path.read_bytes()
    versions = sorted({tuple(map(int, m.split(b'.')))
                       for m in re.findall(rb'GLIBC_([0-9]+\.[0-9]+)', data)})
    highest = versions[-1] if versions else (0, 0)
    item = {'path': str(path.relative_to(root)), 'glibc': '.'.join(map(str, highest)),
            'needed': needed(data)}
    files.append(item)
    if highest > (2, 28):
        bad.append(item)
report = {'target': 'x86_64 glibc >= 2.28', 'elf_files': len(files), 'incompatible': bad, 'files': files}
Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
print(json.dumps({'elf_files': len(files), 'incompatible': bad,
                  'dependencies': sorted({dep for file in files for dep in file['needed']})}, ensure_ascii=False))
raise SystemExit(bool(bad))
