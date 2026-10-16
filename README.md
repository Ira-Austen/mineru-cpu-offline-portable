# MinerU CPU 离线便携包

固定 MinerU 4.0.12、mineru-llama-cpp 0.1.3、Python 3.12.13。无需目标机安装 Python、Docker、CUDA，也不需要 root 或管理员权限。所有可写状态在解压目录的 `data/` 下。请解压到普通用户有写权限的本地磁盘。

## 六种产物

| 平台 | 完整版 | 内置 ONNX | 全部模型用外部路径 |
| --- | --- | --- | --- |
| Windows 11 x64 | `MinerU-Windows-x64-Full.zip` | `MinerU-Windows-x64-Custom.zip` | `MinerU-Windows-x64-Runtime.zip` |
| UOS20 / Linux x86_64 | `MinerU-UOS20-x64-Full.tar.gz` | `MinerU-UOS20-x64-Custom.tar.gz` | `MinerU-UOS20-x64-Runtime.tar.gz` |

三个版本均包含便携 Python、全部 Python 依赖和官方 WebUI。Full 和 Custom 包含 ONNX 小模型；Runtime 不包含 GGUF、mmproj 和 ONNX。

完整版还包含用户提供并测试过的 `MinerU2.5-Pro-2605-1.2B-Q4_K_M.gguf` 和配套 `mmproj-MinerU2.5-Pro-2605-1.2B-f16.gguf`。自定义路径版省去这两个 GGUF 文件，填写已有路径后使用。它仅支持 MinerU2.5-Pro-2605 配套文件；不支持任意替代模型。

所有版本都能指定已有 ONNX 目录。更新程序时可下载 Runtime，填写原来的 GGUF、mmproj 和 ONNX 路径；无需复制或重新下载模型。

## Windows 启动

解压 ZIP，双击 `Start.cmd`。浏览器会打开设置页 `http://127.0.0.1:7862/`。

完整版自动启动。自定义版填写 GGUF 和 mmproj 的完整路径，点击“保存并启动”。路径支持中文和空格，不需要复制原有模型。也可以编辑 `settings.json`；Windows 反斜杠需写成 `\\`，或使用 `/`。

ONNX 路径应指向包含 `Layout/`、`OCR/`、`MFR/`、`Table/` 的完整目录，必须包含配套 `.yml` 文件。也可选择包含 `MinerU-4_models_onnx/` 的上一级目录。留空使用包内 ONNX，Runtime 必须填写。目录名称可自定义，模型目录可以只读；程序与模型下载锁都保存在新包的 `data/` 下。设置文件字段为 `onnx_dir`。

服务就绪后访问 `http://127.0.0.1:7860/`，即官方 MinerU WebUI。上传本地 PDF/图片。追求模型识别质量时选 Advanced，普通印刷文档可以先选 Standard。关闭启动终端或按 Ctrl+C 可停止服务，也可点击设置页的停止按钮。

## UOS 启动（普通用户，无 root）

```bash
tar -xzf MinerU-UOS20-x64-Full.tar.gz
cd MinerU-UOS20-x64-Full
./start.sh
```

自定义版同理。启动脚本 `start.sh` 会自动检测并修复 `python3.12` 与 `llama-server` 的执行权限；若使用的特殊解压工具破坏了权限，脚本亦会自动处理，无需手动 chmod 或 root/sudo 权限。无需 apt 或系统 Python。

UOS 模型路径例如 `/home/user/models/MinerU2.5-Pro-2605-1.2B-Q4_K_M.gguf`。完整版使用相对路径，所以可以搬到另一台机器或另一个目录。

## 性能、资源与诊断

- 纯 CPU：GGUF GPU layers=0，mmproj 不卸载到 GPU，ONNX 使用 CPU。
- 单任务、单 VLM slot、单页处理窗口；Windows 默认 6 线程，UOS 默认 4 线程。
- 默认上下文 8192，与用户现有模型的训练上下文一致；MinerU 按布局裁块识别。不要通过限制输出到 512 token 来“提速”，会截断 OCR 内容。
- 至少 16GB 内存；运行时关闭其他占用内存的软件。实际每页耗时取决于页面内容。
- 在本机 AMD Ryzen 5 5600X 上，以 6 线程识别四行中文、数字和英文的扫描式 PDF：Standard 约 19 秒，Advanced 约 74 秒，两者均正确识别姓名、编号与金额。这是简短测试页，不代表复杂 A4 文档或兆芯 CPU 的性能。
- **长文档处理与超时保护**：官方 MinerU 默认轮询超时为 3600 秒（1 小时），纯 CPU 模式解析 20+ 页 PDF 时易触发 `Job did not complete within timeout` 中断。便携包内置动态补丁，默认将轮询超时放宽至 86400 秒（24 小时），并支持在设置页或 `settings.json`（`poll_timeout`）中自定义。
- **识别前清除红色印章**：页面位图渲染完成后、进入版面与文字识别之前，先清除“约 1.2cm 见方、接近正方形”的红色印章区块，印章位置因此恢复成普通文字，不会再在结果里变成图片而打断排版。红色标题、红色批注等红字保留；印章上的文字（单位名称等）不再单独识别；很淡的印章只清掉主体笔画。官方 WebUI 左侧控制列底部有“识别前清除红色印章”勾选框，勾选或取消对后续解析立即生效、无需重启；启动设置页的同一开关与它共用 `data/seal-clean.txt` 状态。
- **旧浏览器（UOS20 自带浏览器等）**：官方 WebUI 由 Gradio 6 构建，Chromium 80~97 内核无法完整运行（前端产物使用了 `??=`、`structuredClone`、顶层 `await import` 等新语法/接口）。便携包为此内置**旧浏览器兼容页** `http://127.0.0.1:7861/`：上传、档位、强制 OCR、页码范围、任务状态、Markdown/JSON 查看、Markdown/JSON/结构化 JSON/zip 下载、原文件预览（图片与 PDF）与去印章开关，全部不依赖新浏览器；同时官方页会注入一层兼容垫片，可修复 Chromium 89~97 上的部分组件。Chromium 80~84 请直接使用兼容页。兼容页的导出能力与官方界面一致：Markdown/JSON/结构化 JSON/zip 来自解析服务，HTML/DOCX/LaTeX/EPUB/PDF 由本地渲染器按需生成（首次点击稍慢）；EPUB/OFD/MHTML 的页面内预览仍只在官方界面提供。
- 设置保存后重启启动器生效。端口 7860/7861/7862/8000/30000 必须未被其他程序占用。
- 故障日志：`data/logs/vlm.log`、`api.log`、`webui.log`。
- 自检：Windows `Start.cmd --check`；Linux `bash start.sh --check`。
- 所有 HTTP 服务只监听 127.0.0.1。Python 子服务禁止外部 DNS 和网络连接；llama-server 使用 `--offline`。
- 缺少模型时明确报错；目标机启动脚本没有自动安装、升级或下载逻辑。

## Linux 兼容性范围

目标为 UOS20 x86_64、glibc >= 2.28，CPU 需要满足 ONNX Runtime 和 llama.cpp 的指令集要求。包内 ELF 审计记录在附带报告中。静态检查不等同于在真实 UOS20/兆芯机器上完成推理测试；请在目标机执行自检后用代表性文档验证。

## 重新构建

`requirements-windows.lock`、`requirements-linux.lock` 固定当前依赖版本。构建流程参考 Ira-Austen 的 UOS 离线包 GitHub Action，所有网络下载只发生在联网构建机。在联网 Windows 构建机安装 uv 后执行：

```powershell
pwsh -File ./build.ps1 -GgufDirectory 'E:/models/MinerU2.5-Pro-2605-1.2B-GGUF'
```

已有 ONNX 时指定 `-OnnxDirectory 'E:/models/MinerU-4_models_onnx'`，构建脚本会复用已有目录，不访问模型下载服务。未指定时，脚本只下载不完整的模型仓库；缓存完整时会直接复用。

产物位于源码目录的 `output/`。模型目录需要包含上述两个准确文件名。

## GitHub Actions 构建与测试

仓库：https://github.com/Ira-Austen/mineru-cpu-offline-portable

工作流为 `.github/workflows/build-test.yml`。推送程序、测试、锁文件或模型清单到 main 会自动执行，也可在 Actions 页面选择“构建并测试 MinerU CPU 离线包”→“Run workflow”。

构建机会获取清单中固定哈希的 GGUF，复用或获取 ONNX，然后生成六个归档。测试包括单元测试、逐文件哈希、压缩包权限、Windows 中文空格路径、官方 WebUI、Standard/Advanced 扫描 PDF OCR，以及全部外部模型路径。

Linux 测试在 Debian 10（glibc 2.28）容器内以 UID 1000、`--network none` 运行，模型目录只读。容器只用于 CI 验证，目标 UOS 不需要 Docker。这项测试不能替代兆芯 CPU 实机验证。

构建产物与下载位置：
- **GitHub Releases**：工作流在 Windows 实测与 Linux 容器实测全部通过后，会自动创建对应版本的 GitHub Release 并发布离线包与 SHA256 校验清单。
  - 受 GitHub Releases 单文件 2 GiB 上限约束，Full 完整包通过内置分卷工具切分为 `.001`/`.002`（Windows）或 `.part1`/`.part2`（Linux），下载后可通过单行命令合并（Windows 亦可使用 7-Zip 直接右键解压）。
  - Custom 与 Runtime 包均为单文件发布。
- **Actions Artifacts**：运行底部的 `mineru-windows`、`mineru-linux` 亦保留整包 7 天，测试报告保留 14 天。

### 更新程序与模型复用

- `gguf-models.json` 固定 GGUF 下载版本、文件大小和 SHA256；Actions 缓存键只取该清单。
- `onnx-models.json` 单独控制 ONNX 缓存版本；Actions 缓存键不包含 MinerU 依赖锁文件。
- 只更新 MinerU 依赖并不会自动使模型缓存失效。确需更新 ONNX 模型时修改 ONNX 缓存版本；GGUF 更新时修改文件清单。
- GitHub 的缓存可能因过期或容量不足而被淘汰，所以云端缓存复用并非永久保证。本地指定模型路径的 Runtime 部署不依赖 GitHub 缓存。
- 库升级可能新增必要文件、改变模型格式或模型接口；必须重新构建并运行测试，不能承诺任何新版本都兼容所有旧模型。本包固定 4.0.12，未自动升级依赖。

源代码和公开资料见 `SOURCES.md`。各第三方组件许可保留在包内 Python 发行目录和各 `.dist-info/licenses/` 中。使用 MinerU 时遵循其开源许可。
