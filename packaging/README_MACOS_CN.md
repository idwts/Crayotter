# macOS 构建与使用

本机验证版本为 Apple Silicon（arm64），应用声明最低 macOS 15.0。Intel 版本需要在对应的 x86_64 Python 环境中单独构建，本次未验证。

解压 `Crayotter-macOS-arm64.zip` 后，将 `Crayotter.app` 拖到“应用程序”或其他目录，双击启动。程序包含 Python、前端、FFmpeg/FFprobe、yt-dlp 和中文字幕字体，无需另装 Conda。

首次使用在工作台设置中填写自己的 API 配置。发行包不包含开发机的密钥、上传素材、任务日志或测试视频。默认数据目录是 `~/Library/Application Support/Crayotter`，配置保存在其中的 `.env`；`CRAYOTTER_RUNTIME_ROOT` 仍可显式覆盖。应用不会向签名包内写入任务数据。

开发者在 macOS 项目根目录运行：

```bash
conda activate crayotter
packaging/build_macos.sh
```

构建脚本使用 `python3`；可用 `CRAYOTTER_BUILD_PYTHON=/absolute/path/to/python` 指定解释器。依赖已安装时加 `--skip-install`。如果修改了前端源码，先在 `app/frontend_src` 中执行 `npm run build`。

构建产物为 `dist/Crayotter.app` 和 `dist/Crayotter-macOS-arm64.zip`。图标使用项目原有 logo，下载器固定为 yt-dlp 2026.03.13，并校验发布文件 SHA256。FFmpeg/FFprobe 使用项目内当前架构版本。

可离线验证冻结后的图、工具注册、内置命令和裁剪/合并/中文字幕/导出链路：

```bash
CRAYOTTER_RUNTIME_ROOT=/tmp/crayotter-package-check \
  dist/Crayotter.app/Contents/MacOS/Crayotter --crayotter-self-test
```

自检不调用收费模型 API，临时媒体自动清理，结果写到指定运行目录的 `package_self_test.json`。后端 worker 入口仍为 `--crayotter-worker`。

未指定 `CRAYOTTER_MACOS_SIGN_IDENTITY` 时使用 ad-hoc 签名，适用于本机验证。本次包未做 Developer ID 签名和 Apple 公证；对外分发需要另行完成这些步骤。构建脚本会校验签名完整性，但该校验不代表已获 Apple 公证。

参考：[PyInstaller macOS 支持](https://www.pyinstaller.org/en/stable/feature-notes.html)、[pywebview 平台安装](https://pywebview.flowrl.com/guide/installation.html)。
