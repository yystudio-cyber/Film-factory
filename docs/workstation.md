# 工作站接入与首片验收

## 一次性接入现有工作流

打开本地 8766 端口的「工作流接入与自检」。自检检查 ffmpeg、ffprobe、Pillow、字体、环境变量、ComfyUI、ACE-Step 和三条绑定。未勾配乐的任务可在没有 ACE-Step 的情况下制作。密钥自检只检查是否设置；首次调用导演时会实际验证模型授权。

选「读取最近的 ComfyUI 任务」，从输出文件名识别**已跑成功且规格匹配的正片任务**。历史已清除时，在原 ComfyUI 打开那条工作流，用 API 格式导出，选择 JSON。GUI 布局图的 nodes/links 格式需要先经 ComfyUI 导出成 API 图。工厂复制图中的节点与参数，确认绑定后写入本地 workflows 目录。

| 工厂路由 | 本机图 | 必填绑定 |
|---|---|---|
| T2V | ECARX_T2V_LTX23 | 正向提示词、总帧数、随机种子 |
| I2V | ECARX_I2V_LTX23 | 同上，加首帧图像 |
| FLF | LTX_FLF2V | 同上，加首帧和尾帧图像 |

编辑器显示所有字面量，链接输入需要沿连线定位上游常量。复核建议值，尤其是正负提示词与两个 LoadImage 节点。最终输出选择正片的 SaveVideo 或 VideoCombine，节点结果应只包含一个视频文件。一个角色可以绑定多个字段，例如音频 latent 与视频 latent 共用帧数或两阶段共享种子。图中计算出来的帧数、尺寸保持原连线，只绑定其源参数。若图的时长仅按秒控制，可以将秒数和帧数的关系在 ComfyUI 中接成明确的总帧数输入，再导出 API 图。

默认生成画幅为 1920×1088，输出时间线为 1920×1080、24 fps。两阶段图中按实际节点绑定全尺寸或半尺寸；已经固定的画幅可保留原值。沿用 RTX 3060 上实测的权重、CFG 1、蒸馏 LoRA 0.5、低分 8 步和精修 3 步配置。第一次绑定请核对这些常量及音频是否接入视频保存节点。草稿图可用于另一套独立配置，按其真实输出画幅设置 width/height。

命令行也可绑定。下面的节点号**仅演示映射文件格式**，须用本地 inspect 的结果替换。

```json
{"bindings":{"prompt":["12.text"],"negative":["14.text"],"frames":["19.length"],"seed":["23.noise_seed"],"first":["31.image"]},"output":"45"}
```

```powershell
D:\ai\venv\Scripts\python.exe film.py inspect D:\graphs\i2v-api.json
D:\ai\venv\Scripts\python.exe film.py bind I2V D:\graphs\i2v-api.json D:\graphs\i2v-map.json
D:\ai\venv\Scripts\python.exe film.py doctor
```

## 原 Studio 项目与片库

工厂通过 ComfyUI 和 ACE-Step 的服务接口复用渲染与配乐。原 Studio 的项目文件、审批队列与计划号仍由原控制台管理。需要复用该「先导片」时，填入原剧本及此次制作要求，将原素材整理成 PNG/JPEG + 同名说明文本，或把 assets 指向已有同结构目录。调研环境没有该机的 22 张原图、实际图 JSON 或历史片库文件。

保留历史片号时，请按真实文件路径建立 `studio-clips.json`。last_issued 填原计数器曾分配的最大编号；示例仅表示 C001/C006 两条素材被复制且 C001–C009 已保留。未导入的旧素材需在引用前补导，已占用的编号不会被覆盖。

```json
{"last_issued":9,"clips":{"C001":"D:/原片库/S01.mp4","C006":"D:/原片库/隧道2秒.mp4"}}
```

```powershell
D:\ai\venv\Scripts\python.exe film.py import-clips D:\ai\studio-clips.json
```

如旧素材暂未定位，可先用空 clips 和真实 last_issued 保留号段。建立独立工厂时可跳过导入。工厂复制被明确选中的旧片，并从实际最后一帧生成参考图。

## 声音

ACE-Step 需要支持 `POST /release_task`、`POST /query_result`、`GET /v1/audio` 的服务。若 8002 仅提供 Gradio 网页，配置 ace 指向实际运行原生 API 的地址。工厂轮询任务号并验证下载的音乐达到目标时长。

同期对白写入 LTX 的 Sound 段。需要对白时，可在工厂 Python 环境安装 `faster-whisper`，将 config.local.json 的 `asr_model` 指向已准备好的本地 CTranslate2 格式 Whisper 模型目录。回听使用 CPU/int8，默认转录近似相似度门槛 0.8，可通过 `speech_similarity` 调整。Windows 旁白使用本机 SAPI；中文旁白需要可供 System.Speech 使用的中文语音，也可用环境变量 `FILM_VOICE` 指定确切语音名。旁白过长时会显示异常，保留全文供修改。标题使用配置的中文字体；卡通气泡等图形作为同画幅透明 PNG 提供。

## 用一个短镜头验收真实工作站

1. 先使用一张已描述的真实工位图。目标设为 4 秒，逐镜模式，关闭配乐。剧本可以写「夜班工位，测试人员伸手调低台灯，镜头缓慢前移，保留房间底噪」。要求中填入参考图的完整文件名，指定单镜、无台词。
2. 核对分镜、生成的英文提示词、ComfyUI 输出、GPU1 显存与耗时。验收图生路由应含首帧，静态相机约束应与运镜一致。点开候选检查真实动作、色彩、声音与观感，再点击采用。
3. 生成 6 秒双镜小样，在第二镜注明承接上一镜、启用配乐。查看尾帧、Qwen 审片记录、ACE 任务和最终验收 JSON。确认 144 帧、24 fps、H.264/AAC、1920×1080，试听切点、现场声和音乐比例。
4. 接入第三条路由时，给出首尾两张图并指定使用；验证 FLF 的两个图像节点没有接反。通过后再提交 60 秒先导片。质量阈值是可复核的机器判据，品牌调性、表演自然度以及微小口型差异仍适合由制作人抽检。

## 输出与操作

运行目录为 `data/<job_id>/`。brief.json 记录输入，plan.json 记录原分镜，takes 下有各次尝试。采用项、状态与命令队列记录在 SQLite。浏览器中的成片和报告只在 done 状态开放。若退出或远程提交响应丢失，先看该镜的 remote.json、ComfyUI 历史与队列，然后显式继续。服务中断时已到远端但尚未确认的同一镜可能需要单独核对。

网页支持逐镜通过、成片下载、指定镜号修改、1–2 个候选、停止、继续，以及从当前待采用镜头批量生成到指定镜号。修改意见在当前生成任务的安全边界执行。更换已采用镜头后，后续镜头与尾帧依赖重新验收。

将完成的影片交给现有 FableCut 时，用下列命令生成代理目录，然后在 7777 剪辑台中按镜号导入。CSV 提供每剪从 0 起算的入点与排他出点；配乐另放音轨并按需调音。

```powershell
D:\ai\venv\Scripts\python.exe film.py export-edit <job_id> D:\ai\edit-exports\pilot-v1
```

## 常见停机原因

| 现象 | 定位入口 |
|---|---|
| 分镜时长分配错误、模型鉴权失败 | 查看制作记录与 Qwen 配置。明确目标秒数、对白和必留内容。 |
| 收到队列 ID 后等不到结果 | 按 remote.json 的 prompt_id 查看 ComfyUI 历史与显存；工厂渲染等待上限为一小时。 |
| 全部候选画幅、帧数或音轨相同地不合格 | 核对工作流动态输入、声音输出、项目尺寸和最终保存节点。 |
| Qwen 一直否决画面 | 查看 rejected.json、五张抽帧及 review.json 的具体评分，优先修改镜头动作或更换参考图。 |
| 成片存在于制作目录但页面仍显示失败 | candidate.mp4 是待验收版本。acceptance.json 记录总审失败理由，只有通过门槛才发布 film.mp4。 |
