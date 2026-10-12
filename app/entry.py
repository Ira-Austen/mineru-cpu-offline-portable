"""使用包内依赖启动官方 MinerU 命令，避免依赖不可移动的 console-script。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'packages'))

from model_paths import configure_model_paths
configure_model_paths()

from mineru.kit.main import main

if __name__ == '__main__':
    main()
