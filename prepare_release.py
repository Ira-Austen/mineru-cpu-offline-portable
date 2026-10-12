"""为 GitHub Release 准备分发包。
处理超出 GitHub Release 2GiB 单文件限制的文件分卷，并生成发布说明与完整哈希清单。
"""
import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

MAX_RELEASE_FILE_SIZE = 2_000_000_000  # 2,000,000,000 字节，预留充足余量（低于 GitHub 2GiB 限制）
CHUNK_SIZE = 1_500_000_000           # 1.5 GB 分卷大小


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def split_file(file_path: Path, output_dir: Path, chunk_size: int = CHUNK_SIZE):
    """将大文件按固定大小分卷输出到 output_dir。"""
    is_zip = file_path.suffix == '.zip'
    base_name = file_path.name
    part_files = []
    with file_path.open('rb') as stream:
        idx = 1
        while chunk := stream.read(chunk_size):
            if is_zip:
                part_name = f"{base_name}.{idx:03d}"
            else:
                part_name = f"{base_name}.part{idx}"
            part_path = output_dir / part_name
            part_path.write_bytes(chunk)
            part_files.append((part_path, len(chunk), sha256(part_path)))
            idx += 1
    return part_files


def generate_release_notes(packages_info, checksum_records, notes_path: Path):
    notes = [
        "# MinerU CPU 离线便携包 (Portable Offline)",
        "",
        "纯 CPU 离线便携包，包含官方 MinerU 4.0.12 WebUI 与 API 服务。",
        "无需目标机安装 Python、Docker、CUDA，也无需 root 或管理员权限。所有可写状态集中在 `data/` 下。",
        "",
        "## 产物清单与下载说明",
        "",
        "| 平台 | 产物包名 | 类型 | 说明 |",
        "| :--- | :--- | :--- | :--- |",
    ]

    for p in packages_info:
        name = p['name']
        size_mb = p['size'] / (1024 * 1024)
        size_str = f"{size_mb:.1f} MB" if size_mb < 1024 else f"{size_mb/1024:.2f} GB"
        flavor = p['flavor']
        platform = p['platform']
        desc = p['desc']
        if p.get('is_split'):
            parts_str = ", ".join(f"`{part[0].name}`" for part in p['parts'])
            notes.append(f"| {platform} | {parts_str} | {flavor} (分卷) | {desc}（因超 GitHub 2GiB 限制拆分为分卷，大小合计 {size_str}） |")
        else:
            notes.append(f"| {platform} | `{name}` | {flavor} | {desc}（{size_str}） |")

    notes.extend([
        "",
        "## 分卷包合并指南 (Full 完整版)",
        "",
        "因 GitHub Releases 限制单个文件不能超过 2 GiB，Full 完整包拆分为分卷发布。下载后合并即可使用：",
        "",
        "### Windows 完整版合并：",
        "下载 `MinerU-Windows-x64-Full.zip.001` 与 `MinerU-Windows-x64-Full.zip.002` 至同一文件夹，在终端执行：",
        "```cmd",
        "copy /b MinerU-Windows-x64-Full.zip.001 + MinerU-Windows-x64-Full.zip.002 MinerU-Windows-x64-Full.zip",
        "```",
        "*注：也可直接使用 7-Zip 或 WinRAR 右键 `MinerU-Windows-x64-Full.zip.001` 选择“解压到当前文件夹”，无需手动拼接。*",
        "",
        "### Linux / UOS 完整版合并：",
        "下载 `MinerU-UOS20-x64-Full.tar.gz.part1` 与 `MinerU-UOS20-x64-Full.tar.gz.part2` 至同一目录，在终端执行：",
        "```bash",
        "cat MinerU-UOS20-x64-Full.tar.gz.part* > MinerU-UOS20-x64-Full.tar.gz",
        "tar -xzf MinerU-UOS20-x64-Full.tar.gz",
        "```",
        "",
        "## 快速启动",
        "",
        "### Windows 11 x64",
        "1. 解压 ZIP 归档",
        "2. 双击运行 `Start.cmd`",
        "3. 浏览器自动打开设置页 `http://127.0.0.1:7862/`（Full版自动启动服务，Custom/Runtime填入模型路径即可启动）",
        "4. 服务就绪后访问官方 WebUI：`http://127.0.0.1:7860/`",
        "",
        "### UOS20 / Linux (x86_64, glibc >= 2.28)",
        "```bash",
        "tar -xzf <文件名>.tar.gz",
        "cd <解压目录>",
        "bash start.sh",
        "```",
        "",
        "## 文件校验码 (SHA-256)",
        "```text",
    ])

    for line in checksum_records:
        notes.append(line)
    notes.extend(["```", ""])

    notes_path.write_text('\n'.join(notes), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--assets-dir', required=True, help='Actions 归档所在目录')
    parser.add_argument('--reports-dir', default='', help='测试与诊断报告目录')
    parser.add_argument('--max-size', type=int, default=MAX_RELEASE_FILE_SIZE)
    parser.add_argument('--chunk-size', type=int, default=CHUNK_SIZE)
    args = parser.parse_args()

    assets_dir = Path(args.assets_dir).resolve()
    dist_dir = assets_dir / 'dist'
    dist_dir.mkdir(parents=True, exist_ok=True)

    packages_info = []
    checksum_lines = []

    # 查找所有产物包
    archives = sorted(list(assets_dir.glob('*.zip')) + list(assets_dir.glob('*.tar.gz')))
    for archive in archives:
        size = archive.stat().st_size
        hash_val = sha256(archive)
        name = archive.name

        platform = 'Windows x64' if 'Windows' in name else 'UOS20 / Linux'
        if 'Full' in name:
            flavor = '完整版'
            desc = '内置全部 GGUF (1.2B) 与 ONNX 模型，开箱即用'
        elif 'Custom' in name:
            flavor = '内置 ONNX 版'
            desc = '内置全部 ONNX 小模型，外部指定已有 GGUF 模型路径'
        else:
            flavor = '便携运行时版'
            desc = '仅含 Python 环境与 WebUI，全部模型使用外部已有路径'

        if size > args.max_size:
            print(f"产物 {name} 大小 ({size:,} 字节) 超过上限，以每卷 {args.chunk_size:,} 字节执行自动分卷...")
            parts = split_file(archive, dist_dir, chunk_size=args.chunk_size)
            packages_info.append({
                'name': name,
                'size': size,
                'platform': platform,
                'flavor': flavor,
                'desc': desc,
                'is_split': True,
                'parts': parts,
                'sha256': hash_val
            })
            for part_path, part_size, part_sha in parts:
                checksum_lines.append(f"{part_sha}  {part_path.name}")
            checksum_lines.append(f"# (完整合并文件) {hash_val}  {name}")
        else:
            target_path = dist_dir / name
            shutil.copy2(archive, target_path)
            packages_info.append({
                'name': name,
                'size': size,
                'platform': platform,
                'flavor': flavor,
                'desc': desc,
                'is_split': False,
                'sha256': hash_val
            })
            checksum_lines.append(f"{hash_val}  {name}")

    # 复制报告与附加文件
    for rep in assets_dir.glob('*.json'):
        shutil.copy2(rep, dist_dir / rep.name)
    if args.reports_dir:
        reports_dir = Path(args.reports_dir).resolve()
        if reports_dir.exists():
            for rep in reports_dir.glob('*.json'):
                shutil.copy2(rep, dist_dir / rep.name)

    # 写入最终 SHA256SUMS.txt 与 RELEASE_NOTES.md
    (dist_dir / 'SHA256SUMS.txt').write_text('\n'.join(checksum_lines) + '\n', encoding='utf-8')
    generate_release_notes(packages_info, checksum_lines, assets_dir / 'RELEASE_NOTES.md')
    print(f"Release 资产准备完成，输出于 {dist_dir}：")
    for f in dist_dir.iterdir():
        print(f"  - {f.name} ({f.stat().st_size:,} 字节)")


if __name__ == '__main__':
    main()
