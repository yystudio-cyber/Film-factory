"""Independently qualify the actual imported nine-shot pilot, preserving evidence paths."""
import re
from pathlib import Path

from . import media
from .common import read
from .store import Store


def pilot_segments(shots):
    durations=[8,8,6,6,6,6,6,6,8]
    if not shots:
        return False
    position=0
    cursor=0
    for number,seconds in enumerate(durations,1):
        section=0
        group=[]
        while position<len(shots) and section<seconds:
            shot=shots[position]
            n=shot.get("seconds")
            if type(n) is not int or not 2<=n<=8 or shot.get("frames")!=24*n:
                return False
            timing=shot.get("source_clip") or {}
            if (timing.get("clip")!=number or timing.get("part")!=len(group)+1
                    or timing.get("start_frame")!=cursor or timing.get("seconds")!=n):
                return False
            group.append(timing)
            section+=n
            cursor+=n*24
            position+=1
        if section!=seconds or any(t.get("parts")!=len(group) for t in group):
            return False
    return position==len(shots) and cursor==1440


def qualify(cfg,jid):
    if not re.fullmatch(r"[0-9a-f]{32}",jid) or not cfg.get("station_contract"):
        raise ValueError("需要工作站生产配置及正式任务编号")
    folder = cfg["data"]/jid
    spec = read(folder/"brief.json")
    bundle = spec.get("studio_bundle","")
    if not re.fullmatch(r"[a-f0-9]{64}",bundle):
        raise ValueError("需要由旧 Studio 冻结导入的真实先导片剧本")
    manifest = read(cfg["data"]/"studio-imports"/bundle/"manifest.json")
    expected_project = cfg.get("pilot_project_id")
    if not isinstance(expected_project, str) or not expected_project.strip():
        raise ValueError("请先在本地配置 pilot_project_id 指定验收项目")
    if manifest.get("project_id") != expected_project or spec["seconds"] != 60:
        raise ValueError("项目来源或时长与先导片要求不符")
    from .integrity import verify_bundle
    delivery,record = Store(cfg).delivery_record(jid)
    if not delivery:
        raise ValueError("尚无已发布到本机成片库的验收版本")
    if record is None:
        raise ValueError("正式发行验收缺少现场发布时的文件指纹")
    verify_bundle(delivery,record)
    shots = read(delivery/"timeline.json")
    audit = read(delivery/"acceptance.json")
    errors = []
    if not pilot_segments(shots):
        errors.append("镜序或时长不符")
    lines=[s for s in shots if s.get("dialogue")]
    checked=audit.get("final_mix_dialogue",[])
    expected_languages={s.get("dialogue_language","").lower().split("-")[0] for s in lines}
    if (not {"zh","en","de","sv"}<=expected_languages or
            {row.get("shot") for row in checked if row.get("status")=="verified"
             and row.get("language")==row.get("expected_language")}!={s["shot"] for s in lines}):
        errors.append("四语原稿或最终 AAC 分镜对白回听证据不完整")
    if audit.get("qualification")!="passed" or not audit["editorial"]["passed"] or not audit["visual"]["passed"]:
        errors.append("成片含未验对白、未通过的剧情或视觉验收")
    if not audit["source_verdicts"] or not all(s["passed"] for s in audit["source_verdicts"]):
        errors.append("必留内容未全部通过")
    if not audit["audio_mix"]["score"] or not (delivery/"score.wav").is_file():
        errors.append("未取得实际配乐")
    facts = media.technical(delivery/"film.mp4",cfg,1440,master=True)
    level = media.programme_level(delivery/"film.mp4")
    if level["integrated_lufs"] is None or abs(level["integrated_lufs"]+18)>1 or level["true_peak_dbtp"]>-1:
        errors.append("实际 AAC 响度超出门槛")
    requests = []
    previous = None
    offset = 0
    end_frames = []
    tags = []
    for shot in shots:
        cut = (folder/shot["file"]).resolve(strict=True)
        if not cut.is_relative_to((folder/"takes").resolve()):
            raise ValueError("采用镜头超出任务目录")
        take = cut.parent
        receipt = read(take/"remote.json")
        pid = receipt.get("prompt_id")
        if not pid or receipt.get("base")!=cfg["comfy"].rstrip("/") or pid in requests:
            errors.append(shot["code"]+" 缺少唯一的本机渲染回执")
        requests.append(pid)
        src = take/read(take/"source-ready.json")["file"]
        media.technical(src,cfg,shot["frames"],audible=not media.silent_shot(shot))
        if shot["start"]=="previous" and (not previous or shot.get("previous_code")!=previous):
            errors.append(shot["code"]+" 采用片承接错误")
        previous = shot["code"]
        if shot.get("endcard"):
            if not (take/"endcard.png").is_file():
                errors.append(shot["code"]+" 片尾图层缺失")
            end_frames.extend(range(offset+shot["endcard"]["first_frame"],offset+shot["frames"]))
        if shot.get("bubble") and (take/"overlay.png").is_file():
            tags.append(shot["bubble"])
        offset += shot["frames"]
    if end_frames!=list(range(1344,1440)) or not tags:
        errors.append("最后 96 帧定版或气泡图层证据不足")
    return {"passed":not errors,"errors":errors,"job":jid,"film":str(delivery/"film.mp4"),
            "media":facts,"loudness":level,"prompt_ids":requests,"tags":tags,"studio_bundle":bundle}
