import json
import os
import re
import subprocess
import time
import tempfile
from fractions import Fraction
from pathlib import Path
from urllib.parse import urlsplit

from .common import Rejected, canvas, request, run, write


def probe(path, count=False):
    options = ["-count_frames"] if count else []
    return json.loads(run(["ffprobe", "-v", "error", *options, "-show_streams",
                           "-show_format", "-of", "json", path]))


def stream(info, kind):
    return next((s for s in info["streams"] if s["codec_type"] == kind), {})


def ff(*args, cwd=None):
    return run(["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error", "-xerror",
                "-filter_complex_threads", "2", "-filter_threads", "2", *args], cwd=cwd)


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
        if master and (v["codec_name"] != "h264" or a.get("codec_name") != "aac"
                       or abs(float(a.get("duration", 0)) - frames/24) > 0.12):
            raise Rejected("Master must be H.264/AAC with synchronized audio")
        ff("-i", path, "-map", "0:v:0", "-map", "0:a?", "-f", "null", "-")
        peak = None
        if audible and a:
            check = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path), "-vn",
                                    "-af", "volumedetect", "-f", "null", "-"], capture_output=True, timeout=3600)
            m = re.search(rb"max_volume:\s*([-\d.]+) dB", check.stderr)
            if not m or float(m[1]) < -75:
                raise Rejected("Audio required but track is silent or unmeasurable")
            peak = float(m[1])
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


def narrate(shot, directory):
    if os.name != "nt":
        raise ValueError("本地旁白需要 Windows SAPI 或预录语音")
    (directory / "line.txt").write_text(shot["narration"], encoding="utf-8-sig")
    run(["powershell.exe", "-NoProfile", "-NonInteractive", "-File",
         Path(__file__).with_name("voice.ps1"), directory], timeout=120)
    wav = directory / "voice.wav"
    if float(probe(wav)["format"]["duration"]) > shot["seconds"] + 0.02:
        raise ValueError("旁白超出镜头时长，需重新分镜或缩短台词")
    return wav


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
    if stream(probe(src), "audio"):
        audio = "[0:a]"
    else:
        args.extend(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"])
        audio = f"[{count}:a]"
        count += 1
    if shot["narration"]:
        args.extend(["-i", narrate(shot, directory)])
        layers.extend([audio + "volume=0.35[bed]", f"[bed][{count}:a]amix=inputs=2:normalize=0:duration=first[mix]"])
        audio = "[mix]"
    layers.append(audio + "aresample=48000,apad,atrim=duration=" + str(duration) + "[a]")
    out = directory / "cut.mp4"
    ff(*args, "-filter_complex", ";".join(layers), "-map", "[v]", "-map", "[a]",
       "-t", str(duration), "-r", "24", "-c:v", "libx264", "-crf", "17", "-preset", "veryfast",
       "-c:a", "aac", "-ac", "2", "-ar", "48000", out)
    return out


def music(cfg, prompt, seconds, directory, checkpoint):
    base = cfg["ace"].rstrip("/")
    ticket = request(base + "/release_task", {"prompt": prompt, "lyrics": "[Instrumental]",
                     "instrumental": True, "audio_duration": max(30, seconds), "thinking": False,
                     "model": "acestep-v15-turbo", "batch_size": 1, "audio_format": "wav"})
    tid = ticket["data"]["task_id"]
    write(directory / "score-ticket.json", {"task_id": tid})
    end = time.monotonic() + 3600
    while time.monotonic() < end:
        checkpoint()
        items = request(base + "/query_result", {"task_id_list": [tid]})["data"]
        item = next((row for row in items if row["task_id"] == tid), {})
        if item.get("status") == 2:
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
                raise ValueError("生成的配乐不足片长")
            return file
        time.sleep(cfg.get("poll_seconds", 2))
    raise TimeoutError("ACE-Step timeout")


def assemble(clips, score, seconds, directory):
    # Video is stream-copied from conformed cuts. Decode each cut's AAC separately to
    # remove encoder delay before concatenating the audio on the exact 48 kHz clock.
    listing = directory / "cuts.ffconcat"
    listing.write_text("ffconcat version 1.0\n" + "".join(
        f"file '{p.relative_to(directory).as_posix()}'\nduration {t}\n" for p, t in clips), "utf8")
    raw = directory / "mix.pcm"
    with raw.open("wb") as dst:
        for p, duration in clips:
            dst.write(ff("-i", p, "-vn", "-t", str(duration), "-ac", "2", "-ar", "48000",
                         "-f", "s16le", "-"))
    args = ["-f", "concat", "-safe", "1", "-i", listing, "-f", "s16le", "-ac", "2", "-ar", "48000", "-i", raw]
    if score:
        args += ["-i", score, "-filter_complex",
                 f"[2:a]volume=0.16,atrim=duration={seconds},afade=t=out:st={max(0,seconds-1)}:d=1[score];"
                 "[1:a][score]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.94[a]", "-map", "[a]"]
    else:
        args += ["-map", "1:a"]
    output = directory / "candidate.mp4"
    ff(*args, "-map", "0:v:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
       "-t", str(seconds), "-movflags", "+faststart", output)
    raw.unlink()
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
