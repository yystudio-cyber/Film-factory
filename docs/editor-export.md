# 把已验收的片子交给剪辑师

任务状态为 `done` 后，在 Film-factory 目录执行。把 `JOB_ID` 换成控制台显示的任务号，目标选一个尚未创建的目录。导出从数据库选中的发行版读取时间线、采用镜头、混音报告与配乐。工作站只需现有的 Python、FFmpeg 和 ffprobe。

```powershell
D:\ai\venv\Scripts\python.exe film.py export-edit JOB_ID D:\ai\edit-exports\pilot-v1
```

将整个 `pilot-v1` 文件夹交给剪辑师。DaVinci Resolve 18.5 起支持从 **File → Import → Timeline** 打开 `timeline.otio`。所有媒体引用相对于这个文件，跨盘或跨系统后保留同一目录结构；如果剪辑软件要求重新链接，选择 `picture` 中带 `-picture` 后缀的文件和根目录中的 WAV。各软件对备用代理引用的支持不同，必要时手动指定对应的 `Sxx-Cxxx.mp4`。本项目的测试用官方 OTIO 0.18.1 读回并重写了导出的时间线。[Resolve 导入说明](https://documents.blackmagicdesign.com/SupportNotes/DaVinci_Resolve_18.5_New_Features_Guide.pdf) · [OTIO 数据格式](https://opentimelineio.readthedocs.io/en/latest/tutorials/otio-serialized-schema.html)

| 文件 | 用法 |
|---|---|
| `timeline.otio` | 一条画面轨、一条按镜切开的前景声音轨，使用配乐的任务另有连续的音乐轨。起始时码为零，全片 24 fps。 |
| `picture/*-picture.mp4` | 无损复制采用镜头的视频流，保留该任务的交付分辨率、定版与字幕。这些文件仅含画面，声音在独立轨上。 |
| `Sxx-Cxxx.mp4` | 1280×720、H.264、方形像素的代理，保留镜头原有声音，可供 FableCut 或代理编辑使用。片库号与所采用候选一一对应。 |
| `timeline.csv` | 按顺序列出镜号、片库号、代理名、时间线入点与独占出点。`0,24` 表示第 0 至 23 帧。 |
| `foreground.wav`、`music-bed.wav` | 双声道、48 kHz 浮点 PCM。前景含已采用的对白、旁白与音效。音乐轨已应用对白压低、剧情静音、结尾淡出和发行版总增益。两轨推子从 0 dB 起；长时间改动镜头顺序后可重新制作音乐自动化。 |
| `score.wav`、`audio-mix.json` | 使用配乐的新版任务会附带未压低的 ACE 源配乐；冻结的混音报告记录全片增益与区间，供重新混音。`score.wav` 用于替换已有配乐轨。 |
| `originals/` | 镜头有原始生成回执时随包复制原始媒体。它含源模型画面、原生声音及实际存在的尾帧余量。 |

## 可剪范围与分轨降级

每个画面剪辑的 OTIO `source_range` 从 0 帧开始，`duration` 对应该镜实际采用帧数。成片与代理的头尾余量均为 0。原始生成素材的实际帧数由 ffprobe 读取，原始路径和可用尾帧数记在画面剪辑的 `metadata.film_factory`。例如 97 帧生成素材裁成 96 帧交付时，原始素材有 1 帧尾余量。需要延展时从 `originals` 新建镜头，补做裁切、图文与声音。前景声音的每个镜头片段引用同一个全片长度 WAV，入点等于该镜在时间线的起点。

旧任务如果缺少已冻结的配乐或混音报告，导出会从该版 `film.mp4` 解码出 `programme.wav`，保留验收时的完整混音。打开时间线时可见 **Accepted programme mix** 轨。旧数据无法可靠逆推出独立的对白和配乐声部。

## 在工作站上的 FableCut 中使用

打开本机 FableCut，建立 1280×720、24 fps 项目，通过媒体导入添加根目录的 `Sxx-Cxxx.mp4`，按 `timeline.csv` 的顺序放置。需要复现验收版声音时，将代理内置声音静音，再把 `foreground.wav` 和 `music-bed.wav` 从起点叠加；旧版使用 `programme.wav`。整套操作使用 FableCut 已公开的媒体导入功能，当前工作站具体安装版的 OTIO 直读仍待实机验证。[FableCut 项目](https://github.com/ronak-create/FableCut)
