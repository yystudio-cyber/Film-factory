# 对白、配乐与母版声音验收

影片在 CPU 上完成最终混音。候选和采用版本保留各自的图像、现场声、旁白及配乐任务产物；导出后以最终 AAC 的解码结果为响度与真峰值的验收依据。

## 剪辑时钟与声部

每个已采用镜头独立解码，按 48 kHz 双声道、整数样点拼接。出现一个 AAC 帧以内的尾部差异时补齐少量零样点，更长的缺口会报错。切点使用 5 ms 包络减少咔嗒声。视频流复制到最终 MP4，保留视频原始表现时间戳，防止 concat 解复用器把 AAC 预卷导致的负音频起点转移到画面上。交付检查包括首帧和音轨的起点、真实帧数、容器时长以及 AAC 时长。

| 输入 | 处理与边界 |
|---|---|
| 同期对白 | 在有对白的镜头内，取现场声的 150–4000 Hz 成分作为音乐压缩器的控制信号。人声频段有能量时降低音乐，停顿时释放。同期声混入的大响声也可能触发压乐。 |
| 旁白 | Windows SAPI 输出独立语音。镜内使用旁白信号压低原声；影片总线在这些镜头内再以已混前景声控制配乐。 |
| 环境声 | 没有台词及旁白的镜头保留场景本身的动态，配乐维持基准增益。5 ms 切点包络不改变镜头时间线。 |
| 明确静音 | sound 为 silence、silent 或静音，且台词和旁白均为空时，镜头直接输出静音，整片混音也会静音该区间的配乐。 |
| 主音乐 | 配乐起始线性增益为 0.20。进入对白区间后，侧链以 15 ms 启动、320 ms 释放。片尾淡出一秒。 |

若剧本写清晰的环境底噪，可在 sound 中写出具体声源。将环境底噪描述为“静音”会触发明确静音的处理。表演、说话人身份和拟音的语义贴合度仍需试听确认。

## 响度

整体响度以 LUFS 表示，真峰值以 dBTP 表示。默认目标为整个节目 −18 LUFS、编码后真峰值上限 −1 dBTP，属于本工作站面向内部视频的工程设置。EBU R 128 广播建议的节目目标为 −23 LUFS。针对其他交付渠道，可在本机配置中添加以下字段。[EBU R 128](https://tech.ebu.ch/publications/r128) · [EBU 流媒体补充建议](https://tech.ebu.ch/publications/r128s2)

```json
{"master_target_lufs": -18, "master_peak_dbtp": -1}
```

目标允许 −36 至 −12 LUFS，真峰值上限允许 −6 至 −1 dBTP。第一次测量作用于经过压乐和声部混合的浮点母线。系统用恒定增益维持剧情的强弱变化，最高提升 12 dB，同时给有损编码留下 1 dB 真峰值余量。AAC 编码后会再次测量，若峰值仍过高，最多衰减并重编码一次。达到响度目标可能受峰值或极低电平约束；audio-mix.json 的 gain_decision 记录选择，encoded 给出实际测量值。全静音节目中没有可测的整体响度，写入 JSON null。

程序要求编码结果的整体响度与“初始测量 + 实际增益”的预期偏差不超过 1.5 LU，且真峰值不超过配置上限。该检查不会用平均响度覆盖峰值越限，也不会把极安静片段强行放大到目标。

## 实测与资料

集成样例使用 2 秒普通镜、2 秒对白镜、2 秒静音镜，共 144 帧。为了从 AAC 中分别测出两个声部的变化，使用不同频率的测试音代替现场声与配乐。本轮实测对白区间的配乐频率下降约 10.3 dB，前景频率变化约 −0.04 dB；末段两种测试音均静音。最终母版实测约 −18.04 LUFS、−15.33 dBTP。真实语音、音乐旋律和环境声的结果会随输入变化。

```powershell
python -m unittest discover -s tests -p test_master_audio.py -v
```

| 工具或项目 | 取舍 |
|---|---|
| [FFmpeg sidechaincompress](https://ffmpeg.org/ffmpeg-filters.html#sidechaincompress)、[loudnorm](https://ffmpeg.org/ffmpeg-filters.html#loudnorm) | 复用工作站已有 FFmpeg 的侧链压缩和响度测量，嗓音只影响指定区间的音乐。测量明确读取 input_i 和 input_tp。 |
| [ffmpeg-normalize](https://github.com/slhck/ffmpeg-normalize) | 提供 FFmpeg 响度处理工具，适合独立文件批量标准化。工厂需要已采用镜头的对白、旁白与静音区间，直接将检测和增益写入现有组接任务。 |
| [libebur128](https://github.com/jiixyj/libebur128)、[pyloudnorm](https://github.com/csteinmetz1/pyloudnorm) | 可作为独立响度测量实现进行交叉校准。本次实现统一使用 FFmpeg 解码与测量，可在两块 GPU 忙碌时运行。 |

## 同期对白与旁白的前置检查

同期对白的 faster-whisper 模型以 CPU int8 运行并在进程内复用。送识别的单声道 16 kHz 音频只含实际采用时段。启用语音活动检测，转录逐段记录起止时间，台词比对忽略大小写、全角差异、空格和标点。默认相似度门限为 0.8。如要求逐字匹配，可把 `speech_similarity` 设为 1，更严的门限也会拒绝一些同音字、繁简体及专名的识别差异。审查不会以剧本台词作为 ASR 提示。缺少可听现场声、音轨提前结束或音画起点相差超过两帧的候选会被拒绝。[faster-whisper 文档](https://github.com/SYSTRAN/faster-whisper)

有旁白时，整片分镜完成后先在 Windows 本机逐句生成并解码 WAV，核对可听电平和每段镜头预算。首次试读就会把可用的完整波形按台词、音色和语言区域保存到任务的 voices 目录。调整镜长后可直接复用原录音；所有候选与正式剪辑均采用这份通过试读的 WAV。没有匹配的桌面 SAPI 语音或句子超出镜长时，任务给出具体原因。每个候选的 voice.json 会记录实际声音、文化区域、用时和电平。同一输入在恢复及合成时复用已经检查的录音。默认按假名、韩文、汉字、拉丁字母分别匹配日、韩、中、英语音。拉丁文字的其他语言、特定地方口音和混合语言可设环境变量 `FILM_VOICE_CULTURE`（例如 de-DE、zh-CN）或通过 `FILM_VOICE` 指定桌面引擎实际列出的语音名称。[Microsoft SpeechSynthesizer](https://learn.microsoft.com/en-us/dotnet/api/system.speech.synthesis.speechsynthesizer)

编辑包中的 foreground.wav 和 music-bed.wav 在 48 kHz 双声道浮点格式下共用母版相同的时间门控、压缩器和最终增益。相加可重构 AAC 编码之前的节目混音。压缩器两路均使用 5 ms 的音频块，接受样片后检查包括非静音尾镜音乐的实测能量。

在 Windows 工作站项目目录运行以下命令，可独立验收实际 SAPI 语音和本机 faster-whisper。默认中文句子的镜长预算为 6 秒。CLI 会生成临时 WAV、测量可听电平及长度、实际转录并输出含语音名称、相似度、逐段内容的 JSON。失败时退出码为 1。指定语种或特殊发音的项目可用 `--line` 替换样句。

```powershell
python -m factory.speech --config config.local.json
```
