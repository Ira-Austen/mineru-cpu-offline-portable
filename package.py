"""输出四个具有校验清单的可移动离线压缩包。"""
import argparse
import gzip
import hashlib
import json
import tarfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--work', required=True)
parser.add_argument('--output', required=True)
parser.add_argument('--gguf-dir', required=True)
args = parser.parse_args()
work, output, gguf_dir = map(lambda x: Path(x).resolve(), (args.work, args.output, args.gguf_dir))
output.mkdir(parents=True, exist_ok=True)
gguf_files = ['MinerU2.5-Pro-2605-1.2B-Q4_K_M.gguf', 'mmproj-MinerU2.5-Pro-2605-1.2B-f16.gguf']


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def included(path, stage, platform):
    rel = path.relative_to(stage)
    if rel.parts[0] == 'data' or any(part in ('__pycache__', '.cache', '.locks', '.git') for part in rel.parts):
        return False
    if path.suffix == '.pyc' or rel.as_posix() in ('settings.json', 'manifest.json'):
        return False
    if platform == 'windows' and path.name == 'start.sh':
        return False
    if platform == 'linux' and path.name == 'Start.cmd':
        return False
    return path.is_file()


def build(platform):
    stage = work / 'stage' / platform
    payload = [(path, path.relative_to(stage).as_posix())
               for path in sorted(stage.rglob('*')) if included(path, stage, platform)]
    records = [{'path': rel, 'bytes': path.stat().st_size, 'sha256': sha256(path)} for path, rel in payload]
    for flavor in ('Runtime', 'Custom', 'Full'):
        name = f'MinerU-{"Windows" if platform == "windows" else "UOS20"}-x64-{flavor}'
        settings = {'gguf': '', 'mmproj': '', 'onnx_dir': '', 'threads': 6 if platform == 'windows' else 4,
                    'context': 8192, 'open_browser': True}
        entries = list(payload)
        manifest_records = list(records)
        if flavor == 'Runtime':
            entries = [(path, rel) for path, rel in entries if not rel.startswith('models/')]
            manifest_records = [record for record in manifest_records if not record['path'].startswith('models/')]
        if flavor == 'Full':
            settings['gguf'], settings['mmproj'] = (f'models/GGUF/{file}' for file in gguf_files)
            for file in gguf_files:
                path = gguf_dir / file
                entries.append((path, f'models/GGUF/{file}'))
                manifest_records.append({'path': f'models/GGUF/{file}', 'bytes': path.stat().st_size,
                                         'sha256': sha256(path)})
        generated = {
            'settings.json': json.dumps(settings, ensure_ascii=False, indent=2).encode(),
            'manifest.json': json.dumps({'name': name, 'mineru': '4.0.12', 'python': '3.12.13',
                'gguf': flavor == 'Full', 'onnx': flavor != 'Runtime',
                'cpu_only': True, 'files': manifest_records},
                ensure_ascii=False, indent=2).encode(),
        }
        if platform == 'windows':
            archive = output / (name + '.zip')
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as zip:
                for path, rel in entries:
                    zip.write(path, f'{name}/{rel}')
                for rel, content in generated.items():
                    zip.writestr(f'{name}/{rel}', content)
        else:
            import io
            archive = output / (name + '.tar.gz')
            with archive.open('wb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', compresslevel=1, mtime=0) as gz:
                with tarfile.open(fileobj=gz, mode='w') as tar:
                    # Windows 构建时恢复 Linux 执行位；文件内容不改动。
                    for path, rel in entries:
                        info = tar.gettarinfo(str(path), f'{name}/{rel}')
                        info.uid = info.gid = 0
                        info.uname = info.gname = ''
                        info.mode = 0o644
                        with path.open('rb') as file:
                            magic = file.read(4)
                            if magic == b'\x7fELF' or path.suffix == '.sh' or '/bin/' in f'/{rel}':
                                info.mode = 0o755
                            file.seek(0)
                            tar.addfile(info, file)
                    for rel, content in generated.items():
                        info = tarfile.TarInfo(f'{name}/{rel}')
                        info.size = len(content)
                        info.mode = 0o644
                        tar.addfile(info, io.BytesIO(content))
        print(json.dumps({'archive': archive.name, 'bytes': archive.stat().st_size}, ensure_ascii=False), flush=True)
        yield archive


def platform_build(platform):
    return list(build(platform))


if __name__ == '__main__':
    with ThreadPoolExecutor(max_workers=2) as pool:
        archives = [path for group in pool.map(platform_build, ('windows', 'linux')) for path in group]
    checksums = '\n'.join(f'{sha256(path)}  {path.name}' for path in sorted(archives)) + '\n'
    (output / 'SHA256SUMS.txt').write_text(checksums, encoding='utf-8')
