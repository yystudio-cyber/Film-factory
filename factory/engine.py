import difflib
import importlib.util
import re
import secrets
import threading
import uuid
from pathlib import Path

from . import archive, graphs, media
from .common import Rejected, assets, read, write
from .director import Director


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

    def produce(self, jid):
        folder = self.store.root / jid
        spec = read(folder / "brief.json")
        planfile = folder / "plan.json"
        clip_refs = archive.references(self.store, spec["script"], spec["requirements"])
        if not planfile.exists():
            self.store.event(jid, "导演正在拆镜，校验总时长与素材引用")
            write(planfile, self.director.plan(spec, clip_refs))
        plan = read(planfile)
        catalog = assets(self.cfg) | clip_refs
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
        while True:
            self.check(jid)
            while self.store.revisions(jid):
                pass
            takes = self.store.takes(jid)
            selected = {t["shot"]: t for t in takes if t["selected"]}
            idx = next((i for i in range(1, len(plan["shots"])+1) if i not in selected), None)
            if idx is None:
                if self.finish(jid, spec, plan, selected, folder):
                    return
                continue
            shot = dict(plan["shots"][idx-1])
            catalog = assets(self.cfg) | clip_refs
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
            first = Path(selected[idx-1]["path"]) / "tail.png" if shot["start"] == "previous" else catalog.get(shot["start"])
            last = catalog.get(shot["end"])
            graphs.load_profile(self.cfg, "FLF" if last else "I2V" if first else "T2V")
            choices = [t for t in takes if t["shot"] == idx and t["eligible"]]
            target = self.store.job(jid)["variants"]
            interrupted_by_edit = False
            for variant in range(len(choices), target):
                choice = self.candidate(jid, spec, plan, shot, idx, variant, notes, first, last, catalog, folder)
                choices.append(choice)
                with self.store.tx() as db:
                    interrupted_by_edit = bool(db.execute("SELECT 1 FROM notes WHERE job=?", (jid,)).fetchone())
                if interrupted_by_edit:
                    break
            if interrupted_by_edit:
                continue
            def quality(take):
                return sum(read(Path(take["path"])/"review.json")["scores"].values())
            if not self.store.advance(jid, idx, max(choices, key=quality)["code"]):
                return

    def candidate(self, jid, spec, plan, shot, idx, variant, notes, first, last, catalog, folder):
        feedback = list(notes)
        if variant:
            feedback.append("Alternative staging and camera interpretation; preserve all explicit requirements.")
        for attempt in range(1, self.cfg["attempts"]+1):
            self.check(jid)
            label = f"S{idx:02}-V{variant+1}-{uuid.uuid4().hex[:10]}"
            directory = folder / "takes" / label
            directory.mkdir(parents=True)
            self.store.event(jid, f"S{idx:02} 候选 {variant+1}，第 {attempt} 次制作")
            shot["prompt"] = self.director.prompt(spec, plan["bible"], shot, feedback)
            seed = secrets.randbelow(2**31)
            write(directory/"shot.json", dict(shot, seed=seed))
            source = graphs.render(self.cfg, shot, first, last, seed, directory, lambda: self.check(jid))
            try:
                audible = shot["sound"].strip().lower() not in {"silence", "silent", "静音"}
                write(directory/"technical.json", media.technical(source, self.cfg, shot["frames"], audible=audible))
                speech = verify_speech(self.cfg, source, shot["dialogue"])
                if speech:
                    write(directory/"speech.json", speech)
                pics = media.samples(source, shot, directory)
                refs = [p for p in (first, last) if p]
                roles = (["start reference"] if first else []) + (["end reference"] if last else [])
                roles += ["start", "quarter", "middle", "three quarters", "last used frame"]
                review = self.director.review(spec, plan, shot, refs + pics, roles)
                write(directory/"review.json", review)
                if not review["passed"]:
                    raise Rejected(review["notes"] + " " + review["repair"])
                media.conform(self.cfg, source, shot, catalog.get(shot["overlay"]), directory)
                self.check(jid)
                return {"code": self.store.register(jid, idx, directory), "path": str(directory)}
            except Rejected as err:
                self.store.event(jid, f"{label} 未通过：{err}")
                feedback.append(str(err))
                write(directory/"rejected.json", {"reason": str(err)})
        raise ValueError(f"S{idx:02} 达到返工上限；最后原因：{feedback[-1]}")

    def finish(self, jid, spec, plan, selected, folder):
        self.store.event(jid, "正在合成与验收成片")
        timeline, clips = [], []
        for i, shot in enumerate(plan["shots"], 1):
            take = selected[i]
            path = Path(take["path"])
            shot = read(path/"shot.json")
            timeline.append(dict({k:v for k,v in shot.items() if k != "prompt"}, shot=i, code=f"C{take['code']:03}", file=str((path/"cut.mp4").relative_to(folder)),
                                 observations=read(path/"review.json")["notes"]))
            clips.append((path/"cut.mp4", shot["seconds"]))
        write(folder/"timeline.json", timeline)
        musicfile = None
        if spec["music"]:
            musicfile = folder/"score.wav"
            if not musicfile.exists() or not (folder/"score-ready.json").exists():
                musicfile = media.music(self.cfg, plan["music"], spec["seconds"], folder, lambda: self.check(jid))
                write(folder/"score-ready.json", {"seconds":spec["seconds"]})
        self.check(jid)
        output = media.assemble(clips, musicfile, spec["seconds"], folder)
        audible = spec["music"] or any(s["narration"] or s["sound"].strip().lower() not in
                                      {"silence","silent","静音"} for s in timeline)
        facts = media.technical(output, self.cfg, spec["seconds"]*24, master=True, audible=audible)
        boards = media.contact_sheets(output, timeline, folder)
        review = self.director.final_review(spec, timeline, boards)
        write(folder/"acceptance.json", {"technical": facts, "editorial": review, "shots": timeline})
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
