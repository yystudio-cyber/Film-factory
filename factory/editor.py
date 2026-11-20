"""Portable timeline and media for an already accepted production."""
import csv
import math
import re
import shutil
from fractions import Fraction
from pathlib import Path, PureWindowsPath

from . import media
from .common import read, write


FPS = 24


def _time(frames):
    return {"OTIO_SCHEMA": "RationalTime.1", "value": frames, "rate": FPS}


def _range(start, duration):
    return {"OTIO_SCHEMA": "TimeRange.1", "start_time": _time(start), "duration": _time(duration)}


def _item(schema, name, **fields):
    return {"OTIO_SCHEMA": schema, "name": name, "metadata": {}, "enabled": True,
            "source_range": None, "effects": [], "markers": [], "color": None, **fields}


def _clip(name, targets, start, duration, available, metadata=None):
    references = {key: {"OTIO_SCHEMA": "ExternalReference.1", "name": path,
                        "target_url": path, "metadata": {}, "available_image_bounds": None,
                        "available_range": _range(0, available)} for key, path in targets.items()}
    return _item("Clip.2", name, source_range=_range(start, duration), metadata=metadata or {},
                 media_references=references, active_media_reference_key="DEFAULT_MEDIA")


def _source(folder, relative):
    if not isinstance(relative, str) or PureWindowsPath(relative).drive:
        raise ValueError("素材路径必须相对于任务目录")
    path = (folder / relative.replace("\\", "/")).resolve()
    if not path.is_relative_to(folder.resolve()) or not path.is_file():
        raise ValueError(f"所选成片引用的素材缺失或越出目录：{relative}")
    return path


def _length(path):
    video = media.stream(media.probe(path), "video")
    if not video or Fraction(video["avg_frame_rate"]) != FPS:
        raise ValueError(f"需要 24 fps 的视频素材：{path.name}")
    count = video.get("nb_frames")
    if count in (None, "N/A"):
        count = media.stream(media.probe(path, count=True), "video")["nb_read_frames"]
    return int(count)


def _picture(output, shot, cut):
    label = f"S{shot['shot']:02}-{shot['code']}"
    name = label + ".mp4"
    if _length(cut) != shot["frames"]:
        raise ValueError(f"{label} 可剪辑成片帧数与发行记录不符")
    # Default picture is a lossless packet copy of the approved video stream.
    # Keeping it video-only prevents NLE importers from adding the native sound twice.
    picture = f"picture/{label}-picture.mp4"
    media.ff("-i", cut, "-map", "0:v:0", "-c:v", "copy", "-movflags", "+faststart", output/picture)
    media.ff("-i", cut, "-map", "0:v:0", "-map", "0:a?", "-vf",
             "scale=1280:720:force_original_aspect_ratio=decrease,"
             "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-crf", "18", "-c:a", "copy", "-movflags", "+faststart", output/name)
    metadata = {"code": shot["code"], "shot": shot["shot"], "beat": shot.get("beat", ""),
                "proxy": name, "picture_handle_in_frames": 0, "picture_handle_out_frames": 0}
    source_receipt = cut.parent/"source-ready.json"
    if source_receipt.is_file():
        raw = _source(cut.parent, read(source_receipt)["file"])
        length = _length(raw)
        if length < shot["frames"]:
            raise ValueError(f"{label} 原始素材短于采用范围")
        (output/"originals").mkdir(exist_ok=True)
        original = "originals/" + label + raw.suffix.lower()
        shutil.copyfile(raw, output/original)
        metadata.update(original=original, original_available_frames=length,
                        original_handle_in_frames=0, original_handle_out_frames=length-shot["frames"])
    return _clip(label, {"DEFAULT_MEDIA": picture, "PROXY": name}, 0, shot["frames"],
                 shot["frames"], {"film_factory": metadata})


def _sound(release, output, timeline, clips, total):
    report_file = release/"acceptance.json"
    report = read(report_file).get("audio_mix") if report_file.is_file() else None
    score = release/"score.wav"
    if (isinstance(report, dict) and isinstance(report.get("score"), bool) and
            type(report.get("gain_db")) in (int, float) and math.isfinite(report["gain_db"])):
        if not report["score"] or score.is_file():
            if report["score"]:
                shutil.copyfile(score, output/"score.wav")
            foreground, bed = media.export_stems(clips, score if report["score"] else None, total/FPS, output,
                                                 timeline=timeline, gain_db=float(report["gain_db"]))
            write(output/"audio-mix.json", report)
            return foreground.name, bed.name if bed else None, "foreground"
    # Earlier releases did not freeze a calibrated score and gain. Preserve their
    # actual accepted sound by decoding the master, including its original ducking.
    media.ff("-i", _source(release, "film.mp4"), "-map", "0:a:0", "-af",
             f"aresample=48000,atrim=end_sample={total*2000}", "-ar", "48000", "-ac", "2",
             "-c:a", "pcm_f32le", output/"programme.wav")
    return "programme.wav", None, "programme"


def export_edit(store, jid, output):
    """Export one frozen release. Publish the timeline after all media succeeds."""
    if store.job(jid)["state"] != "done":
        raise ValueError("请先完成成片验收")
    folder = (store.root/jid).resolve()
    release = store.delivery_dir(jid) if hasattr(store, "delivery_dir") else folder
    if release is None:
        raise ValueError("所选任务已经重新进入制作队列")
    timeline = read(release/"timeline.json")
    if not isinstance(timeline, list) or not timeline:
        raise ValueError("发行时间线缺少镜头")
    timeline = [dict(shot) for shot in timeline]
    cuts = []
    for number, shot in enumerate(timeline, 1):
        if (type(shot.get("shot")) is not int or shot["shot"] != number or
                not re.fullmatch(r"C[0-9]+", str(shot.get("code", ""))) or
                type(shot.get("frames")) is not int or shot["frames"] <= 0):
            raise ValueError("发行时间线中的镜号、片库号或帧数无效")
        shot.setdefault("seconds", shot["frames"]/FPS)
        if not math.isclose(shot["seconds"]*FPS, shot["frames"], abs_tol=1e-6):
            raise ValueError("发行时间线的秒数与帧数不匹配")
        cuts.append((_source(folder, shot["file"]), shot["seconds"]))
    output = Path(output).resolve()
    if output.is_relative_to(folder):
        raise ValueError("导出位置必须在原任务目录之外")
    output.mkdir(parents=True, exist_ok=False)
    try:
        (output/"picture").mkdir()
        total = sum(shot["frames"] for shot in timeline)
        sound_name, bed, sound_kind = _sound(release, output, timeline, cuts, total)
        for filename in filter(None, (sound_name, bed)):
            stream = media.stream(media.probe(output/filename), "audio")
            sample_count = int(stream["duration_ts"]) * Fraction(stream["time_base"]) * 48000
            if sample_count != total * (48000//FPS) or stream["channels"] != 2:
                raise ValueError(f"分轨文件长度或声道与发行时间线不符：{filename}")
        picture, sound = [], []
        position = 0
        with (output/"timeline.csv").open("w", encoding="utf-8-sig", newline="") as csv_file:
            table = csv.writer(csv_file)
            table.writerow(["shot", "clip", "file", "in_frame", "out_frame_exclusive", "fps", "description"])
            for shot, (cut, _) in zip(timeline, cuts):
                video = _picture(output, shot, cut)
                picture.append(video)
                length = shot["frames"]
                sound.append(_clip(video["name"], {"DEFAULT_MEDIA": sound_name}, position, length, total,
                                   {"film_factory": {"code": shot["code"], "shot": shot["shot"]}}))
                table.writerow([shot["shot"], shot["code"], video["metadata"]["film_factory"]["proxy"],
                                position, position+length, FPS, shot.get("beat", "")])
                position += length
        tracks = [_item("Track.1", "Picture", kind="Video", children=picture),
                  _item("Track.1", "Foreground" if sound_kind == "foreground" else "Accepted programme mix",
                        kind="Audio", children=sound)]
        if bed:
            tracks.append(_item("Track.1", "Music", kind="Audio", children=[
                _clip("Music bed", {"DEFAULT_MEDIA": bed}, 0, total, total)]))
        write(output/"timeline.otio", {"OTIO_SCHEMA": "Timeline.1", "name": jid,
              "metadata": {"film_factory": {"fps": FPS, "frames": total, "audio": sound_kind,
                                           "release": release.name}}, "global_start_time": _time(0),
              "tracks": _item("Stack.1", "tracks", children=tracks)})
    except BaseException:
        shutil.rmtree(output)
        raise
    return output/"timeline.otio"
