"""Optional lower-cost LTX motion passes using separately bound single-stage graphs."""
import hashlib
import os
import re
import time

from . import graphs, media
from .common import Rejected, read, write


def requested(spec,shot):
    if not spec.get("render_drafts"):
        return False
    if spec.get("draft_policy","all")=="all":
        return True
    from .motion import risks
    return bool(risks(shot,{},False))


def settings(cfg):
    return dict(cfg,workflows=cfg["workflows"]/"draft",
                width=max(32,2*round(cfg["width"]/4)),height=max(32,2*round(cfg["height"]/4)))


def key_for(candidate):
    return hashlib.sha256(candidate.name.encode()).hexdigest()[:12]


def folder_for(candidate):
    return candidate.parent.parent/"drafts"/(candidate.name.split("-")[0]+"-"+key_for(candidate))


def _current(folder, meta, index):
    # A later revision to this or an earlier shot can invalidate the handoff.
    for n in range(1,index+1):
        file = folder/f"revisions-S{n:02}.json"
        if file.is_file() and file.stat().st_mtime_ns>meta["created_ns"]:
            return False
    return True


def latest(folder, index, epoch=0):
    paths = (folder/"drafts").glob(f"S{index:02}-*/meta.json")
    for path in sorted(paths,key=lambda p:p.stat().st_mtime_ns,reverse=True):
        data = read(path)
        if (path.parent/"preview.mp4").is_file() and data.get("edit_epoch",0)==epoch and _current(folder,data,index):
            return data
    return None


def video(folder,filename):
    if not re.fullmatch(r"S\d{2,3}-[a-f0-9]{12}\.mp4",filename):
        return None
    path = folder/"drafts"/filename[:-4]
    return path/"preview.mp4" if (path/"meta.json").is_file() and (path/"preview.mp4").is_file() else None


def screen(factory, jid, spec, plan, shot, first, last, seed, candidate, catalog, cast_refs=()):
    cfg = settings(factory.cfg)
    folder = folder_for(candidate)
    folder.mkdir(parents=True,exist_ok=True)
    write(folder/"owner.json",{"candidate":candidate.name})
    route = "FLF" if last else "I2V" if first else "T2V"
    if (folder/"review.json").exists():
        verdict = read(folder/"review.json")
        if verdict["passed"]:
            return
        raise Rejected("动态草稿退回："+verdict["notes"]+" "+verdict["repair"])
    with factory.stage("低分辨率动作草稿 · "+route,"ComfyUI / GPU1"):
        source = graphs.render(cfg,shot,first,last,seed,folder,lambda:factory.check(jid))
    with factory.stage("草稿时间线与抽帧","FFmpeg / CPU"):
        media.technical(source,cfg,shot["frames"],audible=False)
        frames = media.samples(source,shot,folder)
        temp = folder/"preview.tmp.mp4"
        media.ff("-i",source,"-t",str(shot["seconds"]),"-vf",f"crop={cfg['width']}:{cfg['height']},setsar=1",
                 "-map","0:v:0","-map","0:a?","-c:v","libx264","-preset","veryfast","-pix_fmt","yuv420p",
                 "-c:a","aac","-movflags","+faststart",temp)
        os.replace(temp,folder/"preview.mp4")
        refs, roles = [], []
        for role,path in [("first",first),("last",last),("identity",catalog.get(shot.get("root_reference"))),
                          *(("anchor",catalog.get(name)) for name in shot.get("anchors",[]))]:
            if path and path not in refs:
                refs.append(path)
                roles.append(role)
        for name,code,frame in cast_refs:
            refs.append(frame)
            roles.append(f"earliest approved appearance of {name} in {code}; compare identity")
        meta = dict(filename=folder.name+".mp4",seed=seed,seconds=shot["seconds"],width=cfg["width"],
                    height=cfg["height"],profile=route,edit_epoch=shot.get("edit_epoch",0),tried_at=time.time(),created_ns=time.time_ns(),status="审片中")
        write(folder/"meta.json",meta)
    task = dict(shot, pass_type="single-stage draft: judge staging and action, allow reduced detail")
    verdict = factory.director.review(spec,plan,task,refs+frames,roles+["opening","quarter","middle","three quarters","exit"])
    write(folder/"meta.json",dict(meta,status="通过" if verdict["passed"] else "退回",notes=verdict["notes"]))
    write(folder/"review.json",verdict)
    if not verdict["passed"]:
        raise Rejected("动态草稿退回："+verdict["notes"]+" "+verdict["repair"])
