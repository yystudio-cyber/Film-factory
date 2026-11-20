import difflib
import importlib.util
import re
import secrets
import threading
import uuid
from pathlib import Path

from . import archive, graphs, media, references, story
from .common import Rejected, assets, disk_budget, read, write
from .director import DIMS, Director


def verify_speech(cfg, path, expected):
    if not expected:
        return None
    from faster_whisper import WhisperModel
    if not cfg.get("asr_model"):
        raise ValueError("有同期台词的镜头需要配置本地 faster-whisper 的 asr_model")
    model = WhisperModel(cfg["asr_model"], device="cpu", compute_type="int8", local_files_only=True)
    parts, _ = model.transcribe(str(path), beam_size=3)
    actual = "".join(s.text for s in parts)
    def normalize(s):
        return "".join(ch for ch in s.casefold() if ch.isalnum())
    score = difflib.SequenceMatcher(None, normalize(expected), normalize(actual)).ratio()
    if score < cfg.get("speech_similarity", .8):
        raise Rejected(f"同期台词不符 ({score:.0%})：{actual}")
    return {"expected": expected, "transcript": actual, "similarity": score}


class Factory:
    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        self.director = Director(cfg)
        self.shutdown = threading.Event()

    def check(self, jid):
        if self.shutdown.is_set() or self.store.job(jid)["state"] != "running":
            raise InterruptedError("任务已停止")

    def worker(self):
        while not self.shutdown.is_set():
            try:
                disk_budget(self.cfg)
            except (ValueError,OSError):
                # Global disk pressure must not drain an entire overnight queue into failure.
                self.shutdown.wait(10)
                continue
            jid = self.store.claim()
            if jid:
                try:
                    self.produce(jid)
                except InterruptedError:
                    self.store.status(jid, "cancelled", "已停止")
                except Exception as err:
                    self.store.status(jid, "failed", str(err)[:2400] or type(err).__name__)
            else:
                self.shutdown.wait(.4)

    def stage(self, title, actor):
        self.check(self.context[0])
        return self.store.stage(*self.context,title,actor)

    def produce(self, jid):
        folder = self.store.root / jid
        spec = read(folder / "brief.json")
        disk_budget(self.cfg,spec["seconds"])
        planfile = folder / "plan.json"
        clip_refs = archive.references(self.store, spec["script"], spec["requirements"])
        self.context = (jid, 0, "")
        self.director.stage = self.stage
        plan = read(planfile) if planfile.exists() else None
        if not plan or "coverage" not in plan:
            working = dict(spec)
            for draft in range(2):
                self.check(jid)
                if not plan:
                    self.store.event(jid, "导演正在拆镜，校验总时长与素材引用")
                    plan = self.director.plan(working, clip_refs)
                write(folder/"proposed-plan.json",plan)
                ledger = story.coverage(self.director, spec, plan)
                write(folder/f"coverage-draft-{draft+1}.json",ledger)
                write(folder/"coverage-latest.json",ledger)
                missing = [r for r in ledger if not r["covered"]]
                if not missing:
                    plan["coverage"] = ledger
                    write(planfile,plan)
                    break
                reason = "；".join(r["id"]+" "+r["notes"] for r in missing)
                if draft or planfile.exists():
                    raise ValueError("送入 GPU 前剧情核对失败：" + reason)
                self.store.event(jid,"剧本有遗漏，重新拆镜：" + reason)
                working["coverage_corrections"] = missing
                plan = None
        catalog = references.snapshot(folder,assets(self.cfg) | clip_refs,
                                      [name for s in plan["shots"] for name in references.used(s)])
        routes = {"FLF" if s["end"] else "I2V" if s["start"] else "T2V" for s in plan["shots"]}
        for route in routes:
            graphs.load_profile(self.cfg, route)
        if any(s["dialogue"] for s in plan["shots"]):
            if not self.cfg.get("asr_model") or not Path(self.cfg["asr_model"]).is_dir() or not importlib.util.find_spec("faster_whisper"):
                raise ValueError("同期对白验收需要本地 faster-whisper 软件包及 asr_model 模型目录")
        if any(s["title"] or s.get("caption") for s in plan["shots"]) and not Path(self.cfg["font"]).is_file():
            raise ValueError("缺少后期排字所需的 font 字体文件")
        for s in plan["shots"]:
            for name in (s["start"], s["end"], s["overlay"]):
                if name and name != "previous" and name not in catalog:
                    raise ValueError("分镜引用的素材已移走：" + name)
        self.director.check_vision()
        while True:
            self.check(jid)
            while self.store.revisions(jid):
                pass
            takes = self.store.takes(jid)
            selected = {t["shot"]: t for t in takes if t["selected"]}
            idx = next((i for i in range(1, len(plan["shots"])+1) if i not in selected), None)
            if idx is None:
                self.context = (jid,0,"")
                if self.finish(jid, spec, plan, selected, folder):
                    return
                continue
            self.context = (jid,idx,"")
            shot = dict(plan["shots"][idx-1])
            catalog = references.snapshot(folder,assets(self.cfg) | clip_refs,[])
            revisions = folder / f"revisions-S{idx:02}.json"
            notes = [r["note"] for r in read(revisions)] if revisions.exists() else []
            if notes:
                catalog.update(archive.references(self.store, *notes))
                revision_file = folder/f"revised-S{idx:02}.json"
                saved = read(revision_file) if revision_file.exists() else {}
                if saved.get("notes") != notes:
                    revised = self.director.revise(shot, notes, catalog, idx == 1)
                    saved = {"notes": notes, "shot": revised}
                    write(revision_file, saved)
                shot = dict(saved["shot"])
                # Explicit clip identity takes precedence over the director's interpretation.
                for note in notes:
                    ref = re.search(r"(?:用|使用|use)\s*C(\d+)\s*(?:的)?\s*(?:尾帧|tail)", note, re.I)
                    if ref:
                        shot["start"] = f"C{int(ref[1]):03}"
            catalog = references.snapshot(folder,catalog,references.used(shot))
            shot["obligations"] = story.obligations(plan["coverage"],idx)
            shot["changes"] = notes
            previous = Path(selected[idx-1]["path"]) if idx>1 else None
            start_name = shot["start"]
            if start_name == "previous" and previous:
                previous_design = read(previous/"shot.json")
                # Carry the original reference through a chain. Old shots may lack this field.
                start_name = previous_design.get("root_reference", previous_design.get("start",""))
            shot["root_reference"] = start_name if start_name in catalog else ""
            shot["previous_code"] = f"C{selected[idx-1]['code']:03}" if previous else ""
            first = Path(selected[idx-1]["path"]) / "tail.png" if shot["start"] == "previous" else catalog.get(shot["start"])
            last = catalog.get(shot["end"])
            graphs.load_profile(self.cfg, "FLF" if last else "I2V" if first else "T2V")
            choices = [t for t in takes if t["shot"] == idx and t["eligible"]]
            target = self.store.job(jid)["variants"]
            interrupted_by_edit = False
            for variant in range(len(choices), target):
                choice = self.candidate(jid, spec, plan, shot, idx, variant, notes, first, last, catalog, folder, previous)
                choices.append(choice)
                with self.store.tx() as db:
                    interrupted_by_edit = bool(db.execute("SELECT 1 FROM notes WHERE job=?", (jid,)).fetchone())
                if interrupted_by_edit:
                    break
            if interrupted_by_edit:
                continue
            def quality(take):
                scores = read(Path(take["path"])/"review.json")["scores"]
                return sum(scores[k] for k in DIMS)
            if not self.store.advance(jid, idx, max(choices, key=quality)["code"]):
                return

    def candidate(self, jid, spec, plan, shot, idx, variant, notes, first, last, catalog, folder, previous):
        feedback = list(notes)
        if variant:
            feedback.append("Alternative staging and camera interpretation; preserve all explicit requirements.")
        for attempt in range(1, self.cfg["attempts"]+1):
            self.check(jid)
            disk_budget(self.cfg)
            pattern = f"S{idx:02}-V{variant+1}-"
            intent = {"notes":notes,"previous":shot["previous_code"],"beat":shot["beat"],
                      "start":shot["start"],"end":shot["end"]}
            registered = {Path(t["path"]) for t in self.store.takes(jid)}
            old = sorted((folder/"takes").glob(pattern+"*"),key=lambda p:p.stat().st_mtime,reverse=True)
            directory = next((d for d in old if d not in registered and (d/"intent.json").exists()
                              and read(d/"intent.json")==intent and (d/"remote.json").exists()
                              and not (d/"remote-error.json").exists() and not (d/"rejected.json").exists()),None)
            if directory:
                label = directory.name
                self.context = (jid,idx,label)
                cached = read(directory/"shot.json")
                seed, shot["prompt"] = cached["seed"], cached["prompt"]
                self.store.event(jid,f"S{idx:02} 核对已有任务与缓存，沿用原始种子")
            else:
                label = pattern+uuid.uuid4().hex[:10]
                directory = folder/"takes"/label
                directory.mkdir(parents=True)
                self.context = (jid,idx,label)
                self.store.event(jid,f"S{idx:02} 候选 {variant+1}，第 {attempt} 次制作")
                shot["prompt"] = self.director.prompt(spec,plan["bible"],shot,feedback)
                seed = secrets.randbelow(2**31)
                write(directory/"shot.json",dict(shot, seed=seed))
                write(directory/"intent.json",intent)
            route = "FLF" if last else "I2V" if first else "T2V"
            with self.stage("生成镜头 · "+route, "ComfyUI / GPU1"):
                source = graphs.render(self.cfg, shot, first, last, seed, directory, lambda: self.check(jid))
            try:
                audible = shot["sound"].strip().lower() not in {"silence", "silent", "静音"}
                with self.stage("媒体质检", "FFmpeg / CPU"):
                    write(directory/"technical.json", media.technical(source, self.cfg, shot["frames"], audible=audible))
                signals = media.temporal_signals(source)
                write(directory/"motion.json",signals)
                shot["motion_signals"] = signals
                speech = verify_speech(self.cfg, source, shot["dialogue"])
                if speech:
                    write(directory/"speech.json", speech)
                pics = media.samples(source, shot, directory)
                refs = [p for p in (first, last) if p]
                roles = (["start reference"] if first else []) + (["end reference"] if last else [])
                if previous:
                    prior = read(previous/"shot.json")
                    sheet = media.seam_sheet(previous/"cut.mp4",source,prior["seconds"],directory)
                    refs.append(sheet)
                    roles.append("edit boundary: top row final 8 frames sampled from previous cut, bottom row first 8 frames sampled from candidate")
                    adjacent = previous/"frame-4.jpg"
                    if adjacent.exists():
                        refs.append(adjacent)
                        roles.append("previous accepted cut exit; judge motivated transition, not pixel equality")
                for anchor in dict.fromkeys([shot["root_reference"], *shot.get("anchors",[])]):
                    if anchor and anchor in catalog and catalog[anchor] not in refs:
                        refs.append(catalog[anchor])
                        roles.append("canonical reference for identity/location: " + anchor)
                roles += ["start", "quarter", "middle", "three quarters", "last used frame"]
                review = self.director.review(spec, plan, shot, refs + pics, roles)
                write(directory/"review.json", review)
                if not review["passed"]:
                    failed = [c["evidence"] for c in review["checks"] if c["result"] == "failed"]
                    raise Rejected("；".join([review["notes"],review["repair"],*failed]))
                with self.stage("裁切与图文后期", "FFmpeg / CPU"):
                    media.conform(self.cfg, source, shot, catalog.get(shot["overlay"]), directory)
                self.check(jid)
                return {"code": self.store.register(jid, idx, directory), "path": str(directory)}
            except Rejected as err:
                self.store.event(jid, f"{label} 未通过：{err}")
                feedback.append(str(err))
                write(directory/"rejected.json", {"reason": str(err)})
        raise ValueError(f"S{idx:02} 达到返工上限；最后原因：{feedback[-1]}")

    def finish(self, jid, spec, plan, selected, folder):
        disk_budget(self.cfg,spec["seconds"])
        self.store.event(jid, "正在合成与验收成片")
        timeline, clips = [], []
        for i, shot in enumerate(plan["shots"], 1):
            take = selected[i]
            path = Path(take["path"])
            shot = read(path/"shot.json")
            timeline.append(dict({k:v for k,v in shot.items() if k != "prompt"}, shot=i, code=f"C{take['code']:03}", file=str((path/"cut.mp4").relative_to(folder)),
                                 observations=read(path/"review.json")["notes"],
                                 checks=read(path/"review.json").get("checks",[]),
                                 transcript=read(path/"speech.json")["transcript"] if (path/"speech.json").exists() else ""))
            clips.append((path/"cut.mp4", shot["seconds"]))
        write(folder/"timeline.json", timeline)
        musicfile = None
        if spec["music"]:
            musicfile = folder/"score.wav"
            if not musicfile.exists() or not (folder/"score-ready.json").exists():
                with self.stage("生成配乐", "ACE-Step / GPU0"):
                    musicfile = media.music(self.cfg, plan["music"], spec["seconds"], folder, lambda: self.check(jid))
                write(folder/"score-ready.json", {"seconds":spec["seconds"]})
        self.check(jid)
        with self.stage("剪辑与混音", "FFmpeg / CPU"):
            output = media.assemble(clips, musicfile, spec["seconds"], folder)
        audible = spec["music"] or any(s["narration"] or s["sound"].strip().lower() not in
                                      {"silence","silent","静音"} for s in timeline)
        facts = media.technical(output, self.cfg, spec["seconds"]*24, master=True, audible=audible)
        boards = media.contact_sheets(output, timeline, folder)
        review = self.director.final_review(spec, timeline, boards)
        verdicts = story.audit_edit(self.director, plan["coverage"], timeline, review)
        visual_review = dict(review)
        issues = [r for r in verdicts if not r["passed"]]
        if issues and review["passed"]:
            review = dict(passed=False,notes=issues[0]["notes"],repair_shot=issues[0].get("repair_shot"))
        write(folder/"acceptance.json", {"technical":facts,"visual":visual_review,"editorial":review,
                                        "source_verdicts":verdicts,"shots":timeline})
        if not review["passed"]:
            idx = review.get("repair_shot")
            history = folder/"editorial-repairs.json"
            repairs = read(history) if history.exists() else []
            if type(idx) is int and idx in selected and len(repairs) < self.cfg.get("editorial_repairs", 1):
                self.check(jid)
                self.store.command(jid, "revise", shot=idx, note=review["notes"])
                write(history, repairs+[review])
                return False
            raise ValueError("整片剧本验收未通过：" + review["notes"])
        with self.store.tx() as db:
            if db.execute("SELECT state FROM jobs WHERE id=?", (jid,)).fetchone()[0] != "running":
                raise InterruptedError()
            if db.execute("SELECT 1 FROM notes WHERE job=?", (jid,)).fetchone():
                return False
            output.replace(folder/"film.mp4")
            db.execute("UPDATE jobs SET state='done',detail='成片已通过设定的验收门槛' WHERE id=?", (jid,))
            self.store.log(db, jid, "已交付 film.mp4、timeline.json、acceptance.json")
        return True
