"""Offline, field-allowlisted metrics for closed run copies; never opens a service."""
import argparse
import json
import math
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from .common import read


def intervals(spans):
    total=0.0
    end=-math.inf
    for a,b in sorted(spans):
        total+=max(0,b-max(a,end))
        end=max(end,b)
    return total


def safe_output(path, value):
    with Path(path).open("x",encoding="utf8") as f:
        json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)


def export(runs, job, origin, case):
    if not re.fullmatch("[a-f0-9]{32}",job) or origin not in {"fixture","station"} or case not in {"six","pilot","other"}:
        raise ValueError("需要有效任务编号、来源及基准类型")
    root=Path(runs).resolve(strict=True)
    folder=root/job
    db=root/"factory.sqlite3"
    if not folder.is_dir() or not db.is_file() or (root/"factory.sqlite3-wal").exists():
        raise ValueError("需要已退出且完成 WAL checkpoint 的独立任务数据副本")
    with closing(sqlite3.connect(db.as_uri()+"?mode=ro&immutable=1",uri=True)) as con:
        con.row_factory=sqlite3.Row
        record=con.execute("SELECT created,state,detail FROM jobs WHERE id=?",(job,)).fetchone()
        if record is None or record["state"] not in {"done","cancelled","failed"}:
            raise ValueError("只统计终态试验，仍在运行的任务应继续留在原站")
        rows=con.execute("SELECT shot,take,title,started,ended,state,detail FROM stages WHERE job=? ORDER BY id",(job,)).fetchall()
        if record["state"]=="done":
            terminal="已交付 film.mp4、timeline.json、acceptance.json"
        elif record["state"]=="cancelled" and record["detail"]=="已停止调度":
            terminal="stop"
        else:
            terminal=record["detail"]
        # Old crash recovery can mark cancelled without recording a terminal timestamp.
        last=con.execute("SELECT max(ts) FROM events WHERE job=? AND message=?",(job,terminal)).fetchone()[0]
        ok={Path(r[0].replace(chr(92),"/")).name for r in con.execute("SELECT path FROM takes WHERE job=?",(job,))}
        release=con.execute("SELECT folder FROM releases WHERE job=? ORDER BY id DESC LIMIT 1",(job,)).fetchone()
    state=record["state"]
    start=record["created"]
    wall=last-start if last is not None and last>start else None
    spans=[]
    picture={}
    role_seconds={"picture":0.0,"draft":0.0,"score":0.0}
    oom=set()
    attempts={}
    for row in rows:
        a,b=row["started"],row["ended"]
        name=row["take"]
        if row["shot"] and re.fullmatch(r"S\d+-V\d+-[a-f0-9]+",name or ""):
            attempts.setdefault(name,(row["shot"],a))
        if b is None or b<a:
            continue
        if last is not None and min(b,last)>max(a,start):
            spans.append((max(a,start),min(b,last)))
        title=row["title"]
        lane=("picture" if title.startswith("生成镜头 · ") else "draft" if title.startswith("低分辨率动作草稿 · ") else "score" if title=="生成配乐" else "")
        if lane:
            role_seconds[lane]+=b-a
        if lane in {"picture","draft"}:
            picture[name]=picture.get(name,0.0)+(b-a)
        if lane and re.search(r"out of memory|cuda.{0,12}oom|显存不足",row["detail"] or "",re.I):
            oom.add(name or lane)
    plan=read(folder/"plan.json") if (folder/"plan.json").is_file() else {}
    durations=[s.get("seconds") for s in plan.get("shots",[])]
    if any(type(n) is not int or not 2<=n<=8 for n in durations):
        raise ValueError("任务分镜时长不完整")
    first={}
    rework=0.0
    rejected=0
    for name,(shot,_) in sorted(attempts.items(),key=lambda pair:pair[1][1]):
        fail=(folder/"takes"/name/"rejected.json").is_file()
        first.setdefault(shot,True if name in ok else False if fail else None)
        if fail:
            rework+=picture.get(name,0.0)
            rejected+=1
    final=folder/(release[0] if release else "")
    if not final.resolve().is_relative_to(folder.resolve()):
        raise ValueError("本地交付索引异常")
    qualified=0
    if state=="done":
        audit=read(final/"acceptance.json")
        timeline=read(final/"timeline.json")
        if audit.get("qualification")=="passed":
            qualified=sum(s["seconds"] for s in timeline)
            if qualified!=sum(durations):
                raise ValueError("验收时长与计划不一致")
    timing=[]
    for base in ("takes","drafts"):
        for ready in (folder/base).glob("*/source-ready.json"):
            values=read(ready).get("timing") or {}
            timing.append(values)
    waits=[r["submit_to_start_seconds"] for r in timing if type(r.get("submit_to_start_seconds")) in (int,float)]
    known=[v for v in first.values() if v is not None]
    gpupath=folder/"gpu-usage.json"
    sample=read(gpupath) if gpupath.is_file() else {}
    peaks={}
    for card in (() if sample.get("identity_changed") else ("0","1")):
        row=sample.get("devices",{}).get(card,{})
        if type(row.get("peak_mib")) is int and type(row.get("samples")) is int:
            peaks[card]={"peak_mib":row["peak_mib"],"samples":row["samples"]}
    return {"schema":1,"origin":origin,"case":case,"state":state,"shot_seconds":durations,
            "qualified_seconds":qualified,"wall_seconds":round(wall,3) if wall else None,
            "qualified_seconds_per_hour":round(3600*qualified/wall,3) if wall else None,
            "first_pass_shots":sum(known),"first_assessed_shots":len(known),"unknown_first_shots":len(first)-len(known),
            "first_pass_fraction":round(sum(known)/len(known),4) if known else None,
            "rejected_candidates":rejected,"rejected_picture_and_draft_wall_seconds":round(rework,3),
            "lane_wall_seconds":{k:round(v,3) for k,v in role_seconds.items()},
            "unattributed_wall_seconds":round(max(0,wall-intervals(spans)),3) if wall else None,
            "queue_observations":len(waits),"timed_remote_outputs":len(timing),
            "observed_submit_to_start_seconds":round(sum(waits),3) if waits else None,
            "oom_attributed_candidates":len(oom),"gpu_sampled":peaks}


def compare(a,b):
    if (a.get("schema"),a.get("origin"),a.get("case"),a.get("shot_seconds"))!=(
            b.get("schema"),b.get("origin"),b.get("case"),b.get("shot_seconds")) or a.get("schema")!=1:
        raise ValueError("只比较来源、标称测试和渲染段时长均一致的两次运行")
    if any(r.get("state") not in {"done","failed","cancelled"} for r in (a,b)):
        raise ValueError("缺少终态")
    metrics=("qualified_seconds_per_hour","wall_seconds","first_pass_fraction","rejected_picture_and_draft_wall_seconds")
    def numeric(x):
        if x is None:
            return None
        if type(x) not in (float,int) or not math.isfinite(x) or x<0:
            raise ValueError("输入指标须为非负有限数")
        return x
    def delta(x,y):
        x,y=numeric(x),numeric(y)
        return round(y-x,4) if x is not None and y is not None else None
    return {"schema":1,"delta_B_minus_A":{k:delta(a.get(k),b.get(k)) for k in metrics},
            "gpu_peak_mib_delta":{k:delta(a["gpu_sampled"].get(k,{}).get("peak_mib"),
                                         b["gpu_sampled"].get(k,{}).get("peak_mib")) for k in ("0","1")}}


def main():
    p=argparse.ArgumentParser(description="从已关闭的副本离线导出无素材、无自由文本的统计")
    commands=p.add_subparsers(dest="command",required=True)
    save=commands.add_parser("export")
    save.add_argument("--runs",type=Path,required=True)
    save.add_argument("--job",required=True)
    save.add_argument("--origin",choices=("station","fixture"),required=True)
    save.add_argument("--case",choices=("six","pilot","other"),required=True)
    comp=commands.add_parser("compare")
    comp.add_argument("a",type=Path)
    comp.add_argument("b",type=Path)
    for command in (save,comp):
        command.add_argument("--out",type=Path,required=True)
    opt=p.parse_args()
    if opt.command=="export":
        if opt.out.resolve().is_relative_to(opt.runs.resolve()):
            p.error("输出必须在运行数据副本之外")
        doc=export(opt.runs,opt.job,opt.origin,opt.case)
    else:
        doc=compare(read(opt.a),read(opt.b))
    safe_output(opt.out,doc)


if __name__=="__main__":
    main()
