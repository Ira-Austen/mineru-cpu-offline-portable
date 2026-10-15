"""把固定 ONNX 仓库映射到用户目录，下载锁仍写入包内 data。"""
import os
from pathlib import Path


def configure_client_timeout(timeout: int | None = None) -> int:
    """延长 WebUI 客户端轮询等待 API Server 任务的最大超时时间。

    官方 MinerU 硬编码 _POLL_MAX_ATTEMPTS = 3600 (即 1 小时)，
    在 CPU 模式处理 20+ 页 PDF 时极易超过 1 小时触发超时中断。
    支持通过 MINERU_POLL_TIMEOUT 环境变量自定义（秒），默认设为 86400 (24 小时)。
    """
    if timeout is None:
        raw = os.environ.get('MINERU_POLL_TIMEOUT', '86400')
        try:
            timeout = int(raw)
        except (ValueError, TypeError):
            timeout = 86400
    final_timeout = max(timeout, 3600)
    try:
        import mineru.parser.api_client as api_client
        api_client._POLL_MAX_ATTEMPTS = final_timeout
    except Exception:
        pass
    return final_timeout


def configure_model_paths():
    configure_client_timeout()
    directory = os.environ.get('MINERU_PORTABLE_ONNX_DIR')
    if not directory:
        return
    from mineru.model.download import ModelRepo
    original = ModelRepo.local_dir

    def local_dir(repo):
        if repo.name == 'MinerU-4_models_onnx':
            return Path(directory)
        return original(repo)

    ModelRepo.local_dir = local_dir
