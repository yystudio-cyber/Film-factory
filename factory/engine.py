import hashlib
import importlib.util
import re
import secrets
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import dispatch, integrity, finishing, archive, crew, drafts, graphs, media, references, story, studio_import
from .common import Rejected, assets, disk_budget, read, write
from .ownership import StationOwner
from .director import DIMS, Director


class QualityExhausted(ValueError):
    """A candidate or master exhausted its configured quality budget."""


class Factory:
    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        self.owner = StationOwner(cfg)
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
                except QualityExhausted as err:
                    self.store.status(jid,"failed",str(err)[:2400],allow_pending_edit=True)
                except Exception as err:
                    self.store.status(jid, "failed", str(err)[:2400] or type(err).__name__)
            else:
                self.shutdown.wait(.4)

    def stage(self, title, actor):
        self.check(self.context[0])
        return self.store.stage(*self.context,title,actor)

    def produce(self, jid):
        self.owner.acquire(self.store)
        folder = self.store.root / jid
        spec = read(folder / "brief.json")
        profile = folder/"aesthetic-rules.json"
        if not spec.get("station_test") and (profile.exists() or self.cfg.get("aesthetic_rules")):
            if not profile.exists():
                write(profile,self.cfg["aesthetic_rules"])
            spec["aesthetic_rules"] = read(profile)
        original = self.cfg
        if original.get("station_contract"):
            from .ownership import queue_state
            pending = queue_state(original)
            if pending:
                clients = {rec.get("client_id") for root in ("takes","drafts")
                           for d in (folder/root).glob("*") if d.is_dir() and (rec:=dispatch.saved(d))}
                if any(len(row)<4 or not row[3].get("client_id") or row[3]["client_id"] not in clients for row in pending):
                    raise ValueError("8188 尚有外部或其他项目任务；未启动本片制作")
        self.cfg = self.director.cfg = studio_import.project_config(original,spec)
        if original.get("station_contract") and not spec.get("studio_bundle"):
            # Unrelated filenames and their sidecars are private. Plain screenplay users
            # opt in by naming a file, imported projects already have a frozen catalog.
            text = spec["script"] + "\n" + spec["requirements"]
            self.cfg = self.director.cfg = {**self.cfg, "asset_allowlist":[
                name for name in assets(self.cfg) if name in text]}
        try:
            from .gpu_usage import sampling
            with sampling(self.cfg,folder):
                self._produce(jid,folder,spec)
        finally:
            self.cfg = self.director.cfg = original

    def _produce(self,jid,folder,spec):
        disk_budget(self.cfg,spec["seconds"])
        placement = None
        if self.cfg.get("station_contract"):
            from .acceptance import _comfy, _gpu_placement
            compositor = _comfy(self.cfg)
            placement = _gpu_placement(self.cfg)
            write(folder/"gpu-placement.json",{"comfy":compositor,"physical":placement})
            if placement["errors"] or compositor.get("placement_errors"):
                raise ValueError("GPU 配置与工作站不符："+"；".join(
                    placement["errors"]+compositor.get("placement_errors",[])))
        planfile = folder / "plan.json"
        clip_refs = {} if spec.get("station_test") else archive.references(self.store, spec["script"], spec["requirements"])
        self.context = (jid, 0, "")
        self.director.stage = self.stage
        plan = read(planfile) if planfile.exists() else None
        if not plan or "coverage" not in plan:
            working = dict(spec)
            for draft in range(2):
                self.check(jid)
                if not plan:
                    self.store.event(jid, "导演正在拆镜，校验总时长与素材引用")
                    plan = studio_import.apply_links(spec,self.director.plan(working, clip_refs))
                try:
                    plan = finishing.plan_layers(spec,plan)
                except ValueError as err:
                    if draft or planfile.exists():
                        raise
                    self.store.event(jid,"按原文修正后期图层："+str(err))
                    working["graphics_correction"] = str(err)
                    plan = None
                    continue
                write(folder/"proposed-plan.json",plan)
                ledger = story.coverage(self.director, spec, plan)
                write(folder/f"coverage-draft-{draft+1}.json",ledger)
                write(folder/"coverage-latest.json",ledger)
                missing = [r for r in ledger if not r["covered"]]
                timing = []
                if not missing:
                    for number,shot in enumerate(plan["shots"],1):
                        if shot["narration"]:
                            with self.stage(f"试读分镜旁白 S{number:02}","Windows TTS / CPU"):
                                try:
                                    media.prepare_voice(shot,folder)
                                except media.VoiceTimingError as err:
                                    timing.append({"shot":number,"line":shot["narration"],"problem":str(err)})
                if not missing and not timing:
                    plan["coverage"] = ledger
                    write(planfile,plan)
                    break
                reason = "；".join([r["id"]+" "+r["notes"] for r in missing]
                                 +[f'S{r["shot"]:02} {r["problem"]}' for r in timing])
                if draft or planfile.exists():
                    raise ValueError("送入 GPU 前剧情核对失败：" + reason)
                self.store.event(jid,"按覆盖表或试读结果重新拆镜：" + reason)
                working.update(coverage_corrections=missing,narration_timing_corrections=timing)
                plan = None
        catalog = references.snapshot(folder,assets(self.cfg) | clip_refs,
                                      [name for s in plan["shots"] for name in references.used(s)])
        routes = {"FLF" if s["end"] else "I2V" if s["start"] else "T2V" for s in plan["shots"]}
        for route in routes:
            graphs.load_profile(self.cfg, route)
        draft_routes={"FLF" if s["end"] else "I2V" if s["start"] else "T2V"
                      for s in plan["shots"] if drafts.requested(spec,s)}
        for route in draft_routes:
            graphs.load_profile(drafts.settings(self.cfg),route)
        if self.cfg.get("asr","required")=="required" and any(s["dialogue"] for s in plan["shots"]):
            if not self.cfg.get("asr_model") or not Path(self.cfg["asr_model"]).is_dir() or not importlib.util.find_spec("faster_whisper"):
                raise ValueError("同期对白验收需要本地 faster-whisper 软件包及 asr_model 模型目录")
        if any(s["title"] or s.get("caption") or s.get("endcard") or s.get("bubble") for s in plan["shots"]) and not Path(self.cfg["font"]).is_file():
            raise ValueError("缺少后期排字所需的 font 字体文件")
        for s in plan["shots"]:
            for name in (s["start"], s["end"], s["overlay"]):
                if name and name != "previous" and name not in catalog:
                    raise ValueError("分镜引用的素材已移走：" + name)
        self.director.check_vision()
        if "crew" not in plan:
            plan["crew"] = crew.prepare(self.director,spec,plan,folder)
            write(planfile,plan)
        # GPU0 prepares one optional score while GPU1 renders sequential continuity shots.
        parallel = (self.cfg.get("parallel_score",True) and spec["mode"]=="auto" and spec["music"]
                    and any(not media.silent_shot(s) for s in plan["shots"]))
        if parallel and placement is not None:
            parallel = all(placement["services"].get(name,{}).get("gpu_indices")==[card]
                           for name,card in (("ace",0),("comfy",1)))
        stop_score = threading.Event()
        with ThreadPoolExecutor(max_workers=1,thread_name_prefix="score") as pool:
            score = pool.submit(self._score,jid,spec,plan,folder,stop_score) if parallel else None
            try:
                self._shoot(jid,spec,plan,folder,clip_refs,score)
            finally:
                stop_score.set()

    def _score(self,jid,spec,plan,folder,stop=None):
        def checkpoint():
            self.check(jid)
            if stop and stop.is_set():
                raise InterruptedError("本次生产已结束，保留已提交的配乐回执")
        checkpoint()
        file = folder/"score.wav"
        if file.exists() and (folder/"score-ready.json").exists():
            return file
        with self.store.stage(jid,0,"","生成配乐","ACE-Step / GPU0"):
            file = media.select_music(self.cfg,plan["music"],spec["seconds"],folder,checkpoint)
            write(folder/"score-ready.json",{"seconds":spec["seconds"]})
        return file

    def _shoot(self,jid,spec,plan,folder,clip_refs,score):
        while True:
            self.check(jid)
            if score and score.done():
                score.result()
            while self.store.revisions(jid):
                pass
            takes = self.store.takes(jid)
            selected = {t["shot"]: t for t in takes if t["selected"]}
            idx = next((i for i in range(1, len(plan["shots"])+1) if i not in selected), None)
            if idx is None:
                self.context = (jid,0,"")
                if self.finish(jid, spec, plan, selected, folder,score):
                    return
                continue
            self.context = (jid,idx,"")
            shot = dict(plan["shots"][idx-1])
            catalog = references.snapshot(folder,assets(self.cfg) | clip_refs,[])
            history = self.store.instructions(jid,idx)
            notes = [r["note"] for r in history]
            if notes:
                if not spec.get("station_test"):
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
            shot["revision_history"] = [{"origin":r.get("origin","legacy"),"note":r["note"]} for r in history]
            shot["departments"] = (crew.prepare(self.director,spec,plan,folder,revision=(idx,shot))[str(idx)]
                                   if notes else plan["crew"][str(idx)])
            shot["edit_epoch"] = max((v for k,v in self.store.edit_epochs(jid).items() if k<=idx),default=0)
            previous = Path(selected[idx-1]["path"]) if idx>1 else None
            if previous:
                self.store.verify_take(selected[idx-1],("tail.png","cut.mp4","shot.json","frame-4.jpg"))
            start_name = shot["start"]
            if start_name == "previous" and previous:
                previous_design = read(previous/"shot.json")
                # Carry the original reference through a chain. Old shots may lack this field.
                start_name = previous_design.get("root_reference", previous_design.get("start",""))
            shot["root_reference"] = start_name if start_name in catalog else ""
            if idx>1 and shot["start"]=="previous" and not (previous/"tail.png").is_file():
                raise ValueError("上一镜采用版本缺少剪点尾帧，禁止改取临时候选")
            shot["previous_code"] = f"C{selected[idx-1]['code']:03}" if previous else ""
            first = Path(selected[idx-1]["path"]) / "tail.png" if shot["start"] == "previous" else catalog.get(shot["start"])
            last = catalog.get(shot["end"])
            shot["reference_evidence"] = {role:{"name":name,"sha256":hashlib.sha256(p.read_bytes()).hexdigest()}
                    for role,name,p in (("start",shot["previous_code"] if shot["start"]=="previous" else shot["start"],first),
                                        ("end",shot["end"],last)) if p}
            graphs.load_profile(self.cfg, "FLF" if last else "I2V" if first else "T2V")
            choices = [t for t in takes if t["shot"] == idx and t["eligible"]]
            target = history[-1]["variants"] if history else self.store.job(jid)["variants"]
            interrupted_by_edit = False
            cast_refs = references.cast_anchors(selected,idx,shot.get("subjects",[]),self.store.verify_take)
            shot["cast_sources"] = {name:code for name,code,_ in cast_refs}
            for variant in range(len(choices), target):
                choice = self.candidate(jid, spec, plan, shot, idx, variant, notes, first, last, catalog, folder, previous, cast_refs)
                if choice is None:
                    interrupted_by_edit = True
                    break
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

    def candidate(self, jid, spec, plan, shot, idx, variant, notes, first, last, catalog, folder, previous, cast_refs=()):
        feedback = list(notes)
        if variant:
            feedback.append("Alternative staging and camera interpretation; preserve all explicit requirements.")
        design = shot
        pattern = f"S{idx:02}-V{variant+1}-"
        intent = {"notes":notes,"previous":shot["previous_code"],"beat":shot["beat"],
                  "start":shot["start"],"end":shot["end"]}
        if shot.get("edit_epoch"):
            intent["edit_epoch"] = shot["edit_epoch"]
        dispatch.check_working_state(folder,pattern,intent)
        prior_rejections = [p for p in (folder/"takes").glob(pattern+"*")
                            if (p/"rejected.json").exists() and (p/"intent.json").exists()
                            and read(p/"intent.json")==intent]
        for rejected in sorted(prior_rejections,key=lambda p:p.stat().st_mtime):
            feedback.append(read(rejected/"rejected.json")["reason"])
        for attempt in range(1+len(prior_rejections), self.cfg["attempts"]+1):
            shot = dict(design)
            self.check(jid)
            disk_budget(self.cfg)
            registered = {Path(t["path"]) for t in self.store.takes(jid)}
            old = sorted((folder/"takes").glob(pattern+"*"),key=lambda p:p.stat().st_mtime,reverse=True)
            unfinished = [d for d in old if d not in registered and (d/"intent.json").is_file()
                          and read(d/"intent.json")==intent and not (d/"rejected.json").is_file()]
            directory = unfinished[0] if unfinished else None
            if directory and ((directory/"remote-error.json").exists() or
                              (drafts.folder_for(directory)/"remote-error.json").exists()):
                raise ValueError("同一制作意图已有远端失败回执；请检查原错误并显式修改该镜后再投递")
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
            try:
                if shot["narration"]:
                    with self.stage("试读旁白并核对镜头时长","Windows TTS / CPU"):
                        media.prepare_voice(shot,folder,directory)
                if drafts.requested(spec,shot):
                    drafts.screen(self,jid,spec,plan,shot,first,last,seed,directory,catalog,cast_refs)
                with self.stage("生成镜头 · "+route, "ComfyUI / GPU1"):
                    source = graphs.render(self.cfg, shot, first, last, seed, directory, lambda: self.check(jid))
                audible = bool(shot["dialogue"]) or shot["sound"].strip().lower() not in {"silence", "silent", "静音"}
                with self.stage("媒体质检", "FFmpeg / CPU"):
                    write(directory/"technical.json", media.technical(source, self.cfg, shot["frames"], audible=audible))
                signals = media.temporal_signals(source)
                write(directory/"motion.json",signals)
                shot["motion_signals"] = signals
                if shot["dialogue"]:
                    from .speech import screen_dialogue, verify_speech
                    with self.stage("同期对白回听" if self.cfg.get("asr")!="off" else "记录对白未回听","声音后期 / CPU"):
                        speech = screen_dialogue(self.cfg,source,shot,directory,attempt==self.cfg["attempts"])
                        speech.setdefault("status","verified")
                        write(directory/"shot.json",dict(shot,seed=seed))
                        write(directory/"speech.json",speech)
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
                for name,code,frame in cast_refs:
                    refs.append(frame)
                    roles.append(f"earliest approved appearance of {name} in {code}; compare identity")
                roles += ["start", "quarter", "middle", "three quarters", "last used frame"]
                if self.cfg.get("motion_review", "adaptive" if self.cfg.get("station_contract") else "basic") == "adaptive":
                    from .motion import risks, motion_boards
                    reasons = risks(shot, signals, attempt > 1)
                    if reasons:
                        with self.stage("高风险运动过程检查","FFmpeg / CPU"):
                            boards, evidence = motion_boards(source, shot["frames"], directory)
                        shot["motion_evidence"] = dict(evidence, reasons=reasons)
                        write(directory/"motion-evidence.json",shot["motion_evidence"])
                        pics.extend(boards)
                        roles.extend("dense temporal samples; numbered native frames" for _ in boards)
                review = self.director.review(spec, plan, shot, refs + pics, roles)
                write(directory/"review.json", review)
                if not review["passed"]:
                    failed = [c["evidence"] for c in review["checks"] if c["result"] == "failed"]
                    failed.extend(row["evidence"] for row in review.get("aesthetic_checks",[]) if not row["passed"])
                    failed.extend(row["evidence"] for row in review.get("motion_checks",[]) if not row["passed"])
                    failed.extend(row["evidence"] for row in review.get("pulse_checks",[]) if row["decision"]!="motivated")
                    raise Rejected("；".join([review["notes"],review["repair"],*failed]))
                with self.stage("裁切与图文后期", "FFmpeg / CPU"):
                    cut = media.conform(self.cfg, source, shot, catalog.get(shot["overlay"]), directory)
                    if shot.get("dubbed_dialogue") and self.cfg.get("asr")!="off":
                        checked = verify_speech(self.cfg,cut,shot["dialogue"],shot["seconds"])
                        write(directory/"speech.json",dict(checked,status="verified",fallback="dub",native_review=speech))
                self.check(jid)
                return {"code": self.store.register(jid,idx,directory,files=integrity.seal_take(directory)), "path": str(directory)}
            except Rejected as err:
                self.store.event(jid, f"{label} 未通过：{err}")
                feedback.append(str(err))
                write(directory/"rejected.json", {"reason": str(err)})
        reason = f"S{idx:02} 达到返工上限；最后原因：{feedback[-1]}"
        if self.store.handle_rejection(jid,reason):
            return None
        raise QualityExhausted(reason)

    def finish(self, jid, spec, plan, selected, folder,score=None):
        disk_budget(self.cfg,spec["seconds"])
        self.store.event(jid, "正在合成与验收成片")
        timeline, clips = [], []
        for i, shot in enumerate(plan["shots"], 1):
            take = selected[i]
            self.store.verify_take(take)
            path = Path(take["path"])
            shot = read(path/"shot.json")
            timeline.append(dict({k:v for k,v in shot.items() if k != "prompt"}, shot=i, code=f"C{take['code']:03}", file=str((path/"cut.mp4").relative_to(folder)),
                                 motion_evidence=read(path/"motion-evidence.json") if (path/"motion-evidence.json").is_file() else None,
                                 motion_checks=read(path/"review.json").get("motion_checks",[]),
                                 pulse_checks=read(path/"review.json").get("pulse_checks",[]),
                                 observations=read(path/"review.json")["notes"],
                                 checks=read(path/"review.json").get("checks",[]),
                                 aesthetic_checks=read(path/"review.json").get("aesthetic_checks",[]),
                                 delivery_voice=(read(path/"speech.json").get("fallback","native")
                                                 if (path/"speech.json").is_file() else "none"),
                                 speech_review=(read(path/"speech.json").get("status","verified")
                                                if (path/"speech.json").exists() else "not_applicable"),
                                 transcript=read(path/"speech.json")["transcript"] if (path/"speech.json").exists() else ""))
            clips.append((path/"cut.mp4", shot["seconds"]))
        write(folder/"timeline.json", timeline)
        musicfile = None
        audible = any(not media.silent_shot(s) for s in timeline)
        if spec["music"] and audible:
            musicfile = score.result() if score else self._score(jid,spec,plan,folder)
        self.check(jid)
        score_digest=integrity.sha(musicfile) if musicfile else None
        with self.stage("剪辑与混音", "FFmpeg / CPU"):
            output = media.assemble(clips, musicfile, spec["seconds"], folder, timeline=timeline, cfg=self.cfg)
        from .speech import review_mix
        master_digest=integrity.sha(output)
        mix_lines = review_mix(self.cfg,output,timeline)
        first_mix = mix_lines
        if musicfile and any(r["status"]=="failed" for r in mix_lines):
            affected={r["shot"] for r in mix_lines if r["status"]=="failed"}
            for shot in timeline:
                if shot["shot"] in affected:
                    shot["reduce_score_for_line"]=True
            write(folder/"timeline.json",timeline)
            with self.stage("对白混音返修（仅配乐）","FFmpeg / CPU"):
                output=media.assemble(clips,musicfile,spec["seconds"],folder,timeline=timeline,cfg=self.cfg)
                master_digest=integrity.sha(output)
                mix_lines=review_mix(self.cfg,output,timeline)
        facts = media.technical(output, self.cfg, spec["seconds"]*24, master=True, audible=audible)
        boards = media.contact_sheets(output, timeline, folder)
        review = self.director.final_review(spec, timeline, boards)
        failed_line = next((r for r in mix_lines if r["status"]=="failed"),None)
        verdicts = story.audit_edit(self.director, plan["coverage"], timeline, review)
        visual_review = dict(review)
        issues = [r for r in verdicts if not r["passed"]]
        if issues and review["passed"]:
            review = dict(passed=False,notes=issues[0]["notes"],repair_shot=issues[0].get("repair_shot"))
        if failed_line and review["passed"]:
            review = dict(passed=False,notes="S%02d %s" % (failed_line["shot"],failed_line["reason"]),repair_shot=None)
        continuity = [{"shot":s["shot"],"code":s["code"],"action":s["beat"],"cast":s.get("subjects",[]),
                       "references":s.get("reference_evidence",{}),"previous":s.get("previous_code"),
                       "space_props_light":s.get("departments",{}).get("camera",{}).get("continuity"),
                       "sound_bridge":s.get("departments",{}).get("sound",{}).get("bridge"),
                       "edit_point":s.get("departments",{}).get("camera",{}).get("edit_point"),
                       "verdict":s["observations"]} for s in timeline]
        report = {"master_sha256":master_digest,"score_sha256":score_digest,"aesthetic_rules":spec.get("aesthetic_rules",[]),"continuity":continuity,"technical":facts,"production_times":self.store.production_times(jid),"audio_mix":read(folder/"audio-mix.json"),"visual":visual_review,"editorial":review,
                  "source_verdicts":verdicts,"shots":timeline}
        uncertain_mix = {r["shot"] for r in mix_lines if r["status"]=="unverified"}
        report["final_mix_dialogue"] = mix_lines
        if first_mix is not mix_lines:
            report["before_dialogue_mix_repair"] = first_mix
        report["sound_design_review"] = {"method":"final_aac_stereo_16khz_250ms_entry_middle_exit",
            "windows":media.sound_windows(output,timeline,folder),
            "semantic_listening":"unverified","cues":[{"shot":s["shot"],"direction":s["sound"],
            "bridge":s.get("departments",{}).get("sound",{}).get("bridge")} for s in timeline]}
        unverified = [s["code"] for s in timeline if s["speech_review"]=="unverified" or s["shot"] in uncertain_mix]
        report["dialogue_review"] = {"unverified":unverified,"note":"未做对白回听" if unverified else ""}
        report["qualification"] = ("failed" if not review["passed"] else
                                   "dialogue_unverified" if unverified else "passed")
        if (folder/"score-selection.json").is_file():
            report["score_selection"] = read(folder/"score-selection.json")
        self.check(jid)
        if not review["passed"]:
            write(folder/"acceptance.json",report)
            idx = review.get("repair_shot")
            history = folder/"editorial-repairs.json"
            prior = len(read(history)) if history.exists() else 0
            reason = "整片剧本验收未通过：" + review["notes"]
            repair = review if type(idx) is int and idx in selected else None
            if self.store.handle_rejection(jid,reason,review=repair,
                                           limit=self.cfg.get("editorial_repairs",1),legacy_repairs=prior):
                return False
            raise QualityExhausted(reason)
        for take in selected.values():
            self.store.verify_take(take,("cut.mp4",))
        return self.store.publish(jid,{i:t["code"] for i,t in selected.items()},output,timeline,report)
