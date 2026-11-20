# 工作站接入与首片验收

## 升级已有项目

更新仓库代码，保留 config.local.json、workflows、assets 和 runs。旧配置可继续使用 Qwen 单模型。要启用分工，在原配置中加入 text_provider 为 deepseek、ds_base 为现有服务的 OpenAI 兼容基址、ds_model 为该服务的准确模型 ID，并设置 FILM_DS_KEY；或在没有 text_provider 字段的旧配置中设置 FILM_DS_BASE、FILM_DS_MODEL 和 FILM_DS_KEY。FILM_LLM_KEY 继续用于 Qwen。独立密钥不会互相代用。首次运行自动增建批次和执行记录表。既有成片可继续播放；旧任务在继续生产时补做剧情核对，无历史执行记录的阶段不会凭空补写。

打开 /setup 检查两个逻辑角色显示的 model、base 和 key_set。只有实际生成请求成功才证明模型可用。Windows 启动命令见仓库首页。需要比较导演质量时，用已提交项目的 brief.json 执行以下命令，输出文件指定新的路径；测试会消耗两个文本 API 的额度。

```powershell
D:\ai\venv\Scripts\python.exe film.py compare-directors D:\ai\film-factory\runs\项目ID\brief.json D:\ai\director-comparison.json
```

两个模型各出一份分镜，两位评审分别对两份匿名分镜做原文覆盖检查。报告保留模型名、分镜、耗时、合规情况、遗漏理由及错误。可用已知难例重复几次，再决定哪种路由更适合自己的素材。比较只使用文本和素材描述。

## 一次性接入现有工作流

打开本地 8766 端口的「工作流接入与自检」。自检检查 ffmpeg、ffprobe、Pillow、字体、环境变量、ComfyUI、ACE-Step 和三条绑定。未勾配乐的任务可在没有 ACE-Step 的情况下制作。密钥自检只检查是否设置；首次调用导演时会实际验证模型授权。

选「读取最近的 ComfyUI 任务」，从输出文件名识别**已跑成功且规格匹配的正片任务**。历史已清除时，在原 ComfyUI 打开那条工作流，用 API 格式导出，选择 JSON。GUI 布局图的 nodes/links 格式需要先经 ComfyUI 导出成 API 图。工厂复制图中的节点与参数，确认绑定后写入本地 workflows 目录。

| 工厂路由 | 本机图 | 必填绑定 |
|---|---|---|
| T2V | ECARX_T2V_LTX23 | 正向提示词、总帧数、随机种子 |
| I2V | ECARX_I2V_LTX23 | 同上，加首帧图像 |
| FLF | LTX_FLF2V | 同上，加首帧和尾帧图像 |

编辑器显示所有字面量，链接输入需要沿连线定位上游常量。复核建议值，尤其是正负提示词与两个 LoadImage 节点。最终输出选择正片的 SaveVideo 或 VideoCombine，节点结果应只包含一个视频文件。一个角色可以绑定多个字段，例如音频 latent 与视频 latent 共用帧数或两阶段共享种子。图中计算出来的帧数、尺寸保持原连线，只绑定其源参数。若图的时长仅按秒控制，可以将秒数和帧数的关系在 ComfyUI 中接成明确的总帧数输入，再导出 API 图。

默认生成画幅为 1920×1088，输出时间线为 1920×1080、24 fps。两阶段图中按实际节点绑定全尺寸或半尺寸；已经固定的画幅可保留原值。沿用 RTX 3060 上实测的权重、CFG 1、蒸馏 LoRA 0.5、低分 8 步和精修 3 步配置。第一次绑定请核对这些常量及音频是否接入视频保存节点。

**可选的动态草稿。** 首页的「正片前生成单阶段渲染草稿」会实际消耗 GPU 时间；默认关闭。选中后，计划用到的每条路由都需要独立的单阶段 API 图，保存为 `workflows/draft/T2V.json`、`I2V.json` 或 `FLF.json`，文件结构与相应正片绑定相同。1080p 配置的草稿交付预览为 960×540，32 对齐的原始画布为 960×544。在接入页选择对应档位，验证单阶段图确实省略了放大、精修并按草稿尺寸出片。动态草稿先接受构图和动作审查，正式候选仍要通过自己的门禁。

命令行也可绑定。下面的节点号**仅演示映射文件格式**，须用本地 inspect 的结果替换。

```json
{"bindings":{"prompt":["12.text"],"negative":["14.text"],"frames":["19.length"],"seed":["23.noise_seed"],"first":["31.image"]},"output":"45"}
```

```powershell
D:\ai\venv\Scripts\python.exe film.py inspect D:\graphs\i2v-api.json
D:\ai\venv\Scripts\python.exe film.py bind I2V D:\graphs\i2v-api.json D:\graphs\i2v-map.json
D:\ai\venv\Scripts\python.exe film.py bind I2V D:\graphs\i2v-draft-api.json D:\graphs\i2v-draft-map.json --draft
D:\ai\venv\Scripts\python.exe film.py doctor
```

## 原 Studio 项目与片库

普通新项目从控制台粘贴剧本、选用素材库图片即可。原 Studio 的「先导片」可用 `import-studio` 按项目读取剧本、图片与中文说明，建立冻结快照，显式映射首尾帧和已知 C 号，可保留历史计划副本。先核实项目的实际 JSON 字段、22 张图片和片库编号，再将该快照入队。旧计划的已拍、被否状态保留在历史副本，新工厂使用独立的生产与验收队列。[导入实例与映射](studio-import.md)

保留历史片号时，请按真实文件路径建立 `studio-clips.json`。last_issued 填原计数器曾分配的最大编号；示例仅表示 C001/C006 两条素材被复制且 C001–C009 已保留。未导入的旧素材需在引用前补导，已占用的编号不会被覆盖。

```json
{"last_issued":9,"clips":{"C001":"D:/原片库/S01.mp4","C006":"D:/原片库/隧道2秒.mp4"}}
```

```powershell
D:\ai\venv\Scripts\python.exe film.py import-clips D:\ai\studio-clips.json
```

如旧素材暂未定位，可先用空 clips 和真实 last_issued 保留号段。建立独立工厂时可跳过导入。工厂复制被明确选中的旧片，并从实际最后一帧生成参考图。

## 声音

ACE-Step 需要支持 `POST /release_task`、`POST /query_result`、`GET /v1/audio` 的服务。若 8002 仅提供 Gradio 网页，配置 ace 指向实际运行原生 API 的地址。工厂轮询任务号并验证下载的音乐达到目标时长。全自动模式勾选配乐且有需要声音的镜头时，默认允许 GPU0 配乐与 GPU1 渲染重叠。在 `config.local.json` 设置 `"parallel_score": false` 可改为后置；逐镜确认模式在组片前生成。

同期对白写入 LTX 的 Sound 段。需要对白时，可在工厂 Python 环境安装 `faster-whisper`，将 config.local.json 的 `asr_model` 指向已准备好的本地 CTranslate2 格式 Whisper 模型目录。回听使用 CPU/int8，默认转录近似相似度门槛 0.8，可通过 `speech_similarity` 调整。Windows 旁白使用本机 SAPI；中文旁白需要可供 System.Speech 使用的中文语音，也可用环境变量 `FILM_VOICE` 指定确切语音名或 `FILM_VOICE_CULTURE` 指定语言。已选 SAPI 语音与 culture 会记录在候选目录。旁白过长时会显示异常，保留全文供修改。标题使用配置的中文字体；卡通气泡等图形作为同画幅透明 PNG 提供。

成片混音以 48 kHz 双声道进行。说话镜的实际音频驱动配乐避让，显式静音且无人声的镜头同时静音现场声和配乐。配置项 `master_target_lufs` 默认 −18 LUFS，`master_peak_dbtp` 默认 −1 dBTP。系统给安静节目和瞬态留余量，响度不一定等于目标值。验收报告的 `audio_mix` 保存实际避让区间、处理前测量、增益决定和最终 AAC 的整段响度、真峰值。

## 六秒实机验收

在控制台处于运行状态时另开一个 PowerShell，进入项目目录，使用同样的模型、环境变量和配置。若新终端未继承 API 密钥，可用启动控制台时的 PowerShell 再开子终端，或在新终端重新设置。

```powershell
cd D:\ai\Film-factory
$py = 'D:\ai\venv\Scripts\python.exe'
& $py film.py station-accept --report D:\ai\station-preflight.json
```

这一步会调用实际文本模型与视觉端点，检查 ComfyUI 报告的 CUDA 设备、当前 `/object_info`、三个正片路由绑定、ACE-Step 健康状态，以及本地字体和真实 H.264/AAC 编解码。它要求 1920×1080 的交付设置。报告显示 `preflight_passed` 表示现场服务和图通过探测。仍需核对 GPU 映射和实际成片；没有配乐的日常作业允许关闭 ACE-Step，整站验收会探测它。

将两张**不同**且存在于配置素材目录顶层的 PNG/JPEG 文件名代入以下命令。样片需要真实 API 额度、GPU1 和 ACE-Step。

```powershell
& $py film.py station-accept --render --first '办公室首图.png' --last '办公室尾图.png' --run-id first-station-v1 --wait-minutes 90 --report D:\ai\station-render.json
```

命令通过正常生产队列生成三条两秒镜，依次文生、指定首图、承接上一镜到指定尾图，再混入真实配乐。验收器对照当前工作流和远程任务回执，检查每条 LTX 图的 49 帧采样、剪入的 48 帧、六秒母版的 144 帧、输出尺寸、声音和终审记录。报告会给出任务 ID 和监视器地址。`pending` 与退出码 2 表示等待期限届满、任务可以继续运行。重复相同命令复用同一 run-id 跟踪，`--wait-minutes 0` 可仅看一次；更改模型、图或参考图后改用新的 run-id。退出码 1 或任务暂停时，以报告的错误与任务事件定位原因。只有 `render_passed` 表示本次实机链完整通过。[逐项报告与故障解释](acceptance.md)

然后用自有素材做一条有运镜、品牌文字或对白的业务样片，在放映室复核真实动作、切点、声音、定版，记录人审意见。三路技术样片没有覆盖这些特定内容和声音表演。

## 输出与操作

运行目录为 `data/<job_id>/`。`brief.json` 记录委托，`plan.json` 包含获准的分镜、剧情覆盖和部门执行单；`departments/` 缓存摄影与声音的实际输出，`takes/` 保存各候选，启用动态草稿时 `drafts/` 保存草稿及审片。采用项、状态、执行阶段及修改命令由 SQLite 记录。成功交付时，用唯一版本目录保存彼此匹配的母版、时间线与验收报告，浏览器在 done 状态开放下载。若退出或远程提交响应丢失，先看该镜的 remote.json、ComfyUI 历史与队列，然后显式继续。服务中断时已到远端但尚未确认的同一镜可能需要单独核对。

在 `/storyboard` 选择影片，可按帧定位或连续播放当前时间线。从「优先已采用片段」「优先渲染草稿」「仅分镜与参考图」切换预览来源，标签说明当前画面是否来自真实渲染。只有已生成并符合当前门禁的候选可采用。生产监视器用于查看阶段与证据、导入批量剧本；制作控制台支持逐镜通过、成片下载、指定镜号修改、1–2 个候选、停止、继续，以及从当前待采用镜头批量生成到指定镜号。修改意见在当前生成任务的安全边界执行。更换已采用镜头后，后续镜头与尾帧依赖重新验收。

用下面的命令导出已交付的一个版本。选择任务目录以外、尚不存在的输出目录；整包移动时保留目录结构。

```powershell
$job = Read-Host '已交付的任务 ID'
D:\ai\venv\Scripts\python.exe film.py export-edit $job D:\ai\edit-exports\pilot-v1
```

| 包内内容 | 剪辑用途 |
|---|---|
| `timeline.otio`、`picture/` | OpenTimelineIO 时间线引用每镜经过后期的正片画面，保留片号、精确源区间和时间线位置。默认画面不带音频，声音由独立轨道播放。文字与图形已烘焙在正片画面中。 |
| 根目录的 `Sxx-Cxxx.mp4`、`timeline.csv` | 1280×720 带声代理供 FableCut 按镜号手动导入。CSV 内的入出点位于整条序列，结束帧为排他出点。 |
| `foreground.wav`、可选的 `music-bed.wav`、`audio-mix.json` | 带混音记录的新交付可以重建前景与已避让的音乐两轨；原配乐可另存为 `score.wav`。使用分轨时关闭代理自带声音，避免重复播放。 |
| `programme.wav` | 旧交付缺少分轨所需记录时，从已采用母版解码出整条实际混音，保留当时的声音。 |
| 可选 `originals/` | 有源文件回执的镜头附上原始生成结果；剪辑描述中记录实际原片可用帧数和余量。 |

支持 OTIO 的接收端可加载 `timeline.otio` 并重连包内相对路径。现有 FableCut 7777 可按原有导入方式使用 720p 代理。目标机剪辑台的软件版本和宿主导入能力需要在现场验证，分别核对第一镜、剪点两侧、末帧与声音。[交接包字段与回程核验](editor-export.md) · [声音处理口径](audio-master.md)

## 常见停机原因

| 现象 | 定位入口 |
|---|---|
| 分镜时长分配错误、模型鉴权失败 | 查看制作记录中实际失败的工序，以及该工序所用 Qwen 或 DeepSeek 端点。明确目标秒数、对白和必留内容。 |
| 收到队列 ID 后等不到结果 | 按 remote.json 的 prompt_id 查看 ComfyUI 历史与显存；工厂渲染等待上限为一小时。 |
| 全部候选画幅、帧数或音轨相同地不合格 | 核对工作流动态输入、声音输出、项目尺寸和最终保存节点。 |
| Qwen 一直否决画面 | 查看 rejected.json、五张抽帧及 review.json 的具体评分，优先修改镜头动作或更换参考图。 |
| 成片存在于制作目录但页面仍显示失败 | candidate.mp4 是待验收版本。acceptance.json 记录总审失败理由，只有通过门槛才发布 film.mp4。 |

监视器按动作草稿、正片、配乐分别显示已完成调用次数和累计服务耗时，包含共享队列等待及传输。并行通道的时长可重叠。同样的数据随 `acceptance.json` 交付。逐字修改可写「把标题从“杭州夜班”改为“杭州晨班”」；修改景别、调色或自动返工会保留已有对白和定版字稿要求。
