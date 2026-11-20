# Film Factory · 制片工厂

在 Windows 双 RTX 3060 工作站提交剧本、要求、素材和秒数，工厂拆镜、分派摄影与声音工作、调用已有的 LTX 工作流生成候选、按剧本审片，再混音并输出 **1920×1080、24 fps 的 H.264/AAC 母版**。可以全自动连跑，也可以保留原来的逐镜确认。网页绑定工作站本机，原 Studio Console 继续使用 8765，工厂使用 8766。

| 要办的事 | 从这里开始 |
|---|---|
| 接入现有 ComfyUI、模型 API、配乐与单阶段草稿图 | [工作流接入窗口](http://127.0.0.1:8766/setup) · [接入手册](docs/workstation.md) · [实机验收报告读法](docs/acceptance.md) |
| 从现有 Studio 导入「先导片」的剧本、图片、中文说明和镜头引用 | [旧项目导入与字段映射](docs/studio-import.md) |
| 提交一部影片、采用候选、指定重制或停止 | [制作控制台](http://127.0.0.1:8766/) |
| 一次提交多部，查看模型执行、GPU、磁盘、剧情遗漏和退回原因 | [生产监视器](http://127.0.0.1:8766/monitor) · [批量清单示例](examples/batch.json) |
| 在生成过程中按帧定位，连续看参考图、运动草稿、候选和已采用镜头 | [分镜放映室](http://127.0.0.1:8766/storyboard) · [放映室操作](docs/storyboard.md) |
| 将成片移交剪辑与声音后期 | [编辑包与可剪范围](docs/editor-export.md) · [母版声音与试听](docs/audio-master.md) |
| 了解企业先导片、产品片和叙事片怎样安排岗位与验收 | [真实制片业务与框架取舍](docs/final-research.md) · [工程与故障设计](docs/production-engineering.md) · [项目架构](docs/design.md) |

## 启动工作站

1. 将仓库克隆到 `D:\ai\Film-factory`。用原启动方式运行 ComfyUI（8188、GPU1）。需要配乐或执行整站验收时，启动 ACE-Step 的原生 REST 服务（8002、GPU0）。
2. 在项目目录打开 PowerShell。启动器使用已有的 `D:\ai\venv\Scripts\python.exe`，该环境应有 Python 3.12+、Pillow，且能调用 `ffmpeg` 和 `ffprobe`。首次启动会从示例生成 `config.local.json`；修改配置后重启。

```powershell
cd D:\ai\Film-factory
$env:FILM_LLM_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'Qwen API Key' -AsSecureString)).Password
$env:FILM_DS_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'DeepSeek API Key' -AsSecureString)).Password
$env:FILM_DS_BASE = Read-Host '现有 DeepSeek 网关的 OpenAI 兼容 API 基址'
$env:FILM_DS_MODEL = Read-Host '该网关实际提供的模型 ID'
.\start.cmd
```

3. 打开接入窗口，从成功历史或 ComfyUI 的 API JSON 分别绑定 T2V、I2V、FLF 三路正片图。节点号和推理参数取自现场工作流。需要低分辨率动态预演时，另接入对应的单阶段草稿图。需要输出同期对白时，准备 `faster-whisper` 与本地 ASR 模型。逐项操作见[工作站手册](docs/workstation.md)。
4. 用手册中的 `station-accept` 做真实模型、节点与媒体探测，再用 `--render` 提交六秒三路实机片。打开报告中的项目检查节奏、身份、剪点和混音。要沿用旧 C 编号，请先保留历史号段再生产。

示例把文本导演、场记、摄影与声音执行单交给工作站已有 DeepSeek 端点，Qwen 读取参考图与实际抽帧做视觉审片。Qwen 也可承担文本任务；`compare-directors` 可以用同一剧本对比两个端点的文本分镜。网关别名随服务商配置，需填写本账户的准确模型 ID。文本和审片图像会发送到对应的配置端点，原始生成视频与交付文件保存在工作站。

## 制作第一部影片

在制作控制台粘贴剧本，填**整数 2–600 秒**，在选填的「制片简报」指定目标观众、传播目标、播放渠道。制作要求写清必须保留的动作或台词、后叠文字和实际素材文件名。首页默认逐镜确认；选择「全自动成片」后，工厂自动采用通过各项门槛的候选。无人值守批量任务在生产监视器导入 TXT、Markdown 或 JSON，每批 1–50 部，单台工作站按队列逐部制作。

| 生产阶段 | 界面和结果 |
|---|---|
| 拆镜与分工 | 导演整理人物、地点和声音，场记将原句与制作要求分配到镜号。摄影和声音分别补充走位、机位、环境与声音承接。在放映室看逐镜的原文验收项、摄影与声音执行单。发现遗漏会先返工。 |
| 生成与审片 | 按首尾帧自动路由，采用上一镜剪辑点对应的干净尾帧。勾选动态草稿可先审一条低分辨率实际生成的片段。正式候选经过媒体检查、五时点视觉审片、邻镜剪点核对和适用的同期对白回听。登记的角色再次出场时，审片会对照其首次已采用镜头中可见的画面。 |
| 观看或改镜 | 分镜放映室区分计划、参考图、渲染草稿、候选与采用片。逐镜模式选中候选再采用；控制台可指定批量生成范围、要求 1–2 个版本、修改指定镜、停止与显式继续。修改采用镜会重新验收依赖的后续镜。 |
| 剪辑与混音 | 按整数帧裁切，正片从 1920×1088 裁到 1920×1080。后叠标题、字幕与指定透明 PNG，保留需要的现场声，按需加入 SAPI 旁白及 ACE-Step 配乐。全自动模式可让 GPU0 配乐与 GPU1 渲染同期执行。说话镜音轨驱动配乐避让，成片复测响度与真峰值。 |
| 交付 | 成片解码检查、实际剪辑联系表视觉审查、剧情逐条终审。通过后可在网页播放或下载 `film.mp4`、`timeline.json` 和 `acceptance.json`。剪辑师可通过 `export-edit` 取得 OTIO 时间线、正片画面、720p 代理及声音分轨；旧交付可回退到母版混音，详见[输出与操作](docs/workstation.md#输出与操作)。 |

如果本次目标是继续旧 Studio 的 `proj_3617a34c`，使用 `import-studio --inspect` 确认 JSON 字段，通过映射冻结选定剧本、图片与中文说明。可保留 `plan_6743a92b` 作参考，并显式指定 C001、C006 等首尾帧来源。`import-clips` 负责单独迁入已存在的片库编号。空工厂从 C001 分配，历史号段要包含已经删除的号。[旧 Studio 接入示例](docs/studio-import.md)

## 如何判断这版已经过哪一级验证

- **开发环境。** 仓库包含真实 FFmpeg 媒体加工和本地 HTTP 服务替身的集成测试。60 秒技术样片用十个测试镜头验证 1440 个帧时间戳、九个剪点前后画面与声音顺序。另有项目导入、帧时间线、音量避让、发布一致性和实机验收器的回归测试。运行下面的命令可检查当前检出的提交。
- **工作站。** `station-accept` 会发真实模型探测并与当地已安装的 ComfyUI 节点比对。`--render` 另外核对三路实际任务回执、配乐、审片与 144 帧母版。开发测试中的视频由测试素材构成；目标机 GPU 耗时、云端模型语言能力和真实镜头可用率以实机报告和样片为准。
- **客户或导演。** 技术通过可以确认封装、画幅、帧数等合同指标。品牌外观、自然表演、口型与整片节奏仍需用该类影片的人工结果校准。项目没有把某个自动审片分数换算成实测客户采用率。[验收口径与案例](docs/final-research.md)

```powershell
D:\ai\venv\Scripts\python.exe -m unittest discover -s tests -v
```
