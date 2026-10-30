# Crayotter

<p align="center">
  <a href="./README.md">English</a> | <a href="./README_CN.md">中文</a>
</p>

<p align="center">
  <img src="./logo.png" alt="Crayotter Logo" width="180" />
</p>

<p align="center">
  <a href="https://idwts.github.io/Crayotter" target="_blank" rel="noopener noreferrer">
    <img src="https://img.shields.io/badge/🚀-在线演示-4CAF50?style=for-the-badge&logo=googlechrome&logoColor=white" alt="在线演示">
  </a>
  <a href="https://github.com/idwts/Crayotter/stargazers" target="_blank" rel="noopener noreferrer">
    <img src="https://img.shields.io/github/stars/idwts/Crayotter?style=for-the-badge&logo=github&label=Star%20Crayotter&color=ffb347" alt="在 GitHub Star Crayotter">
  </a>
  <a href="https://idwts.github.io/Crayotter/paper/" target="_blank" rel="noopener noreferrer">
    <img src="https://img.shields.io/badge/Blog-Paper%20Page-6f7cff?style=for-the-badge" alt="论文博客">
  </a>
  <a href="https://arxiv.org/abs/2606.07636" target="_blank" rel="noopener noreferrer">
    <img src="https://img.shields.io/badge/arXiv-2606.07636-b31b1b?style=for-the-badge&logo=arxiv&logoColor=white" alt="arXiv 论文">
  </a>
  <a href="https://arxiv.org/abs/2608.02694" target="_blank" rel="noopener noreferrer">
    <img src="https://img.shields.io/badge/arXiv-2608.02694-b31b1b?style=for-the-badge&logo=arxiv&logoColor=white" alt="arXiv 论文">
  </a>
  <a href="./LICENSE" target="_blank" rel="noopener noreferrer">
    <img src="https://img.shields.io/badge/License-PolyForm%20Noncommercial%201.0.0-blue?style=for-the-badge" alt="许可证">
  </a>
</p>

<p align="center">
  如果这个项目能切实帮助到您，欢迎在 GitHub 上给 Crayotter 点一个 Star。
</p>

Crayotter 帮你把一句剪辑需求变成视频：上传自己的素材，或让它搜索网络素材，再自动选镜头、剪辑、配音、加字幕并导出。

> 例如：“用我上传的视频做一条30秒旅行短片，横屏、舒缓清新，配中文旁白和字幕。”

[观看演示](https://idwts.github.io/Crayotter/#demo) · [下载桌面版](https://github.com/idwts/Crayotter/releases) · [反馈问题](https://github.com/idwts/Crayotter/issues)

## 适合用来做什么

- 从旅行、校园、活动等素材中制作短片，支持本地上传和网络素材搜集。
- 按你的要求调整时长、横竖屏和风格，添加转场、旁白及中文字幕。
- 在工作台查看进度、预览成片、查看历史任务；中断后可从可用检查点继续。

适合愿意查看成片并做最终确认的创作者。联网素材获取取决于平台访问情况，部分平台需要你在浏览器中登录；模型也可能误判画面，发布前请检查旁白与镜头。

## 安装与打开

| 平台 | 获取方式 | 打开方式 |
| --- | --- | --- |
| Windows 10/11 x64 | [Releases](https://github.com/idwts/Crayotter/releases) 中的 `Crayotter-Windows-x64.zip` | 完整解压后运行 `Crayotter.exe` |
| macOS Apple Silicon | 云端测试构建见 [Actions](https://github.com/idwts/Crayotter/actions/workflows/desktop-build.yml)；正式包以 Releases 为准 | 解压后双击 `Crayotter.app`，也可移到“应用程序” |

桌面包自带 Python、FFmpeg 和下载器，无需安装 Conda。Windows 独立窗口需要 Edge WebView2，缺少时会尝试浏览器模式。当前 Mac 包声明最低 macOS 15.0，仅支持已验证的 arm64 构建；测试包尚未做 Apple 公证。

## 第一次使用

1. **配置模型**：打开“设置”，填写自己的 API Key、服务地址和模型。模型调用会产生对应服务商的费用，项目不提供内置额度。
2. **选择素材**：上传本地视频，或在需求中写清题材，让它在线搜集；也可以提供视频链接。
3. **写清需求**：说明目标时长、风格、横竖屏，以及是否需要旁白和字幕，然后启动任务。
4. **查看结果**：在任务详情中预览、保存最终视频，并核对旁白、字幕和素材内容。

当前默认模型为 `qwen3.8-flash`（规划与剪辑）、`qwen3.8-omni-flash`（音视频分析）、`qwen3-tts-flash`（配音）。只需要视觉分析时，可将视频模型设为 `qwen3.8-flash`。已有自定义模型配置会保留。

网络素材、视频分析和模型思考都需要时间，实际处理时长取决于素材数量、长度和服务响应。可以先用约30秒的任务试跑。

## 常见问题

**成片保存在哪里？**
在工作台任务详情中查看最终产物。Mac 默认运行目录是 `~/Library/Application Support/Crayotter`；Windows 优先使用程序所在的可写目录，否则使用用户应用数据目录。

**为什么搜索或下载失败？**
先检查平台能否正常访问。需要登录的平台，在本机浏览器完成授权后重试；也可以上传自己的素材继续剪辑。

**模型报错或配音失败怎么办？**
检查密钥、服务地址、模型可用性和余额。文本、视频、配音可以在设置中分别配置。

**中断后还能继续吗？**
工作台支持恢复被标记为“已中断”的任务。已失败任务与已中断任务不同，不能保证原地恢复。

## 从源码运行

需要 Python 3.10+，并安装 FFmpeg/FFprobe。项目会按平台选择已配备的二进制；Mac 缺少工具时可运行 `python script/setup_ffmpeg.py`。

```bash
python -m pip install -r requirements.txt
python script/run_backend.py --host 127.0.0.1 --port 8765
```

打开 `http://127.0.0.1:8765/ui/` 后在设置中填写 API 配置。需要独立窗口时，另装 `requirements-desktop.txt` 并运行 `python script/run_desktop.py`。

## 开发与研究

- [源码安装、环境变量与日志](docs/development_cn.md)
- [三阶段架构](docs/architecture_cn.md)
- [统一工具接口、MCP 与 FFmpeg](docs/tool_protocol_and_ffmpeg.md)
- [Windows 构建](packaging/build_windows.ps1) / [macOS 构建与签名说明](packaging/README_MACOS_CN.md)
- [论文与引用](docs/citation.md)；[实验性 RL 训练](phase3_rl/README_CN.md)与日常剪辑运行分开。

项目采用 [PolyForm Noncommercial 1.0.0](LICENSE)，商业使用请先核对许可条款。
