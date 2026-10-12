# 构建与测试记录

GitHub Actions 已实际构建六个离线包，并完成 Windows 与 Linux CPU 推理测试。

- 成功运行：https://github.com/Ira-Austen/mineru-cpu-offline-portable/actions/runs/38288783915
- 测试源码：`714c0df96cfd3ba059a808b3438f6309a28ce07f`
- 固定版本：MinerU 4.0.12、mineru-llama-cpp 0.1.3、Python 3.12.13。
- `build` 与 `linux-check` 两个任务均为 `success`。

## 验证范围

构建任务通过七个单元测试、六个归档的逐文件 SHA256 校验、159 个 Linux ELF 的 glibc 符号审计，以及 Windows 中文空格路径下的官方 WebUI 与扫描 PDF OCR。完整包测试 Standard、Advanced；Runtime 包配置已有 GGUF、mmproj、ONNX 路径后测试 Standard。

Linux 测试在 Debian 10（glibc 2.28）容器内执行，使用 UID 1000、`--network none`，完整包模型目录为只读。Full 与 Runtime 的自检、官方 WebUI、实际 OCR 均通过；Runtime 使用完整包中的只读模型目录。容器仅用于 CI 测试，UOS 目标机无需 Docker 或 root。

以下为 CI 中一页简短扫描 PDF（`CPU OCR 1234.56`）的耗时，不能用于估计复杂 A4 文档或兆芯 CPU 的速度。所有结果均包含正确的 `CPU OCR 1234.56`，WebUI HTTP 状态为 200。

| CI 环境与包 | Standard | Advanced |
| --- | ---: | ---: |
| Windows Full | 39.20 秒 | 97.42 秒 |
| Windows Runtime，外部模型 | 6.48 秒 | 未运行 |
| Linux Full，禁网普通用户 | 7.37 秒 | 93.44 秒 |
| Linux Runtime，外部只读模型 | 6.37 秒 | 未运行 |

本地 AMD Ryzen 5 5600X 的四行中文、英文、数字扫描 PDF 测试中，Standard 约 19 秒、Advanced 约 74 秒；姓名、编号、金额均正确。最终 Windows Runtime ZIP 解压到中文空格目录后，外部模型 OCR 也已通过。

## 下载与报告

在上述运行底部的 Artifacts 下载：

- `mineru-windows`：Windows 的 Full、Custom、Runtime 三个 ZIP。
- `mineru-linux`：UOS20/Linux 的 Full、Custom、Runtime 三个 tar.gz。
- `build-and-windows-reports`：逐文件校验、ELF 审计、Windows 推理结果及 `SHA256SUMS.txt`。
- `linux-test-reports`：Linux 推理结果与服务日志。

下载 Actions 产物需要登录 GitHub。包保留 7 天，报告保留 14 天；过期后可手动重新运行工作流。模型缓存独立于程序依赖锁，缓存仍存在时可复用；本地 Runtime 外部模型部署不依赖云端缓存。

## 未验证范围

尚未在真实 UOS20/兆芯 KX-7000 上运行。容器测试验证了 glibc 2.28、无 root、禁网和只读模型条件，不代表特定硬件指令集已经验证。目标机应先执行 `bash start.sh --check`，再使用代表性文档测试。

程序与模型可分开更新，但 MinerU 新版本可能改变模型文件、格式或接口。只有新版本自检和推理测试通过后，才能确认原模型继续兼容。
