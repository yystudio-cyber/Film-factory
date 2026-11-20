# 架构、开源调研与验收边界

## GitHub 搜索范围

2026-11-20 按剧本到视频、AI 制片、LTX-2.3、ComfyUI API、生成视频评估、智能剪辑、配乐、故事板、时间线交换和本地语音识别十个方向检索。每个方向保留前 20 个返回结果，共 200 条、198 个去重仓库；原始查询与仓库名记录在 [github-search.json](github-search.json)。宽口径检索也返回了低相关工具。另按项目名检索、阅读关键仓库和官方接口示例。数字表示搜索命中范围，逐项采用判断仅覆盖下表；GitHub 全站动态变化，无法声称穷尽全部仓库。

| 项目 | 可借鉴或复用的能力 | 本项目的决策 |
|---|---|---|
| [HKUDS/ViMax](https://github.com/HKUDS/ViMax) | 剧本、角色、镜头、关键帧与视频的分阶段工作流 | 采用可检查的镜头产物、跨场角色约束；分镜与渲染分开持久化。 |
| [Lightricks/ComfyUI-LTXVideo](https://github.com/Lightricks/ComfyUI-LTXVideo) | LTX 节点与模型工作流 | 调用已经在 12 GB 卡上验证过的本地图，保留设备特有的采样与卸载设置。 |
| [Comfy-Org/workflow_templates](https://github.com/Comfy-Org/workflow_templates) | T2V、I2V、首尾帧模板与蓝图 | 确认三路结构。现场以 API 格式图为输入，显式标注动态节点。 |
| [Comfy-Org/ComfyUI](https://github.com/Comfy-Org/ComfyUI/blob/master/script_examples/websockets_api_example.py) | prompt 任务、history、view 结果寻址 | 按任务 ID 轮询指定输出；同台其他用户的生成结果不会被“最新文件”检索误领。 |
| [ace-step/ACE-Step-1.5](https://github.com/ace-step/ACE-Step-1.5/blob/main/docs/en/API.md) | 音乐任务提交、轮询与音频下载 | 使用本地 GPU0 的 REST 服务，单独保存配乐及任务号。 |
| [FFmpeg/FFmpeg](https://github.com/FFmpeg/FFmpeg) | 编解码、逐帧选取、音频处理与封装 | 实际调用 FFmpeg/ffprobe 做裁切、规范化、抽帧、混音和交付检查。 |
| [ronak-create/FableCut](https://github.com/ronak-create/FableCut) | 可编辑时间线、浏览器媒体库、项目接口 | 提供独立的 720p 代理与剪辑清单，供现有剪辑台接手；主流程可自行交付 MP4。 |
| [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) | CPU/int8 本地语音转录 | 有同期对白时检测缺句、明显错句；不会仅靠模型阅读提示词来推断音频合格。 |
| [Vchitect/VBench](https://github.com/Vchitect/VBench) | 主体一致性、运动等质量评估维度 | 借鉴分维度验收，运行时使用抽帧与 Qwen 评审。本系统的 1–5 分属于自己的审片规则。 |
| [Breakthrough/PySceneDetect](https://github.com/Breakthrough/PySceneDetect) | 场景切点检测 | 保留为进一步量化意外跳切的候选；当前以镜内抽帧及人工复核处理。 |
| [AcademySoftwareFoundation/OpenTimelineIO](https://github.com/AcademySoftwareFoundation/OpenTimelineIO) | 剪辑决定交换 | 借鉴源素材身份与时间线分离；当前输出 JSON 审计清单和代理 CSV。 |
| [harry0703/MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) | 短视频自动合成与音轨组织 | 比较素材拼装型流水线后，选择保留本地 LTX 镜头生成与逐镜验收。 |

## 工作站资源和调度

工作台通过 HTTP 连接推理进程。主控本身不加载 torch。在 8766 运行独立控制台，使既有 8765 项目能继续打开。ComfyUI 的视频任务串行提交，GPU1 使用已验证的低显存参数；ACE-Step 使用 GPU0。两块卡各有独立的 12 GB 容量。当前在镜头采用完毕后生成配乐，避免剧本变化造成重复长音频任务。

导演生成结构化节拍、动作、声音及角色场景约束。程序检查引用、字段、时长和工作流结构。摄制阶段逐镜保存模型实际收到的图与提示词。参考图的名称与同名说明文本用于选材，渲染使用原图；Qwen 的逐镜评审可见起止参考图和实际生成结果。

帧时间线独立于采样帧数。长度为 T 帧的剪辑至少生成 8 × ceil((T−1)/8) + 1 帧。6 秒在 24 fps 的交付时钟上是 144 帧，生成 145 帧，只剪入前 144 帧。衔接使用第 144 帧，先提取干净画面，再叠字和图形。制作目录保留原始模型文件。成片采用整数帧时间线，逐镜解码音频去除 AAC 编码器填充，再拼接并编码总音轨。

## 状态与失败语义

| 场景 | 系统动作 |
|---|---|
| 自动模式 | 每个候选必须通过每一维的阈值。请求多版本时，从通过者中采用总分最高者。 |
| 逐镜模式 | 进入 review，等待选中同镜可用候选；批量指令仅免去指定范围的手工点选。 |
| 修改插队 | 保存修改意见，重新生成目标镜；已采用的后续镜头一并重算，保留旧媒体和片号。 |
| 模型/API 故障 | 保留远程任务号、已有采用项和错误。提交请求发生不确定结果时不会盲目重复发送 POST。 |
| 画面或媒体不达标 | 保存具体退回原因与修复意见，换种子并重写提示词；默认每种候选最多 3 次。 |
| 总审不合格 | 保存组接视频、成片联系表、验收原因。能定位到镜号时有限次回修；仍未通过则显示待处理。 |
| 异常退出或主动停止 | 重启将运行和排队中的工作标为已取消；人工继续才会重新入队。候选号和采用关系在 SQLite 中持久保存。 |

指令和采用关系通过 SQLite 事务更新；同一任务目录只允许一个调度实例。成片只在技术与内容审核均通过后开放下载。每个项目保留版本、种子与评分，便于比较不同轮次。现场声音是否贴合动作、台词表演、跨镜细微变形仍可能躲过抽样检查；阈值需用自己的已通过片和被否片校准。

## 已执行的验证

Linux/Python 3.12、FFmpeg 7.1 环境完成本地 HTTP 集成测试。测试替身提供 Qwen、ComfyUI、ACE-Step 的协议结果，生成可解码的实际测试素材，再由工厂做完整后期。三镜成片为 320×180、24 fps、144 帧、6 秒、H.264/AAC；使用 320×192 输入检验裁切。测试覆盖逐镜验收、双候选、恢复与批量、画面退回、总审拒交、旧号保留。Windows、3060 的推理速度、真实模型画质和 SAPI 音色需要使用实机验收记录补齐。
