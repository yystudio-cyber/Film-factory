# 工作站实机验收

换模型、升级节点或导入原 Studio 工作流后，使用同一份 config.local.json 启动工厂并运行验收。用 D 盘 AI 虚拟环境的 Python。三条正片图的绑定方法见[工作站接入](workstation.md)。模型 API 的环境变量要对运行工厂的进程和验收命令都可见。

## 检查服务与三条图

```powershell
D:\ai\venv\Scripts\python.exe film.py --config config.local.json station-accept --report D:\ai\film-factory\station-preflight.json
```

命令读取 ComfyUI 当前节点清单，再对 T2V、I2V、FLF 各自的实际配置逐条检查。服务检查会调用文本模型和视觉模型，按供应商规则计费。

| 报告位置 | 通过条件 |
|---|---|
| checks.media | 工作盘满足配置的剩余空间，字体可用，FFmpeg 成功编码和解码 H.264 与双声道 AAC，交付尺寸配置为 1920×1080 |
| checks.comfy / object_info | 服务报告 CUDA 设备并能提供节点清单。对照设备名核对启动的 ComfyUI 实例。设备索引采用该进程看到的编号 |
| checks.gpu_placement | 读取驱动的物理卡编号、容量及已用显存，在 Windows 核对监听服务 PID 与 GPU UUID。仅在能够唯一确认服务进程时判定错卡，子进程、代理、空闲或多卡上下文会标为待核对 |
| checks.ace | ACE-Step 的健康端点返回正常业务状态 |
| checks.text / vision | 当前文本模型正确复述随机校验值，视觉模型按照从左到右的顺序识别随机生成的两块颜色 |
| routes.T2V / I2V / FLF | 节点类与输出槽存在，必填字段齐全，静态枚举中的模型名可用，绑定字段可接收实际输入，输出节点可追溯到各项绑定 |
| routes.*.calibration | 沿正式输出的视频、latent 端口反查。可识别的单阶段图会失败；标准双阶段图核对两次真实采样之间是否经过空间放大、可读的 8+3 步及模型名所示 ×2；沿标准采样器/引导器的模型链核对可读的 CFG 1 和蒸馏 LoRA 强度 0.5。自定义封装无法展开时标 unverified，并保留已识别的路径 |
| routes.*.warnings | 自定义节点的未公开输入需要核对。模型权重能否实际加载、显存峰值和插件执行期行为均需通过下一步试片观察 |

图配置的 SHA-256 和核对失败的节点、端口保留在 JSON 中。驱动的显存已用量是检查时刻的快照；渲染峰值要在实际制作中另行观察。模型请求使用工厂的 OpenAI 兼容客户端；供应商别名以工作站上实际配置为准。

## 制作六秒验收短片

在另一个窗口启动本地 8766 工厂控制台。将两张场景有关联、适合衔接的 PNG 或 JPEG 放入资产目录，填写各自的同名说明文本。以下示例中的图片名应换成实际文件名。

```powershell
D:\ai\venv\Scripts\python.exe film.py --config config.local.json serve --port 8766
```

```powershell
D:\ai\venv\Scripts\python.exe film.py --config config.local.json station-accept --render --run-id first-ltx23 --first room-entry.png --last room-exit.png --port 8766 --wait-minutes 90 --report D:\ai\film-factory\station-render.json
```

验收先执行上述检查，随后通过正常批次入口提交一部自动制作的影片。队列内其他任务会影响等待时间。报告中的 monitor 地址可直接打开，控制台可查看导演计划、抽帧、返工原因和采用版本。

| 镜号 | 时长 | 实际交付依据 |
|---|---|---|
| S01 | 2 秒 | T2V 建立场景 |
| S02 | 2 秒 | I2V 使用指定的第一张图作为首帧 |
| S03 | 2 秒 | FLF 从已采用的 S02 尾帧衔接到第二张图 |

每镜请求 49 帧，每个交付镜头占 48 帧。验收核对采用候选的实际提交图、随机种子、提示词、回执和逐镜审片，通过后再检查 ACE-Step 配乐任务回执、文件、全片视觉审片和剧本逐项验收。最后重新解码母版，核对 144 帧、24 fps、6 秒、H.264 和 48 kHz 双声道 AAC。若导演安排的镜号、路线、首尾帧或时长与试片要求有出入，此轮验收失败；报告保留实际任务链接和原因。

| 退出码 | 状态与处理 |
|---|---|
| 0 | preflight_passed 表示基础检查通过；render_passed 表示本轮三路验收短片通过 |
| 1 | preflight_failed 或 failed；根据报告中的具体错误定位服务、节点、模型、任务或成片问题 |
| 2 | pending；监控达到等待时限，工厂任务可继续运行。沿用相同参数和 run-id 可再次查看；--wait-minutes 0 只轮询一次 |

报告的 qualification 会区分端到端成片通过情况与硬件、工作流配置的可验证程度。Windows 上在三次视频生成和配乐阶段查询监听进程的 GPU 上下文；短任务、驱动隐藏进程、代理和动态节点可能使部分项显示 unverified。profile_evidence 为 partial 时，逐项查看清单。进程上下文与显存快照反映采样时刻；它们不能提供单个算子的显存峰值。

报告默认写入配置数据目录的 station-acceptance 下。相同 run-id 会关联同一任务，命令重跑仍会重新检查当前服务。更换参考图内容、图配置或模型后，使用新的 run-id 保留对照记录。真实 GPU 推理、视觉 API 和配乐仅在目标站点通过后才能在报告中出现 render_passed。此试片覆盖三种路由与后期交付；长剧本中的人物一致性、语义遗漏及运动镜头仍由每次实际制作的逐镜和全片验收检查。

接口依据见 [ComfyUI OpenAPI](https://github.com/Comfy-Org/ComfyUI/blob/master/openapi.yaml) 、[Comfy 官方 LTX-2.3 工作流](https://comfy.org/hub/workflows/ltx-2-3-text-to-video-47d245c34a7d/) 与 [ACE-Step 服务文档](https://ace-step.github.io/ACE-Step-1.5/zh/API)。
