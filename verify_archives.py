"""独立核对归档清单、模型差异、文件内容和 Linux 执行权限。"""
import argparse
import hashlib
import json
import tarfile
import zipfile
from pathlib import Path


def digest(stream):
    value = hashlib.sha256()
    for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
        value.update(block)
    return value.hexdigest()


def verify(path):
    root = path.name.removesuffix('.zip').removesuffix('.tar.gz')
    is_zip = path.suffix == '.zip'
    archive = zipfile.ZipFile(path) if is_zip else tarfile.open(path, 'r:gz')
    with archive:
        read = archive.open if is_zip else archive.extractfile
        names = archive.namelist() if is_zip else archive.getnames()
        manifest = json.load(read(root + '/manifest.json'))
        settings = json.load(read(root + '/settings.json'))
        gguf = [name for name in names if name.endswith('.gguf')]
        onnx = any(name.startswith(root + '/models/MinerU-4_models_onnx/') for name in names)
        assert len(gguf) == (2 if root.endswith('Full') else 0)
        assert onnx == (not root.endswith('Runtime'))
        assert bool(settings['gguf']) == root.endswith('Full')
        for record in manifest['files']:
            with read(root + '/' + record['path']) as stream:
                assert digest(stream) == record['sha256'], record['path']
        if not is_zip:
            for name in ('start.sh', 'runtime/python/bin/python3.12', 'packages/mineru_llama_cpp/bin/llama-server'):
                assert archive.getmember(root + '/' + name).mode & 0o111, name
    return {'archive': path.name, 'files': len(manifest['files']), 'gguf': len(gguf), 'onnx': onnx, 'passed': True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output)
    checksums = {line.split('  ')[1]: line.split('  ')[0]
                 for line in (output / 'SHA256SUMS.txt').read_text().splitlines()}
    reports = []
    for name, expected in checksums.items():
        path = output / name
        with path.open('rb') as stream:
            assert digest(stream) == expected, name
        reports.append(verify(path))
        print(json.dumps(reports[-1]), flush=True)
    (output / 'archive-verification.json').write_text(json.dumps(reports, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
