# Film Factory · 制片工厂

在本机工作台输入剧本和制作要求，选择「全自动成片」，工厂会拆镜、生成镜头、审片、处理有限次返工、剪辑与混音，并保存 `film.mp4`、`timeline.json` 和 `acceptance.json`。需要逐镜参与时，使用「逐镜确认」。RC3 的目标规格是 H.264 1920×1080、24 fps、AAC 48 kHz 双声道。

## 开始使用

1. 准备已有 Python 3.12、Pillow、FFmpeg、ffprobe、可用的本机 ComfyUI 工作流和文本/视觉模型。需要配乐时接入 ACE-Step。工作站模型、API 图、个人配置和历史片库由使用者自行提供。
2. 从仓库根目录复制 `config.example.json` 为 `config.local.json`。将模型地址、模型标识、字体、素材目录和工作流目录设为自己的配置。FFmpeg、ffprobe 可在 PATH 上，或填写各自的绝对路径。
3. Windows 用户可设 `FILM_PYTHON` 指向已有解释器，然后运行 `check.cmd` 检查命令入口。按[运行指南](RUNBOOK.md)完成图绑定与片库设置后运行 `start.cmd`。在浏览器打开 `http://127.0.0.1:8766`。
4. 先用自有的虚构剧本和两张测试图做三路短片验收，再提交正式剧本。[工作流配置](docs/workstation.md) · [实机验收](docs/acceptance.md) · [历史项目导入](docs/studio-import.md)

## 生产与验收

全自动模式依赖能工作的本机或已授权模型服务。默认关闭托管模型调用。无法确认远端回执、找不到采用资产、对白未核验或超出返工预算时，工作台会显示原因和可恢复状态。可验证的正式交付保留版本化目录与文件指纹。

- [分镜放映室](docs/storyboard.md)用于查看执行单、候选和跨镜连续性。
- [母版音频](docs/audio-master.md)与[剪辑工程导出](docs/editor-export.md)说明混音和移动交付。
- [RC3 验证范围及本地复现](docs/acceptance.md)记录离线测试和真机验证之间的边界。

运行回归时执行 `python -B -m unittest discover -s tests -v`。测试使用临时目录、回环模型替身与真实媒体工具。剪辑交换测试使用 OpenTimelineIO。
