"""仅联网构建机使用；已存在且校验正确的模型不会重新下载。"""
import argparse
import hashlib
import json
import urllib.request
from pathlib import Path


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dest', required=True)
    parser.add_argument('--manifest', default=str(Path(__file__).with_name('gguf-models.json')))
    args = parser.parse_args()
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
    for item in manifest['files']:
        target = dest / item['filename']
        if target.is_file() and target.stat().st_size == item['bytes'] and digest(target) == item['sha256']:
            print('复用已有模型：', target, flush=True)
            continue
        temporary = target.with_suffix('.gguf.part')
        urllib.request.urlretrieve(item['url'], temporary)
        if temporary.stat().st_size != item['bytes'] or digest(temporary) != item['sha256']:
            raise ValueError(f'模型完整性校验失败：{target.name}')
        temporary.replace(target)
        print('下载并校验：', target, flush=True)


if __name__ == '__main__':
    main()
