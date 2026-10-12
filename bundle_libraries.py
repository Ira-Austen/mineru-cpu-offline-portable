"""运行库随应用携带；目标机无需执行安装程序。"""
import argparse
import io
import shutil
import tarfile
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--work', required=True)
args = parser.parse_args()
work = Path(args.work).resolve()
linux = work / 'stage' / 'linux'
windows = work / 'stage' / 'windows'


def deb_members(path):
    with path.open('rb') as stream:
        if stream.read(8) != b'!<arch>\n':
            raise ValueError('不是 Debian ar 包')
        while header := stream.read(60):
            if len(header) != 60:
                raise ValueError('Debian 包头损坏')
            name = header[:16].decode().strip().rstrip('/')
            size = int(header[48:58])
            content = stream.read(size)
            if size % 2:
                stream.read(1)
            yield name, content


(linux / 'lib').mkdir(exist_ok=True)
(linux / 'licenses').mkdir(exist_ok=True)
for filename in ('libstdcpp.deb', 'libgcc.deb', 'gcc8-base.deb'):
    for name, content in deb_members(work / filename):
        if name.startswith('data.tar'):
            with tarfile.open(fileobj=io.BytesIO(content)) as archive:
                for member in archive:
                    basename = Path(member.name).name
                    if member.isfile() and basename.startswith(('libstdc++.so.', 'libgcc_s.so.')):
                        data = archive.extractfile(member).read()
                        if not data.startswith(b'\x7fELF'):
                            continue
                        library = 'libstdc++.so.6' if basename.startswith('libstdc++') else 'libgcc_s.so.1'
                        (linux / 'lib' / library).write_bytes(data)
                    if member.isfile() and basename == 'copyright':
                        (linux / 'licenses' / (filename + '-copyright.txt')).write_bytes(archive.extractfile(member).read())

(windows / 'lib').mkdir(exist_ok=True)
system32 = Path('C:/Windows/System32')
for filename in ('msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll', 'msvcp140_atomic_wait.dll',
                 'msvcp140_codecvt_ids.dll', 'vcruntime140.dll', 'vcruntime140_1.dll', 'concrt140.dll'):
    origin = system32 / filename
    if origin.exists():
        shutil.copy2(origin, windows / 'lib' / filename)
        shutil.copy2(origin, windows / 'packages' / 'mineru_llama_cpp' / 'bin' / filename)
# CPU 专用包不加载动态 Vulkan 后端，避免依赖显卡驱动及 libvulkan。
for target, filename in ((windows, 'ggml-vulkan.dll'), (linux, 'libggml-vulkan.so')):
    optional_backend = target / 'packages' / 'mineru_llama_cpp' / 'bin' / filename
    if optional_backend.exists():
        optional_backend.unlink()
print('运行库已放入包内；移除可选 Vulkan 后端。')
