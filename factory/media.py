import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
import tempfile
from fractions import Fraction
from pathlib import Path
from urllib.parse import urlsplit

from .common import read, Rejected, canvas, request, run, write


def probe(path, count=False):
    options = ["-count_frames"] if count else []
    return json.loads(run(["ffprobe", "-v", "error", *options, "-show_streams",
                           "-show_format", "-of", "json", path]))


def stream(info, kind):
    return next((s for s in info["streams"] if s["codec_type"] == kind), {})


def ff(*args, cwd=None):
    return run(["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error", "-xerror",
                "-filter_complex_threads", "2", "-filter_threads", "2", *args], cwd=cwd)


def _volume(path, duration=None):
    # volumedetect counts interleaved samples. Force stereo so duration can also
    # be checked for sources whose containers do not publish an audio duration.
    filters = "aresample=48000,aformat=channel_layouts=stereo,"
    if duration is not None:
        filters += f"atrim=duration={duration},"
    result = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-xerror",
                             "-i", str(path), "-map", "0:a:0", "-vn", "-af",
                             filters+"volumedetect", "-f", "null", "-"],
                            capture_output=True, timeout=3600)
    peak = re.search(rb"max_volume:\s*([-\d.]+) dB", result.stderr)
    sizes = re.findall(rb"n_samples:\s*([0-9]+)", result.stderr)
    if result.returncode or not peak or not sizes:
        raise ValueError("无法解码并测量音轨")
    return float(peak[1]), sum(map(int, sizes)) / (2*48000)


def technical(path, cfg, frames, master=False, audible=True):
    try:
        info = probe(path, count=True)
        v, a = stream(info, "video"), stream(info, "audio")
        allowed = {(cfg["width"], cfg["height"])} if master else {canvas(cfg), (cfg["width"], cfg["height"])}
        if (v.get("width"), v.get("height")) not in allowed:
            raise Rejected(f"Unexpected canvas {v.get('width')}×{v.get('height')}; expected {allowed}")
        n = int(v["nb_read_frames"])
        if Fraction(v["avg_frame_rate"]) != 24 or (n != frames if master else not frames <= n <= frames+8):
            raise Rejected(f"Timeline mismatch: {n} frames at {v['avg_frame_rate']}; target {frames}/24")
        if audible and not a:
            raise Rejected("缺少剧本要求的音轨；请检查 ComfyUI 保存节点是否连接音频")
        if master:
            duration = frames / 24
            if (v["codec_name"] != "h264" or a.get("codec_name") != "aac"
                    or a.get("channels") != 2 or int(a.get("sample_rate", 0)) != 48000):
                raise Rejected("母版需要 H.264 视频和 48 kHz 双声道 AAC 音轨")
            # The MP4 edit list removes AAC priming. Both tracks must begin on the
            # same programme clock and the container must end at the requested cut.
            if (any(abs(float(s.get("start_time", 1))) > .001 for s in (v, a))
                    or abs(float(a.get("duration", 0)) - duration) > .022
                    or abs(float(info["format"]["duration"]) - duration) > .001):
                raise Rejected("母版音画起点或容器时长偏离指定时间线")

        ff("-i", path, "-map", "0:v:0", "-map", "0:a?", "-f", "null", "-")
        peak = None
        if audible and a:
            peak, audio_seconds = _volume(path, frames/24)
            if peak < -75:
                raise Rejected("所需音轨在实际剪辑区间内静音")
            if audio_seconds < frames/24 - .055:
                raise Rejected("所需音轨提前结束，不能用静音填补缺失的现场声")
            if not master and abs(float(a.get("start_time", 0))-float(v.get("start_time", 0))) > .085:
                raise Rejected("现场声与画面起点相差超过两帧")
        return {"frames": n, "fps": 24, "width": v["width"], "height": v["height"],
                "audio": a.get("codec_name"), "peak_db": peak}
    except (KeyError, ValueError, ZeroDivisionError) as err:
        raise Rejected(str(err)) from err


def samples(path, shot, folder):
    n = shot["frames"]
    images = []
    for i, frame in enumerate([0, n // 4, n // 2, 3*n // 4, n-1]):
        name = folder / f"frame-{i}.jpg"
        ff("-i", path, "-vf", f"select=eq(n\\,{frame}),scale=640:-2", "-frames:v", "1", name)
        images.append(name)
    tail = folder / "tail.png"
    ff("-i", path, "-vf", f"select=eq(n\\,{n-1})", "-frames:v", "1", tail)
    return images


def graphics(cfg, shot, overlay, path):
    from PIL import Image, ImageDraw, ImageFont
    w, h = cfg["width"], cfg["height"]
    canvas_img = Image.new("RGBA", (w, h))
    if overlay:
        with Image.open(overlay) as img:
            canvas_img.alpha_composite(img.convert("RGBA").resize((w, h), Image.Resampling.LANCZOS))
    for text, position, start_size in ((shot["title"], .5, h//18), (shot.get("caption"), .86, h//25)):
        if not text:
            continue
        draw = ImageDraw.Draw(canvas_img)
        size = max(12, start_size)
        while size >= 8:
            font = ImageFont.truetype(cfg["font"], size)
            box = draw.multiline_textbbox((0, 0), text, font=font, align="center")
            if box[2]-box[0] <= .90*w and box[3]-box[1] <= (1-position)*1.7*h:
                break
            size -= 1
        else:
            raise ValueError("定版文字过长，无法在安全区内排版")
        draw.multiline_text((w//2, round(h*position)), text, font=font, anchor="mm",
                            align="center", fill="white", stroke_width=max(1, size//22), stroke_fill="black")
    canvas_img.save(path)


class VoiceTimingError(ValueError):
    """Usable speech needs a larger picture edit."""
    def __init__(self, seconds, budget):
        self.seconds = seconds
        self.budget = budget
        super().__init__(f"旁白为 {seconds:.2f} 秒，超过镜头 {budget} 秒；请重新分镜或缩短台词")


def _validate_voice(path, seconds=None):
    level, length = _volume(path)
    if level < -65 or length < .05:
        raise ValueError("旁白录音没有可用的语音电平，请检查 Windows 语音")
    if seconds is not None and length > seconds + 1/48000:
        raise VoiceTimingError(length, seconds)
    return {"peak_db": level, "seconds": length}


def _voice_input(shot):
    return {"line": shot["narration"], "requested_voice": os.getenv("FILM_VOICE", ""),
            "culture": os.getenv("FILM_VOICE_CULTURE", "")}


def _record_voice(line, work):
    if os.name != "nt":
        raise ValueError("本地旁白需要 Windows SAPI")
    (work/"line.txt").write_text(line, encoding="utf-8-sig")
    run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", Path(__file__).with_name("voice.ps1"), work], timeout=120)


def _has_voice(directory, identity):
    receipt = directory/"voice.json"
    return (receipt.is_file() and (directory/"voice.wav").is_file()
            and read(receipt).get("input") == identity)


def narrate(shot, directory):
    identity = _voice_input(shot)
    receipt = directory/"voice.json"
    wav = directory/"voice.wav"
    if _has_voice(directory, identity):
        _validate_voice(wav, shot["seconds"])
        return wav
    with tempfile.TemporaryDirectory(dir=directory) as tmp:
        work = Path(tmp)
        _record_voice(identity["line"], work)
        facts = _validate_voice(work/"voice.wav")
        selected = read(work/"selected-voice.json")
        # Commit a good recording even if this shot is too short. A later edit
        # can reuse that exact recording, with its original measured duration.
        receipt.unlink(missing_ok=True)
        (work/"voice.wav").replace(wav)
        (work/"line.txt").replace(directory/"line.txt")
        write(receipt, {"input": identity, "selected": selected, **facts})
    if facts["seconds"] > shot["seconds"] + 1/48000:
        raise VoiceTimingError(facts["seconds"], shot["seconds"])
    return wav


def _install_voice(source, destination):
    destination.mkdir(parents=True,exist_ok=True)
    metadata = read(source/"voice.json")
    with tempfile.TemporaryDirectory(dir=destination) as tmp:
        stage = Path(tmp)
        shutil.copyfile(source/"voice.wav", stage/"voice.wav")
        (stage/"line.txt").write_text(metadata["input"]["line"], encoding="utf-8-sig")
        (destination/"voice.json").unlink(missing_ok=True)
        (stage/"voice.wav").replace(destination/"voice.wav")
        (stage/"line.txt").replace(destination/"line.txt")
        write(destination/"voice.json", metadata)


_voice_cache_lock = threading.Lock()


def prepare_voice(shot, job_folder, candidate=None):
    """Read a line once per job and validate it against each proposed shot budget."""
    if not shot.get("narration"):
        return None
    identity = _voice_input(shot)
    digest = hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    source = Path(job_folder)/"voices"/digest
    candidate = Path(candidate) if candidate is not None else None
    with _voice_cache_lock:
        source.mkdir(parents=True, exist_ok=True)
        # Resume a take created before the job-scoped cache was available.
        if candidate and not _has_voice(source, identity) and _has_voice(candidate, identity):
            _validate_voice(candidate/"voice.wav")
            _install_voice(candidate, source)
        wav = narrate(shot,source)
        if candidate is None or candidate == source:
            return wav
        _install_voice(source, candidate)
    return candidate/"voice.wav"


def silent_shot(shot):
    """An explicitly silent cut overrides the global score unless it has speech."""
    return (shot.get("sound", "").strip().lower() in {"silence", "silent", "静音"}
            and not shot.get("dialogue") and not shot.get("narration"))


def conform(cfg, src, shot, overlay, directory):
    w, h, duration = cfg["width"], cfg["height"], shot["seconds"]
    args = ["-i", src]
    layers = []
    count = 1
    video = f"[0:v]crop={w}:{h},setsar=1"
    if overlay or shot["title"] or shot.get("caption"):
        image = directory / "overlay.png"
        graphics(cfg, shot, overlay, image)
        args.extend(["-i", image])
        video += f"[base];[base][{count}:v]overlay"
        count += 1
    layers.append(video + ",format=yuv420p[v]")
    mute_native = shot.get("sound", "").strip().lower() in {"silence", "silent", "静音"} and not shot.get("dialogue")
    if not mute_native and stream(probe(src), "audio"):
        audio = "[0:a]"
    else:
        args.extend(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"])
        audio = f"[{count}:a]"
        count += 1
    if shot["narration"]:
        args.extend(["-i", narrate(shot, directory)])
        layers.extend([
            f"[{count}:a]aresample=48000,apad,atrim=duration={duration},asetnsamples=n=240:p=0,asplit=2[voice][key]",
            audio + f"aresample=48000,apad,atrim=duration={duration},asetnsamples=n=240:p=0[native]",
            "[native][key]sidechaincompress=threshold=0.025:ratio=6:attack=12:release=300[bed]",
            "[bed][voice]amix=inputs=2:normalize=0:duration=first,"
            "alimiter=limit=0.9:level=0:latency=1[mix]",
        ])
        audio = "[mix]"
    layers.append(audio + "aresample=48000,apad,atrim=duration=" + str(duration) + "[a]")
    out = directory / "cut.mp4"
    ff(*args, "-filter_complex", ";".join(layers), "-map", "[v]", "-map", "[a]",
       "-t", str(duration), "-r", "24", "-c:v", "libx264", "-crf", "17", "-preset", "veryfast",
       "-c:a", "aac", "-ac", "2", "-ar", "48000", out)
    return out


def music(cfg, prompt, seconds, directory, checkpoint):
    base = cfg["ace"].rstrip("/")
    receipt = directory/"score-ticket.json"
    if receipt.exists():
        stored = read(receipt)
        if stored.get("base",base)!=base:
            raise ValueError("待恢复配乐的 ACE-Step 服务地址已更改")
        tid = stored.get("task_id")
        if not tid:
            raise ValueError("ACE-Step 提交回执丢失，请先核对服务端任务，再补录 score-ticket.json 的 task_id")
    else:
        write(receipt,{"base":base,"state":"submitting"})
        ticket = request(base + "/release_task", {"prompt":prompt,"lyrics":"[Instrumental]",
                         "instrumental":True,"audio_duration":max(30,seconds),"thinking":False,
                         "model":"acestep-v15-turbo","batch_size":1,"audio_format":"wav"})
        tid = ticket["data"]["task_id"]
        write(receipt,{"base":base,"task_id":tid})
    end = time.monotonic() + 3600
    while time.monotonic() < end:
        checkpoint()
        items = request(base + "/query_result", {"task_id_list": [tid]})["data"]
        item = next((row for row in items if row["task_id"] == tid), {})
        if item.get("status") == 2:
            receipt.replace(directory/("score-failed-"+str(time.time_ns())+".json"))
            raise ValueError("ACE-Step 配乐失败：" + str(item.get("result", ""))[:600])
        if item.get("status") == 1:
            results = item["result"]
            link = (json.loads(results) if isinstance(results, str) else results)[0]["file"]
            url = urlsplit(link)
            if url.path != "/v1/audio" or (url.netloc and url.netloc != urlsplit(base).netloc):
                raise ValueError("Unexpected ACE audio URL")
            file = directory / "score.wav"
            request(base + url.path + "?" + url.query, dest=file, timeout=300)
            if float(probe(file)["format"]["duration"]) < seconds - .05:
                receipt.replace(directory/("score-short-"+str(time.time_ns())+".json"))
                raise ValueError("生成的配乐不足片长")
            stats = subprocess.run(["ffmpeg","-nostdin","-hide_banner","-i",str(file),"-vn","-af",
                                    "volumedetect","-f","null","-"],capture_output=True,timeout=600)
            peak = re.search(rb"max_volume:\s*([-\d.]+) dB",stats.stderr)
            if stats.returncode or not peak or float(peak[1]) < -75:
                receipt.replace(directory/("score-silent-"+str(time.time_ns())+".json"))
                raise ValueError("ACE-Step 配乐单轨静音或无法解码")
            return file
        time.sleep(cfg.get("poll_seconds", 2))
    raise TimeoutError("ACE-Step timeout")


def programme_level(path):
    # Inspect the actual input measurement. loudnorm also describes a hypothetical
    # normalized output, which is irrelevant to verifying this encoded file.
    proc = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-xerror",
                           "-i", str(path), "-map", "0:a:0", "-af",
                           "loudnorm=print_format=json", "-f", "null", "-"],
                          capture_output=True, timeout=3600)
    log = proc.stderr.decode("utf-8", "replace")
    match = re.search(r'\{\s*"input_i".*?\}', log, re.DOTALL)
    if proc.returncode or not match:
        raise Rejected("整片响度测量失败：" + log[-700:])
    values = json.loads(match[0])

    def metric(key):
        value = float(values[key])
        return value if math.isfinite(value) else None

    return {"integrated_lufs": metric("input_i"), "true_peak_dbtp": metric("input_tp"),
            "range_lu": metric("input_lra")}


def _mix_intervals(timeline, key):
    spans = []
    start = 0
    for shot in timeline:
        end = start + shot["seconds"]
        if key(shot):
            if spans and spans[-1][1] == start:
                spans[-1][1] = end
            else:
                spans.append([start, end])
        start = end
    return spans


def _time_gate(spans):
    return "+".join(f"gte(t,{start})*lt(t,{end})" for start, end in spans)


def _programme_gain(level, target, ceiling):
    peak = level["true_peak_dbtp"]
    loudness = level["integrated_lufs"]
    if peak is None:
        return 0.0, "silence"
    # Leave codec headroom for AAC and retain intentional film dynamics. Only
    # apply constant gain; an exceptionally quiet programme is raised by at most 12 dB.
    desired = target-loudness if loudness is not None else 0
    peak_room = ceiling-1.0-peak
    gain = min(desired, 12, peak_room)
    if gain == peak_room:
        return gain, "peak_limited"
    if gain < desired or loudness is None:
        return gain, "quiet_programme"
    return gain, "target"


def _timeline(clips, timeline, seconds):
    if sum(length for _, length in clips) != seconds:
        raise ValueError("剪辑清单与总时长不匹配")
    if timeline is None:
        timeline = [{"seconds":length} for _,length in clips]
    if [s["seconds"] for s in timeline] != [length for _,length in clips]:
        raise ValueError("已采用镜头与混音时间线不匹配")
    return timeline


def _decode_foreground(clips, timeline, raw):
    # Independently decode each accepted cut so AAC priming never accumulates.
    with raw.open("wb") as dst:
        for (path, length), shot in zip(clips, timeline):
            sample_count = round(length * 48000)
            if silent_shot(shot):
                dst.write(bytes(sample_count*4))
                continue
            data = ff("-i", path, "-map", "0:a:0", "-vn", "-af",
                      f"aresample=48000,atrim=end_sample={sample_count},"
                      f"afade=t=in:d=0.005,afade=t=out:st={length-.005:.3f}:d=0.005",
                      "-ac", "2", "-ar", "48000", "-f", "s16le", "-")
            missing = sample_count*4 - len(data)
            if not 0 <= missing <= 1024*4:
                raise Rejected(f"{path.name} 的音轨长度与镜头时长不符")
            dst.write(data)
            dst.write(bytes(missing))


def _audio_buses(raw, score, seconds, speech, silence):
    inputs = ["-f", "s16le", "-ar", "48000", "-ac", "2", "-i", raw]
    graph, foreground, bed = [], "[0:a]", None
    if score:
        inputs += ["-i", score]
        graph = [f"[1:a]aresample=48000,aformat=channel_layouts=stereo,volume=0.20,"
                 f"apad,atrim=duration={seconds},afade=t=out:st={max(0,seconds-1)}:d=1,"
                 "asetnsamples=n=240:p=0[music]"]
        bed = "[music]"
        if speech:
            graph.extend(["[0:a]asplit=2[foreground][control]",
                          "[control]asetnsamples=n=240:p=0,highpass=f=150,lowpass=f=4000,"
                          f"volume=0:enable='not({_time_gate(speech)})'[key]",
                          "[music][key]sidechaincompress=threshold=0.02:ratio=6:attack=15:"
                          "release=320:detection=rms:makeup=1[ducked]"])
            bed = "[ducked]"
        if silence:
            graph.append(bed + f"asetnsamples=n=240:p=0,volume=0:enable='{_time_gate(silence)}'[scored]")
            bed = "[scored]"
        foreground = "[foreground]" if speech else "[0:a]"
        # The compressor can flush slightly short when its control input ends.
        # Fill only after the envelope so standalone stems keep the full handle.
        graph.append(bed + f"apad,atrim=end_sample={round(seconds*48000)}[bedoutclock]")
        bed = "[bedoutclock]"
    return inputs, graph, foreground, bed


def export_stems(clips, score, seconds, output_dir, *, timeline, gain_db):
    """Render the accepted pre-AAC buses with the published programme gain."""
    timeline = _timeline(clips, timeline, seconds)
    if not math.isfinite(gain_db):
        raise ValueError("无效的母版增益")
    speech = _mix_intervals(timeline, lambda shot: shot.get("dialogue") or shot.get("narration"))
    silence = _mix_intervals(timeline, silent_shot)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
        work = Path(tmp)
        raw = work/"foreground.pcm"
        _decode_foreground(clips, timeline, raw)
        inputs, graph, foreground, bed = _audio_buses(raw, score, seconds, speech, silence)
        outputs = []
        for bus, name, label in [(foreground, "foreground.wav", "fgout"), (bed, "music-bed.wav", "bgout")]:
            if bus:
                graph.append(f"{bus}volume={gain_db:.5f}dB[{label}]")
                outputs += ["-map", f"[{label}]", "-ac", "2", "-ar", "48000", "-c:a", "pcm_f32le", work/name]
        ff(*inputs, "-filter_complex", ";".join(graph), *outputs)
        (work/"foreground.wav").replace(output_dir/"foreground.wav")
        if bed:
            (work/"music-bed.wav").replace(output_dir/"music-bed.wav")
    return output_dir/"foreground.wav", output_dir/"music-bed.wav" if bed else None


def assemble(clips, score, seconds, directory, *, timeline=None, cfg=None):
    cfg = cfg or {}
    target = float(cfg.get("master_target_lufs", -18))
    ceiling = float(cfg.get("master_peak_dbtp", -1))
    if not -36 <= target <= -12 or not -6 <= ceiling <= -1:
        raise ValueError("master_target_lufs 需在 -36 至 -12 之间，master_peak_dbtp 需在 -6 至 -1 之间")
    timeline = _timeline(clips, timeline, seconds)
    speech = _mix_intervals(timeline, lambda shot: shot.get("dialogue") or shot.get("narration"))
    silence = _mix_intervals(timeline, silent_shot)
    listing = directory / "cuts.ffconcat"
    listing.write_text("ffconcat version 1.0\n" + "".join(
        f"file '{p.relative_to(directory).as_posix()}'\nduration {length}\n" for p,length in clips), "utf8")
    output = directory / "candidate.mp4"
    with tempfile.TemporaryDirectory(dir=directory) as work:
        raw = Path(work)/"foreground.pcm"
        _decode_foreground(clips, timeline, raw)
        inputs, graph, foreground, bed = _audio_buses(raw, score, seconds, speech, silence)
        mix = "0:a"
        if bed:
            graph.append(f"{foreground}{bed}amix=inputs=2:duration=first:normalize=0[mix]")
            inputs += ["-filter_complex", ";".join(graph)]
            mix = "[mix]"
        # Floating-point intermediate preserves peaks above unity for the gain pass.
        wave = Path(work) / "programme.wav"
        ff(*inputs, "-map", mix, "-c:a", "pcm_f32le", "-ac", "2", "-ar", "48000", wave)
        initial = programme_level(wave)
        gain, reason = _programme_gain(initial, target, ceiling)
        for attempt in range(2):
            # The concat demuxer reports a negative audio start from AAC priming.
            # Preserve video packet timestamps so the first picture remains at zero.
            ff("-copyts", "-f", "concat", "-safe", "1", "-i", listing, "-i", wave,
               "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-af", f"volume={gain:.5f}dB",
               "-c:a", "aac", "-ac", "2", "-ar", "48000", "-b:a", "192k", "-t", str(seconds),
               "-movflags", "+faststart", output)
            measured = programme_level(output)
            peak = measured["true_peak_dbtp"]
            if peak is None or peak <= ceiling:
                break
            if attempt:
                raise Rejected(f"AAC 成片真峰值 {peak} dBTP 超出上限 {ceiling}")
            gain -= peak-ceiling+.5
            reason = "encoded_peak_limited"
        integrated = measured["integrated_lufs"]
        if initial["integrated_lufs"] is not None:
            expected = initial["integrated_lufs"]+gain
            if integrated is None or abs(integrated-expected)>1.5:
                raise Rejected("AAC 成片实测响度偏离预期超过 1.5 LU")
        write(directory/"audio-mix.json", {"sample_rate":48000, "channels":2,
              "score":bool(score), "duck_intervals_seconds":speech if score else [],
              "silent_intervals_seconds":silence, "target_lufs":target, "ceiling_dbtp":ceiling,
              "gain_db":round(gain,5), "gain_decision":reason, "before":initial, "encoded":measured})
    return output


def contact_sheets(movie, timeline, folder):
    """Select the midpoint of every accepted shot from the decoded final master."""
    from PIL import Image, ImageDraw
    with tempfile.TemporaryDirectory(dir=folder) as tmp:
        frames, pos = [], 0
        for s in timeline:
            frames.append(pos + s["frames"]//2)
            pos += s["frames"]
        select = "+".join(f"eq(n\\,{n})" for n in frames)
        ff("-i", movie, "-vf", f"select={select},scale=320:-2", "-fps_mode", "vfr",
           Path(tmp)/"sample-%04d.jpg")
        paths = sorted(Path(tmp).glob("sample-*.jpg"))
        if len(paths) != len(frames):
            raise Rejected("成片抽帧数量与时间线不符")
        boards = []
        for start in range(0, len(paths), 16):
            batch = paths[start:start+16]
            with Image.open(batch[0]) as first:
                w,h=first.size
            sheet=Image.new("RGB",(w*min(4,len(batch)), (h+22)*((len(batch)+3)//4)), "#192321")
            pen=ImageDraw.Draw(sheet)
            for i,p in enumerate(batch):
                x,y=i%4*w,i//4*(h+22)
                with Image.open(p) as frame:
                    sheet.paste(frame,(x,y))
                pen.text((x+6,y+h+2),f"S{start+i+1:02}  {frames[start+i]/24:.2f}s",fill="white")
            target=folder/f"master-sheet-{len(boards)+1:02}.jpg"
            sheet.save(target,quality=88)
            boards.append(target)
        return boards


def temporal_signals(path):
    """Scan the decoded shot, including intervals between the review thumbnails."""
    result = subprocess.run(["ffmpeg","-nostdin","-hide_banner","-nostats","-i",str(path),
                             "-an","-vf","scale=320:-2,blackdetect=d=0.16:pix_th=0.08,"
                             "freezedetect=n=-55dB:d=0.5","-f","null","-"],
                            capture_output=True,timeout=300)
    if result.returncode:
        raise Rejected("逐帧运动检查无法解码")
    log = result.stderr.decode("utf-8","replace")
    return {"black_intervals":[[float(a),float(b)] for a,b in re.findall(
                r"black_start:([\d.]+) black_end:([\d.]+)", log)],
            "freeze_events":[{"kind":kind,"second":float(value)} for kind,value in re.findall(
                r"lavfi\.freezedetect\.freeze_(start|end): ([\d.]+)", log)]}


def seam_sheet(previous_cut, current_raw, duration, folder):
    from PIL import Image, ImageDraw
    from io import BytesIO
    sheet = Image.new("RGB",(3*320,2*206),"#18231c")
    pen = ImageDraw.Draw(sheet)
    for i, (video, t) in enumerate([(previous_cut,duration-f/24) for f in (8,4,1)] +
                                    [(current_raw,f/24) for f in (0,3,7)]):
        raw = ff("-ss",f"{t:.8f}","-i",video,"-frames:v","1","-vf","scale=320:180",
                 "-f","image2pipe","-c:v","mjpeg","-")
        with Image.open(BytesIO(raw)) as frame:
            x,y=(i%3)*320,(i//3)*206
            sheet.paste(frame,(x,y))
            pen.text((x+5,y+182),f"{'outgoing' if i<3 else 'incoming'} | {t:.2f}s",fill="white")
    path=folder/"seam.jpg"
    sheet.save(path,quality=88)
    return path
