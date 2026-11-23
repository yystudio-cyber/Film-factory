from pathlib import Path
from .common import read
from . import storyboard


def optional(folder, filename, default=None):
    file = folder/filename
    return read(file) if file.is_file() else default


def snapshot(store, jid, shot):
    job = store.job(jid)
    folder = store.root/jid
    plan = optional(folder,"plan.json",{})
    if not plan:
        plan = optional(folder,"proposed-plan.json",{})
        plan["coverage"] = optional(folder,"coverage-latest.json",[])
    stages = store.stages(jid)
    takes = {Path(t["path"]).name:t for t in store.takes(jid)}
    selected = {t["shot"]:t["code"] for t in takes.values() if t["selected"]}
    if shot is None:
        shot = (stages[0]["shot"] if stages else 0) or job["gate"] or 1
    versions = storyboard.eligible_versions(takes.values())
    designs = [shot for shot, _ in storyboard.current_designs(store, jid, plan, versions)]
    if not 1 <= shot <= max(1,len(designs)):
        raise ValueError("镜号不在计划内")
    attempts = []
    for directory in sorted((folder/"takes").glob(f"S{shot:02}-V*-*"),key=lambda d:d.stat().st_mtime,reverse=True):
        take = takes.get(directory.name,{})
        meta = optional(directory,"shot.json",{})
        attempts.append({"id":directory.name,"code":take.get("code"),"selected":bool(take.get("selected")),
                         "eligible":bool(take.get("eligible")),"design":meta,
                         "seam":(directory/"seam.jpg").is_file(),"motion":optional(directory,"motion.json",{}),
                         "ticket":optional(directory,"remote.json",{}).get("prompt_id"),
                         "speech":optional(directory,"speech.json"),
                         "tech":optional(directory,"technical.json"),"review":optional(directory,"review.json"),
                         "rejection":optional(directory,"rejected.json"),
                         "frames":[i for i in range(5) if (directory/f"frame-{i}.jpg").is_file()]})
    # A rerender must not show an earlier master's verdict as the current result.
    audit = optional(store.delivery_dir(jid) or folder,"acceptance.json",{}) if job["state"]=="done" or (job["state"]=="failed" and "整片剧本验收未通过" in job["detail"]) else {}
    return {"state":job["state"],"detail":job["detail"],"shot":shot,"selected":selected,
            "shots":designs,"stages":stages,"attempts":attempts,"coverage":plan.get("coverage",[]),
            "production_times":audit.get("production_times") or store.production_times(jid),
            "audio_mix":audit.get("audio_mix"),"technical":audit.get("technical"),"source_verdicts":audit.get("source_verdicts",[])}
