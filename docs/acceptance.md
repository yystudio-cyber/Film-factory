# 验证 RC3

**离线回归。** 使用 Python 3.12、Pillow、FFmpeg、ffprobe 和剪辑交换所需的 OpenTimelineIO。在仓库根目录执行以下命令。故障重放只使用临时文件、回环服务替身与虚构媒体。输出名称必须未被使用。

~~~sh
python -B -m unittest discover -s tests -v
python -B tools/replay_release.py . trial-clean clean unknown lost changed_cut alias --seed 17017
python -B tools/replay_release.py . trial-held incomplete ambiguous changed_tail lost_json published_damage --seed 91441
python -B tools/replay_local_motion.py . trial-motion.json --held-out
python -B tools/replay_pilot.py . trial-pilot
~~~

**回执与母版。** 在相同的三镜重放下，替换采用视频后，工厂须暂停并保存现场。恢复原件再继续，母版应与干净对照逐字节一致。断开已接单的响应或删除 JSON 回执时，必须沿用原任务身份。状态矛盾、节点错误或过多相符票据均不能把任意可播放输出当作完成。

**审片。** 风险镜头逐帧缩小扫描，常规间隔画格之外再提交明显转换和局部短脉冲的相邻帧。要求审片给出具体帧号、可见证据及剧情依据。虚构数据的标签能证明哪些帧进入审片；实际模型对自然表演、皮肤、动作及遮挡的判断需看真实结果。

**工作站。** 激活本机服务与绑定后，可用 `python tools/make_station_cards.py NEW_DIRECTORY` 生成两张虚构参考图，将两图复制到隔离的测试素材目录。使用这两张图片，文件名分别传给 `station-accept --render --first ... --last ... --run-id ... --report ...`。运行前先查 `python film.py station-accept --help`。即使不带 render，模型探针也可能产生实际调用；先确认授权。三路各请求 49 帧，剪成 144 帧无对白片，检查 GPU 归属、1920×1080 / 24 fps 和原声音轨。

**正式样片。** 在最终 AAC 中核对预期语种的原声或配音，字幕按单独证据记录。48 kHz 双声道、−18 LUFS 和 ≤−1 dBTP 可以测量。对白可懂度、音乐是否妨碍剧情以及剪点听感还需回听。记录每小时采用秒数、首轮合格率、返工成本、整片墙钟时间和单卡显存峰值。六秒与六十秒合成实验只验证流程和媒体规格。
