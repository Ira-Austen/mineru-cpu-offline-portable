"""仅联网构建机使用；已存在且校验正确的模型不会重新下载。"""
import argparse
import hashlib
import json
import time
import urllib.request
from pathlib import Path


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def download_file(url, target, expected_bytes, expected_sha, retries=3):
    temporary = target.with_suffix('.part')
    for attempt in range(1, retries + 1):
        try:
            print(f'正在下载 {target.name} (第 {attempt}/{retries} 次尝试)...', flush=True)
            req = urllib.request.Request(
                url,
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) MinerU-Builder'}
            )
            downloaded = 0
            hasher = hashlib.sha256()
            with urllib.request.urlopen(req, timeout=60) as resp, temporary.open('wb') as out:
                while chunk := resp.read(8 * 1024 * 1024):
                    out.write(chunk)
                    hasher.update(chunk)
                    downloaded += len(chunk)
            if downloaded != expected_bytes or hasher.hexdigest() != expected_sha:
                raise ValueError(
                    f'模型完整性校验失败：{target.name} '
                    f'(大小: {downloaded}/{expected_bytes}, 哈希: {hasher.hexdigest()[:8]} vs {expected_sha[:8]})'
                )
            temporary.replace(target)
            print(f'下载并校验完成：{target.name}', flush=True)
            return
        except Exception as exc:
            if temporary.exists():
                temporary.unlink(missing_ok=True)
            if attempt == retries:
                raise
            print(f'下载遇到异常 ({exc})，将在 {attempt * 3} 秒后重试...', flush=True)
            time.sleep(attempt * 3)


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
        download_file(item['url'], target, item['bytes'], item['sha256'])


if __name__ == '__main__':
    main()
