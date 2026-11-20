# 从实际制片业务到双 3060 工作站

本文记录项目选型、岗位交接和实机验收依据。企业委托片与叙事短片各有不同的失败条件。输出分辨率、总帧数与声音电平可测；剧本含义、表演、节奏和品牌准确性需要把原文、参考素材、实际成片放在一起判断。公开论文或其他工作室的效率数据不能代替这台工作站的出片记录。更早一轮的 LTX、ComfyUI、ACE-Step、模型分工和社区故障分析见[制片工程与故障设计](production-engineering.md)。

## 哪些委托会进这间工厂

| 委托 | 工作中最昂贵的错误 | 开拍前冻结 | 看片时拿什么验收 |
|---|---|---|---|
| 企业活动开场片、跨地域品牌先导片 | 项目按时交付，画面却把两个城市、产线或人物身份拍反；活动定版文字有错 | 受众、播放场合、精确片长、城市／岗位／资产对应、品牌批准的字稿、最后一帧 | 对着逐句原稿标记真实时间段；逐一比对地名、人名、型号、日期、定版与素材；在目标屏幕上播放交付文件 |
| 品牌故事、产品演示 | 画面漂亮，但产品外观或操作顺序失真；生成的界面假装是真实产品 | 产品多角度参考、展示顺序、必留特写、已批准的界面和产品文案 | 对照参考检查外形、控件与动作；用已有录屏、图层或定版呈现须精确复现的画面；把品牌判断记到对应版本 |
| 带人物的叙事短片 | 单镜评分高，连起来却换人、跳轴、拿错道具；正确台词出自错误角色 | 角色造型、场景与故事时间、道具起止状态、人物左右、台词归属、剪辑动机 | 比对邻镜入／出状态与原始角色参考；听一遍对白与环境转场；看动作实际发生和人物反应；刻意时空跳跃可有明确硬切 |
| 内部培训、采访式旁白 | 把总结当作被采访者原话，或者多个数字、缩写、产品名发音相似 | 已批准的逐字稿、术语读音、原录音、哪些声音必须来自画面人物 | 比对白、说话人、字幕与已批准原稿；仅有素材照片时明确可实现的表达方式；口型与说话人身份需要另行验收 |
| 系列化批量制作 | 共享模板的某次改字或换素材污染已交付影片；横竖版裁切切掉关键物件 | 每部剧本、资产版本、交付规格、系列通用字稿与本片例外 | 逐片留存采用的版本、时长和终审结果；新画幅或新语种按新交付版本复核，不能从横版合格自动推导 |

这些委托来自两类实际工作。VMware 的活动制作团队需要同时服务多个内部委托方和外部供应商，处理视频、动画、数字标牌等交付物。YETI 的品牌片生产涉及多个地区的合作方、频繁审片和多个输出版本。这些是供应商公开的客户案例，可用来识别版本、反馈与交接需求；其中的产能不适宜照搬成 GPU 预算。[VMware 案例](https://frame.io/case-studies/vmware) · [YETI 案例](https://business.adobe.com/customer-success-stories/yeti-case-study.html)

英国 ScreenSkills 的场记职责包括剧本拆解、估算片长、监测覆盖并记录演员动作、服装、道具及故事时间。Frame.io 的后期指南记载剪辑在拍摄期间用样片持续粗剪，及早发现缺镜。ScreenSkills 的在线剪辑助理规范把套底、混音后合成、母版技术质检放在不同环节。这支持两个独立决策：早期检查“这场戏有没有拍到”，交付前检查“观众实际收到的文件能不能讲清楚”。[场记工作清单](https://www.screenskills.com/skills-checklists/scripted-film-and-tv/script-supervisor-department/script-supervisor-skills/) · [连续性拆解](https://www.screenskills.com/job-profiles/browse/film-and-tv-drama/technical/script-supervisor-film-and-tv-drama/) · [粗剪与样片](https://workflow.frame.io/guide/editing-stages) · [在线剪辑与母版](https://www.screenskills.com/skills-checklists/unscripted-tv/post-production-department/online-assistant-skills/)

## 从岗位交接划出并行边界

| 岗位与交付 | 输入依赖 | 用本机做决定 |
|---|---|---|
| 制片与场记交付剧本分解、素材清单和覆盖表 | 原文、受众、时长、制作要求与实际存在的素材 | 给源文和每镜绑定编号。区分可见动作、同期说话、旁白、字幕、定版、声音和剪辑。缺必拍项先修分镜。 |
| 导演、摄影与美术交付逐镜执行单 | 已确认的剧情、镜长、前后镜、人物和地点 | 导演锁定叙事；摄影给出景别、走位、轴线、布光与切点。静止画面只验证主体与构图；真实运动草稿另验动作方向与时序。 |
| 声音编辑交付声音提示和承接点 | 同一版镜头及逐字台词 | 可与摄影并行；标明声音从何时起、在何处停、有没有提前进入下一景。实际输出另验台词可懂度、音量与声音透视。 |
| 现场与样片审查交付候选、剪辑点和退片原因 | 当前镜的全部必需道具、参考、最终提示词，必要时还依赖上一镜已采用尾帧 | GPU1 一次一条重型视频任务。草稿能淘汰错误动作；正式镜的清晰度和连续性还需看正式输出。退片应携带可执行的改动。 |
| 剪辑、混音和交付交付锁定时间线及可播放母版 | 实际采用的片段、对白／旁白、环境、配乐、图层 | 在整数帧时间线上合片，记录媒体源与剪辑位置。测最终解码结果，听最终混音；修改顺序后重新对齐声音、标题和总片长。 |

本文中“并行”有明确前提。摄影与声音部门各自读取同一版镜号、时长、剧本，产出独立结构化提案，制片合并并检查每镜字段。用两个有界 API 请求可以缩短等待；在两个端点争用同一供应商配额或一路异常时，真实收益可能很小。模型无权生成文件路径或选择显卡执行命令。ComfyUI、SQLite 与媒体命令仍由确定性程序控制。连续接尾帧的镜号有数据依赖，下一镜须等待采用结果。即使 GPU0 运行配乐与 GPU1 渲染重叠，仍共享主机 RAM、磁盘和热预算，耗时应在真实工作站记录。[LangGraph 工作流与独立任务并行](https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/workflows-agents.mdx) · [MM-StoryAgent 的实际调度实现](https://github.com/X-PLUG/MM_StoryAgent/blob/main/mm_story_agent/mm_story_agent.py)

以《先导片》的两秒隧道为例，C006 的爆闪是切往杭州研发办公室的候选切点。制片先从原稿确认是否要求保留白闪、杭州身份是否依赖特定素材。摄影检查上一镜出口与办公室建立镜能否读懂；声音编排隧道掠过、耳鸣、室内底噪的先后。正式总审需要回看剪点两侧实际画面并听实际声音。仅凭声音提示词、某一帧是白色、ASR 转录正确或两个镜头来自同一模型，均无法判定衔接成功。电影声效实践强调环境底可掩盖剪点和维持空间感，Netflix 的配音指南也把人物位置、移动和空间听感纳入混音判断。[FilmSound 关于环境底的说明](https://www.filmsound.org/terminology/roomtone.htm) · [Netflix 配音混音指南](https://partnerhelp.netflixstudios.com/hc/en-us/articles/360017944573-Netflix-Mixing-Style-Guide-For-Dubbed-Content)

## 对照公开实现和社区故障

| 阅读的代码、说明或故障记录 | 对本机的技术结论与取舍 |
|---|---|
| [Wonder Unit Storyboarder 的分镜时长代码](https://github.com/wonderunit/storyboarder/blob/master/src/js/models/board.js) · [Windows 导出故障报告](https://github.com/wonderunit/storyboarder/issues/2575) | 上游用镜头和附属声音的时长推导预演长度，说明读稿与分镜要在同一时间线对照。厂内有精确总片长时应在实拍前解决超时台词。Windows 用户报告 FFmpeg 已安装仍因调用路径导致导出失败，因此自检应跑实际编码。 |
| [MM-StoryAgent 调度器](https://github.com/X-PLUG/MM_StoryAgent/blob/main/mm_story_agent/mm_story_agent.py) · [项目示例](https://github.com/X-PLUG/MM_StoryAgent) | 工具把故事、模态资产和最终合成分开，模态可并行；其媒体和算力配置与 LTX 的顺序连续镜头不同。借用部门产物约定，避免在 12 GB 显存上同时放多个重型本地模型。 |
| [OpenTimelineIO 时间范围](https://github.com/AcademySoftwareFoundation/OpenTimelineIO/blob/main/docs/tutorials/time-ranges.md) · [FCP7 XML 适配器源代码](https://github.com/OpenTimelineIO/otio-fcp-adapter/blob/main/src/otio_fcp_adapter/fcp_xml.py) · [FCPX XML 独立适配器](https://github.com/OpenTimelineIO/otio-fcpx-xml-adapter) | 编辑交接需要真实媒体、轨道类型、帧率和源时间。应区别母片源区间和序列区间，约定入点含、出点不含。FCP7 的 xmeml 与 FCPXML 使用不同格式；接收方、格式版本和实际往返测试要写清，单个 XML 文件无法包装媒体。 |
| [Creative COW 中的声音套底问题](https://creativecow.net/forums/thread/aaf-from-premiere-2020-problem/) · [另一例导出时间码偏移](https://creativecow.net/forums/thread/timecode-is-off-in-exported-aaf/) | 剪辑用户报告项目可打开但音频对应到错误源时间。校验交换文件时至少核对第一镜、非零素材入点、两个相邻剪点、末帧与音频长度。交换格式默认不继承某个宿主中的全部插件、效果、字型和重混行为。 |
| [MediaArea QCTools 的图表与读法](https://github.com/MediaArea/QCTools/blob/master/docs/getting_started.md) · [Netflix VMAF 的 FFmpeg 用法](https://github.com/Netflix/vmaf/blob/master/resource/doc/ffmpeg.md) | 指标最好能定位到时间，方便回看片因。档案视频噪点检测不能替代生成视频的变脸检测。VMAF 要有时间对齐的已知参考，适合排查转码损失；脚本、风格图和单张首帧不能提供这个参考。 |
| [FFmpeg 的 loudnorm 两遍测量示例](https://github.com/FFmpeg/FFmpeg/blob/master/tools/loudnorm.rb) · [Adobe 剪辑社区的自动避让反馈](https://community.adobe.com/announcements-732/available-now-in-27-0-auto-update-keyframes-for-audio-ducking-in-premiere-1625227) | 测量、按测量值处理、复核最终编码结果是不同证据。调整台词、换镜和配乐后都可能需要重做避让。接单时使用项目适配的响度目标；达标响度也可能伴随遮蔽对白或不合适的环境声。 |
| [WhisperX 的对齐与已知限制](https://github.com/m-bain/whisperX) · [SyncNet 的人脸跟踪实现](https://github.com/joonson/syncnet_python/blob/master/run_pipeline.py) | ASR、说话人分离与口型同步是三个任务。前者会遇到数字、缩写、重叠说话；后一项目依赖可跟踪的脸和足够多的有效帧。2 秒插入镜、画外音、背影须分别处理。两者都要额外模型预算，本调研未在双 3060 上测得准确率。 |
| [MovieBench 的分层评估](https://weijiawu.github.io/MovieBench/) · [W3C 的视听媒体制作策划](https://www.w3.org/WAI/media/av/planning/) | 电影级评估涉及跨场景叙事、角色与多镜组织；按帧打分无法替代整片审查。需要无障碍交付时应在委托阶段明确字幕、音述、文字稿和播放渠道，才能留出时间与混音空间。 |

## 工作站验收与可证实的完成条件

| 指标 | 记录方法 | 缺少什么时不能下结论 |
|---|---|---|
| 剧本覆盖与错误放行 | 逐条记录原文、镜号、实际证据位置、自动意见与人工结论；按文案、动作、身份、声音、转场分组统计 | 只检查拆镜表会漏掉“计划写了、成片没拍到”；无人标记的样本不能估计真实误放率 |
| 样片价值 | 记录每个草稿被退回的原因、正式镜重制次数、总 GPU 分钟；对同类镜长和路线比较 | 草稿本身也耗时，不能因启用草稿就承诺吞吐更高 |
| 并行收益 | 保存各部门请求起止、限流或重试、GPU 等待和实际出片时长；报告样本数、P50 与 P90 | 共享供应商接口的并发速度、两卡负载和 Windows 驱动都需实测 |
| 批量合格率 | 每批统计提交、完成、卡在门禁、取消和人工采用数；分母包括退回与取消，另报告人工看片样本数 | 把未通过的作业从分母移走会高估产能 |
| 可交付分钟成本 | 总生成、局部重做、合成与终审耗时分别计数，再除以实审通过的成片分钟；另记 API 调用量 | 下载到文件或显示“完成”不能自动计为客户采用 |
| 编辑交接 | 在目标剪辑软件实际打开交换文件，重连到交接目录，在剪点与末帧比对画面和音频。代理供剪辑，定版导出使用母版来源 | 只解析 XML/JSON 不能证明本机 FableCut 版本接受该格式，交换包打开也不能证明母版效果已完整重建 |

用三段真实业务素材校准最容易揭示决策缺口。第一段是两秒隧道接六秒办公室，观察刻意白闪误报与跳时空声音。第二段是同角色跨两个机位完成拿起道具的动作，覆盖方向、手部、道具状态。第三段含中文公司名称、缩写、一句同步口播和后叠定版，检查声音、字稿、字形和时长。比较 Qwen 与 DeepSeek 写分镜时应遮去模型名、使用同一原文与素材，并同时记录合法输出、原文遗漏、人工采用与等待时间。验收结果应标明模型供应商实际返回的标识、工作流指纹、设备、片长和样本数。

公开源码、文档与论坛用于确定流程和识别失败方式。未完成实机生成、NLE 往返或人工听看时，对应质量、兼容性与耗时只能待测。该项目的本地部署、自检和真实任务入口见[工作站手册](workstation.md)。
