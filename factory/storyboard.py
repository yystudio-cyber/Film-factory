"""A frame-indexed review timeline built from the current project's own artifacts."""
import json
import re
from collections import defaultdict
from pathlib import Path

from .common import read
from . import drafts

FPS = 24
REF_FILE = re.compile(r"[0-9a-f]{64}\.(?:png|jpe?g)")
TAKE_DIRECTORY = re.compile(r"S[0-9]{2,3}-V[12]-[0-9a-f]{10}")


def _optional(path, default):
    return read(path) if path.is_file() else default


def _reference_path(folder, filename):
    if not isinstance(filename, str) or not REF_FILE.fullmatch(filename):
        return None
    base = (folder / "references").resolve()
    path = (base / filename).resolve()
    if base.parent != folder.resolve() or path.parent != base or not path.is_file():
        return None
    return path


def reference_file(store, jid, filename):
    store.job(jid)
    folder = store.root / jid
    index = _optional(folder / "reference-index.json", {})
    if not any(isinstance(r, dict) and r.get("file") == filename for r in index.values()):
        return None
    return _reference_path(folder, filename)


def _reference(jid, folder, index, role, name):
    record = index.get(name, {})
    filename = record.get("file")
    path = _reference_path(folder, filename)
    return {"role": role, "name": name,
            "url": f"/api/storyboard/{jid}/refs/{filename}" if path else None}


def eligible_versions(takes):
    grouped = defaultdict(list)
    for take in sorted(takes, key=lambda v: (not v["selected"], -v["code"])):
        if take["eligible"]:
            grouped[take["shot"]].append(take)
    return grouped


def current_designs(store, jid, plan, versions):
    folder = store.root / jid
    edit_epochs = store.edit_epochs(jid)
    epoch = 0
    for number, original in enumerate(plan.get("shots", []), 1):
        epoch = max(epoch, edit_epochs.get(number, 0))
        yield _state_design(store, jid, folder, number, original, versions[number], epoch), epoch


def _state_design(store, jid, folder, number, original, versions, epoch):
    if versions:
        file = Path(versions[0]["path"]) / "shot.json"
        if file.is_file():
            return read(file)
    # A queued revision can supersede an earlier revision file.
    revision = _optional(folder / f"revised-S{number:02}.json", {})
    if revision:
        notes = [r["note"] for r in store.instructions(jid, number)]
        if notes and revision.get("notes") == notes:
            # Once a revised take is in production its actual execution sheets and
            # resolved reference binding supersede the director's preliminary rewrite.
            attempts = (folder / "takes").glob(f"S{number:02}-V*-*/shot.json")
            for path in sorted(attempts, key=lambda p: p.stat().st_mtime_ns, reverse=True):
                shot = read(path)
                if shot.get("changes") == notes and shot.get("edit_epoch", 0) == epoch:
                    return shot
            return dict(revision["shot"], departments=revision["shot"].get("departments", {}))
    return original


def _view(design, number, jid, folder, index, selected, plan, cast_reference):
    references = []
    anchors = dict.fromkeys([design.get("root_reference"), *design.get("anchors", [])])
    for role, name in [("首帧", design.get("start")), ("尾帧", design.get("end")),
                       *(("身份 / 场景参考", name) for name in anchors
                         if name not in (design.get("start"), design.get("end")))]:
        if not name:
            continue
        if role == "首帧" and name == "previous":
            previous = selected.get(number - 1)
            directory = Path(previous["path"]) if previous else None
            url = None
            if directory and TAKE_DIRECTORY.fullmatch(directory.name) and (directory / "frame-4.jpg").is_file():
                url = f"/api/frames/{jid}/{directory.name}/4"
            label = f"C{previous['code']:03} 末帧" if previous else "等待上一镜采用"
            references.append({"role": "衔接首帧", "name": label, "url": url})
        else:
            references.append(_reference(jid, folder, index, role, name))
    references.extend(cast_reference(name, code) for name, code in design.get("cast_sources", {}).items())
    data = {field: design.get(field, "") for field in (
        "beat", "sound", "dialogue", "narration", "title", "caption", "overlay")}
    data["departments"] = design.get("departments", plan.get("crew", {}).get(str(number), {}))
    data["references"] = references
    return data


def snapshot(store, jid):
    job = store.job(jid)
    spec = json.loads(job["spec"])
    folder = store.root / jid
    ready = (folder / "plan.json").is_file()
    plan = _optional(folder / ("plan.json" if ready else "proposed-plan.json"), {})
    index = _optional(folder / "reference-index.json", {})
    takes = store.takes(jid)
    by_shot = eligible_versions(takes)
    by_code = {f"C{take['code']:03}": Path(take["path"]) for take in takes}
    identity_evidence = {}

    def cast_reference(name, code):
        directory = by_code.get(code)
        entry = {"role": "角色指认帧", "name": f"{name} · {code}", "url": None}
        if not directory or not TAKE_DIRECTORY.fullmatch(directory.name):
            return entry
        if code not in identity_evidence:
            subjects = _optional(directory / "review.json", {}).get("subjects", [])
            identity_evidence[code] = {row["name"]: row["frame"] for row in subjects}
        frame = identity_evidence[code].get(name)
        if type(frame) is int and 0 <= frame <= 4 and (directory / f"frame-{frame}.jpg").is_file():
            entry["name"] += f" · 抽帧 {frame + 1}/5"
            entry["url"] = f"/api/frames/{jid}/{directory.name}/{frame}"
        return entry

    selected = {number: rows[0] for number, rows in by_shot.items() if rows[0]["selected"]}

    coverage = defaultdict(list)
    ledger = plan.get("coverage", []) if ready else _optional(folder / "coverage-latest.json", [])
    for row in ledger:
        for check in row["checks"]:
            coverage[check["shot"]].append({"id": row["id"], "original": row["text"],
                                            "expect": check["expect"], "channel": check.get("channel")})

    shots = []
    start = 0
    for number, (design, epoch) in enumerate(current_designs(store, jid, plan, by_shot), 1):
        versions = by_shot[number]
        frames = int(design.get("frames") or round(float(design["seconds"]) * FPS))
        if frames <= 0:
            raise ValueError(f"S{number:02} 时长无效")
        def view(shot):
            return _view(shot, number, jid, folder, index, selected, plan, cast_reference)

        preferred_view = view(design)

        movies = []
        for take in versions:
            if (Path(take["path"]) / "cut.mp4").is_file():
                code = take["code"]
                movies.append({"kind": "adopted" if take["selected"] else "candidate", "code": code,
                               "label": ("已采用 " if take["selected"] else "候选 ") + f"C{code:03}",
                               "url": f"/api/jobs/{jid}/C{code}.mp4"})
                if take != versions[0]:
                    other = _optional(Path(take["path"]) / "shot.json", None)
                    if other:
                        alternate = view(other)
                        if alternate != preferred_view:
                            movies[-1]["design"] = alternate
        draft = drafts.latest(folder, number, epoch)
        if draft and not draft.get("obsolete"):
            movies.append({"kind": "draft", "status": draft.get("status", ""),
                           "label": f"渲染草稿 · {draft['width']}×{draft['height']} · {draft.get('status', '待审')}",
                           "url": f"/api/drafts/{jid}/{draft['filename']}"})

            for path in (folder / "takes").glob(f"S{number:02}-V*-*/shot.json"):
                if drafts.folder_for(path.parent).name + ".mp4" == draft["filename"]:
                    draft_view = view(read(path))
                    if draft_view != preferred_view:
                        movies[-1]["design"] = draft_view
                    break

        shots.append(dict(preferred_view, number=number, start_frame=start, end_frame=start+frames,
                          frames=frames, movies=movies, checks=coverage[number]))
        start += frames
    return {"id": jid, "title": spec["title"], "state": job["state"], "detail": job["detail"],
            "gate": job["gate"], "fps": FPS, "frames": start, "shots": shots,
            "ready": ready, "music": spec["music"], "planned_seconds": spec["seconds"],
            "master": f"/api/jobs/{jid}/film.mp4" if job["state"] == "done" else None}
