import json
import os
import re
import time
import secrets
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from contextlib import nullcontext

from .common import ServiceHTTPError, assets, image_url, request

DIMS = ("story", "composition", "identity", "continuity", "artifacts")


class Director:
    def __init__(self, cfg):
        self.cfg = cfg

    def endpoint(self, vision):
        dual = self.cfg.get("text_provider") == "deepseek" or (
            "text_provider" not in self.cfg and os.environ.get("FILM_DS_BASE"))
        if dual and not vision:
            base = os.environ.get("FILM_DS_BASE") or self.cfg.get("ds_base")
            model = os.environ.get("FILM_DS_MODEL") or self.cfg.get("ds_model")
            if not base or not model:
                raise ValueError("请设置 DeepSeek 实际 API 基址和模型 ID（FILM_DS_BASE、FILM_DS_MODEL）")
            return base, model, "FILM_DS_KEY", True
        return self.cfg["llm"], self.cfg["model"], "FILM_LLM_KEY", False

    def ask(self, instruction, data, images=(), tokens=2500, task="文本导演"):
        base, model, env, deepseek = self.endpoint(bool(images))
        key = os.environ.get(env)
        local = urlsplit(base).hostname in {"localhost","127.0.0.1","::1"}
        if not local and self.cfg.get("allow_hosted_models") is not True:
            raise ValueError("托管导演尚未授权。请核准本次素材范围和模型费用，或配置本机视觉语言模型")
        if not key and not local:
            raise ValueError("缺少模型密钥环境变量 " + env)
        payload = json.dumps(data, ensure_ascii=False)
        content = [{"type":"text","text":payload}]
        content.extend({"type":"image_url","image_url":{"url":image_url(p)}} for p in images)
        body = {"model":model, "temperature":0.25, "max_tokens":tokens,
                "messages":[{"role":"system","content":instruction + " Return one JSON object only."},
                            {"role":"user","content":payload if deepseek else content}]}
        if deepseek and self.cfg.get("ds_json", True):
            body["response_format"] = {"type":"json_object"}
        if deepseek and self.cfg.get("ds_thinking"):
            body["thinking"] = {"type":self.cfg["ds_thinking"]}
        stage = getattr(self, "stage", None)
        with stage(task, model) if stage else nullcontext():
            for attempt in range(3):
                try:
                    reply = request(base.rstrip("/")+"/chat/completions", body,
                                    headers={"Authorization":"Bearer "+key} if key else {}, timeout=240)
                    break
                except ServiceHTTPError as err:
                    if err.status != 429 or attempt==2:
                        raise
                    delay = int(err.retry_after) if (err.retry_after or "").isdigit() else 2**(attempt+1)
                    if delay>30:
                        raise
                    time.sleep(delay)
            choice = reply["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ValueError(model + " 输出被截断")
            s = choice["message"].get("content")
            if not isinstance(s, str) or not s.strip():
                raise ValueError(model + " 返回空正文")
            result = json.loads(re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", s.strip()))
            if not isinstance(result, dict):
                raise ValueError(model + " 未返回 JSON 对象")
            return result

    def check_vision(self):
        from PIL import Image, ImageDraw
        base,model,env,_ = self.endpoint(True)
        key = (base,model,os.environ.get(env))
        if getattr(self,"_vision_verified",None)==key:
            return
        colors = ["red","blue","green","yellow"]
        left,right = secrets.SystemRandom().sample(colors,2)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"vision.png"
            img = Image.new("RGB",(128,64),left)
            ImageDraw.Draw(img).rectangle((64,0,127,63),fill=right)
            img.save(path)
            result = self.ask("Read the two colored panels from left to right. Return "
                              "{\"colors\":[\"left color\",\"right color\"]} using only red, blue, green or yellow.",
                              {},[path],tokens=250,task="视觉接口自检")
        if result.get("colors") != [left,right]:
            raise ValueError("视觉接口未正确读取校验图，请核实 Qwen 端点支持图片输入")
        self._vision_verified = key

    def plan(self, spec, clip_refs=None):
        from .timecode import authored_links, breakdown
        locked = breakdown(spec["script"],spec["seconds"],spec["requirements"])
        if locked:
            # Keep long authored films within the model context, never rewrite source passages.
            groups = [locked[start:start+9] for start in range(0,len(locked),9)]
            combined = None
            for group in groups:
                data = dict(spec, timed_shots=group, seconds=sum(x["seconds"] for x in group))
                if combined is not None:
                    data.update(previous_beat=combined["shots"][-1]["beat"],bible=combined["bible"])
                plan = self.outline(data,clip_refs,continuation=combined is not None)
                for timing, shot in zip(group,plan["shots"],strict=True):
                    shot["source_clip"] = timing
                    authored_links(shot)
                if combined is None:
                    combined = plan
                else:
                    combined["shots"].extend(plan["shots"])
            self.validate_plan(combined,spec['seconds'], assets(self.cfg) | (clip_refs or {}))
            return combined
        if spec["seconds"] <= 90:
            return self.outline(spec, clip_refs)
        instruction = """Divide the entire screenplay into coherent sequences of 2–60 whole
seconds each. Their durations must sum exactly to requested seconds. Preserve dialogue,
titles, and action from the source, in order. Provide a concise global continuity bible
and instrumental score brief. Return {"bible":"...", "music":"...", "sequences":[
{"script":"source beats and literal lines in this sequence","seconds":integer}]}."""
        data = dict(spec)
        for _ in range(3):
            try:
                sequence_plan = self.ask(instruction, data, tokens=8000)
                seqs = sequence_plan["sequences"]
                if not seqs or not all(type(q["seconds"]) is int and 2 <= q["seconds"] <= 60
                                       and q["script"].strip() for q in seqs):
                    raise ValueError("Invalid sequence payload")
                if sum(q["seconds"] for q in seqs) != spec["seconds"]:
                    raise ValueError("Sequence durations must sum to the requested total")
                bible, music = sequence_plan["bible"], sequence_plan["music"]
                break
            except (KeyError, TypeError, ValueError) as err:
                data["correction"] = str(err)
        else:
            raise ValueError("无法按目标时长拆分场次：" + data["correction"])
        shots = []
        for seq in seqs:
            brief = dict(spec, script=seq["script"], seconds=seq["seconds"],
                         continuity_bible=bible, previous_beat=shots[-1]["beat"] if shots else "")
            shots.extend(self.outline(brief, clip_refs, continuation=bool(shots))["shots"])
        plan = dict(bible=bible, music=music, shots=shots)
        self.validate_plan(plan, spec["seconds"], assets(self.cfg) | (clip_refs or {}))
        return plan

    def outline(self, spec, clip_refs=None, continuation=False):
        catalog = assets(self.cfg)
        catalog.update(clip_refs or {})
        notes = dict.fromkeys(catalog,"")
        if self.cfg.get("allow_sidecars",True):
            for name,path in catalog.items():
                sidecar=path.with_suffix(".txt")
                if name not in (clip_refs or {}) and sidecar.is_file() and not sidecar.is_symlink() and sidecar.resolve().parent==path.parent.resolve():
                    with sidecar.open(encoding="utf-8-sig") as handle:
                        notes[name]=handle.read(800)
        notes.update({k: "已指定片库编号的最后一帧" for k in clip_refs or {}})
        instruction = """You direct the supplied film. Preserve narrative order, all specific beats,
names, spoken lines, requested titles and ending. Work from the script and creative brief.
Prefer observable actions, plausible performance and practical light. Preserve source dialogue verbatim; invent no slogans. Design motivated cuts; use previous accepted tail for continuity, and cut to a fresh setup
when location/time changes. Use the literal sound value "silence" for intentional silence. Return {"bible":"recurring cast, wardrobe, locations, light and sound",
"music":"instrumental scoring brief","shots":[{"beat":"specific visible event + camera movement",
"seconds":4,"start":"previous","end":"","sound":"diegetic sound bridge",
"dialogue":"","narration":"","title":"","caption":"","overlay":"","anchors":[],"subjects":[],"camera_motion":"moving"}]}.
camera_motion is locked or moving and describes the camera, independent of subject motion.
subjects lists at most three recurring characters that must be visibly identifiable here.
For requested #tag eyes bubbles put the exact hashtag in the relevant shot's bubble field.
Return a single plan-level endcard string for requested closing lockup copy, verbatim from the script.
Never place that copy in shot titles. For foreign dialogue add dialogue_language as its ISO language code.
Use exactly the same names from the screenplay/bible throughout, including after location changes.
Omit off-camera speakers, implied or unidentifiable figures. These names link accepted on-screen reference frames.
anchors lists up to two catalog image names for canonical identity or location QA, whenever available.
start is exactly previous, empty for a fresh cut, or a listed image name. end is empty
or a listed image name and needs a start. First shot cannot start with previous. overlay
may name a supplied transparent fullscreen PNG. title is exact post-production text.
caption is verbatim subtitle text only if requested; dialogue is literal on-camera speech; narration is literal separately recorded voiceover.
Use empty strings for absent elements. Use whole seconds, 2–8 per shot, favor 4–6;
sum MUST match requested seconds. When narration_timing_corrections is present, use those recorded
line lengths to reallocate shots or distribute the literal text across adjacent cuts in order, preserving
the full script and film duration. Budget enough time for speech. Keep the outline compact."""
        if spec.get("timed_shots"):
            instruction += (" Follow timed_shots exactly, one shot per segment with its exact duration. "
                            "For split segments divide action and lines in chronological order without repetition.")
        if continuation:
            instruction += " This sequence follows the previous beat. Its first shot MAY use previous."
        data = dict(spec, assets=notes)
        for _ in range(3):
            try:
                p = self.ask(instruction, data, tokens=min(24000, 2200 + spec["seconds"] * 27))
                self.validate_plan(p, spec["seconds"], catalog, continuation)
                if spec.get("timed_shots") and [s["seconds"] for s in p["shots"]] != [
                        row["seconds"] for row in spec["timed_shots"]]:
                    raise ValueError("镜数与逐镜时长须严格匹配 timed_shots")
                return p
            except (KeyError, ValueError, TypeError) as err:
                data["correction"] = str(err)
        raise ValueError("导演分镜未通过结构或时长检查：" + data["correction"])

    @staticmethod
    def validate_plan(plan, seconds, catalog, continuation=False):
        if not isinstance(plan["bible"], str) or not isinstance(plan["music"], str):
            raise ValueError("Missing visual bible or score brief")
        total = 0
        for i, s in enumerate(plan["shots"]):
            if type(s["seconds"]) is not int or not 2 <= s["seconds"] <= 8:
                raise ValueError("Each shot must be 2–8 whole seconds")
            total += s["seconds"]
            for k in ("beat", "sound", "start", "end", "dialogue", "narration", "title", "overlay"):
                if not isinstance(s[k], str):
                    raise ValueError(f"Invalid shot {i+1} field {k}")
            if s.get("camera_motion","moving") not in {"locked","moving"}:
                raise ValueError("camera_motion must be locked or moving")
            subjects = s.get("subjects",[])
            if (not isinstance(subjects,list) or len(subjects)>3 or
                any(not isinstance(x,str) or not x.strip() or len(x)>90 for x in subjects) or
                len(set(subjects))!=len(subjects)):
                raise ValueError("Invalid returning character names")
            anchors = s.get("anchors", [])
            if not isinstance(anchors, list) or len(anchors)>2 or any(not isinstance(a,str) or a not in catalog for a in anchors):
                raise ValueError("Invalid continuity anchors")
            if not isinstance(s.get("caption", ""), str):
                raise ValueError("Invalid post-production caption")
            if not s["beat"].strip() or not s["sound"].strip():
                raise ValueError("Shot needs an action and sound direction")
            if s["start"] not in {"", *catalog} and not ((i or continuation) and s["start"] == "previous"):
                raise ValueError(f"Invalid start reference in shot {i+1}")
            if s["end"] and (not s["start"] or s["end"] not in catalog):
                raise ValueError("End reference requires a valid start")
            if s["overlay"] and (s["overlay"] not in catalog or not s["overlay"].lower().endswith(".png")):
                raise ValueError("Overlay must be an available PNG")
            s["frames"] = s["seconds"] * 24
        if total != seconds:
            raise ValueError(f"Shot durations sum to {total}, requested {seconds}")

    def revise(self, shot, notes, catalog, first_shot):
        if not any(n.strip() for n in notes):
            return shot
        result = self.ask("""Update this shot to implement the user's revision notes, including
any exact title, dialogue, voiceover, visible event, camera, visual references or sound
changes. Preserve every other shot field and its exact duration. Allowed start is empty,
previous (unless first shot), or a catalog key. End needs a start. Overlay must be a listed PNG.
Return {"shot":{...complete shot...}}. This is a local shot revision.""",
                          dict(shot=shot, notes=notes, assets=list(catalog), first_shot=first_shot))
        updated = result["shot"]
        self.validate_plan({"bible":"","music":"","shots":[updated]}, shot["seconds"], catalog,
                           continuation=not first_shot)
        for field in ("source_clip","endcard","bubble","dialogue_language"):
            if field in shot and field not in updated:
                updated[field] = shot[field]
        return updated

    def prompt(self, spec, bible, shot, feedback):
        instruction = """Write one English LTX shot prompt of 90–200 words in present tense.
Translate the shot into an executable camera and sound direction. Set shot size, camera
position and sound cues using the supplied shot.departments.camera and shot.departments.sound
execution sheets. Follow their blocking, framing, continuity, edit_point, sync, ambience,
bridge and mix_intent within the locked beat. Set the camera
position relative to the actor, movement or lock, direction of travel and framing on the
cut. Stage the visible action at explicit times within the shot. Keep the light source and
screen direction consistent with adjacent shots unless the edit motivates a change. End
with Sound: and describe diegetic sound entering, changing with the action, and carrying
over or cutting at the end. Keep intentional silence and verbatim original-language dialogue.
If feedback includes a low-resolution draft, correct the specific blocking, framing or
timing defect while preserving approved beats and anchors. Keep this one filmable shot.
Exclude music, narration, captions, speech bubbles, typography and other post overlays.
Use 'Static camera' only for a locked-off shot with a start reference. Respect any
revision notes without altering continuity. Return {"prompt":"one paragraph"}."""
        locked_phrase = "Static camera, framing stays exactly the same, no cuts."
        if shot.get("camera_motion")=="locked" and shot["start"]:
            instruction += " Begin with this exact sentence: "+locked_phrase
        elif shot.get("camera_motion")=="moving":
            instruction += " This is a moving camera. Omit the static-camera instruction."
        data = dict(brief=spec["requirements"], bible=bible, shot=shot, feedback=feedback)
        for _ in range(3):
            value = self.ask(instruction, data, task="撰写镜头提示词").get("prompt", "")
            if isinstance(value, str):
                value = " ".join(value.split())
                if (90 <= len(value.split()) <= 200 and "Sound:" in value and value.split("Sound:")[-1].strip()
                    and ("static camera" not in value.lower() or (shot["start"] and shot.get("camera_motion")!="moving"))
                    and (not shot["start"] or shot.get("camera_motion")!="locked" or value.startswith(locked_phrase))
                    and (not shot["dialogue"] or shot["dialogue"] in value.split('Sound:')[-1])):
                    return value
            data["format_error"] = "Check length, Sound: ending, camera lock and verbatim dialogue."
        raise ValueError("导演提示词连续三次未通过格式检查")

    def review(self, spec, plan, shot, pictures, roles):
        instruction = """Judge the candidate shot against the supplied creative brief, shot,
bible and labeled frames. Score each dimension from 1 (wrong) to 5 (fully meets):
story, composition, identity, continuity, artifacts. Compare references to candidate frames.
Inspect shot size and framing, camera travel or lock, staging at the specified times,
screen direction, eyeline, light and blocking at the edit, anatomy and hallucinated text.
Compare visible cues with the camera and sound department execution sheets attached to the shot.
Use the shot's sound direction to identify the planned handoff. Judge only the *visible*
action cue for a sound. A low-resolution draft can establish blocking, camera path and
coarse timing. Mark specific correctable defects in repair; a draft cannot establish
final texture, audio timing, dialogue accuracy or delivery quality.
Consider measured freeze and black intervals in motion_signals against the action.
Intentional stillness, blackouts, flash cuts and hard scene changes are legitimate.
Evaluate each numbered source obligation. Return a checks array in order, each item with
index (1-based), result (seen, failed, or post), and evidence (specific visible cue). Use post only for
nonvisual or post-production requirements. A missing visible requirement must be failed. Compare adjacent
shots according to the intended cut, screen direction and motion; intentional changes of scene are valid.
Judge only visible evidence; titles/overlay/narration are added later. Do not claim to
hear audio. Return {"scores":{...},"checks":[{"index":1,"result":"seen","evidence":"visible detail"}],
"notes":"specific defects or observations",
"repair":"concrete prompt changes if needed"}."""
        if shot.get("subjects"):
            instruction += (" For EVERY name in shot.subjects return subjects in the same order, each with name,"
                            " frame (index 0–4 of candidate samples clearly showing the subject, null if unavailable),"
                            " and evidence describing identifiable details. Compare any labeled past appearance"
                            " of this person, respecting intentional wardrobe, time and location changes."
                            " Mark identity below threshold for visible mismatches.")
        rubric = spec.get("aesthetic_rules",[])
        if rubric:
            instruction += (" Judge each supplied aesthetic rule on visible evidence in its stated scope, "
                "allowing post layers to be added later. Return aesthetic_checks in order with "
                "index (1-based), passed (boolean) and evidence citing specific frames. A rejection requires an actionable fix.")
        if shot.get("motion_evidence"):
            instruction += (" Inspect the dense native-frame boards in temporal order, including abrupt transitions. "
                "Return motion_checks in early, middle, late order. For each third cite one actual numbered frame "
                "from that third, a boolean passed and specific evidence of movement, hand/prop contact, eyeline, "
                "performance, texture or transition. Cite intentional flashes as such when supported by the script. "
                "A static pose alone cannot establish an action. Use frame as the zero-based native integer index.")
        pulses=shot.get("motion_evidence",{}).get("pulse_frames",[])
        if pulses:
            instruction += (" For each marked pulse_frames entry in order, inspect its preceding and following "
                "images against script intent. Return pulse_checks with frame, decision (motivated, defect or unclear) "
                "and specific visible evidence. A normal occlusion, gesture, or intentional flash is motivated. "
                "The numerical signal alone is never evidence of a defect.")
        verdict = self.ask(instruction, {"requirements": spec["requirements"], "aesthetic_rules":rubric,"bible": plan["bible"],
                                  "shot": shot, "image_order": roles}, pictures, task="抽帧与跨镜审片")
        scores = verdict["scores"]
        if any(type(scores.get(k)) is not int or not 1 <= scores[k] <= 5 for k in DIMS):
            raise ValueError("Invalid visual review scores")
        verdict["passed"] = min(scores[k] for k in DIMS) >= self.cfg["threshold"]
        if shot.get("motion_evidence"):
            from .motion import verify_windows
            from .motion import verify_pulses
            pulse_ok=verify_pulses(verdict,pulses)
            motion_ok = verify_windows(verdict, shot["motion_evidence"]) and pulse_ok
            verdict["passed"] = verdict["passed"] and motion_ok
        if rubric:
            rows = verdict.get("aesthetic_checks")
            if (not isinstance(rows,list) or [r.get("index") for r in rows]!=list(range(1,len(rubric)+1))
                    or any(type(r.get("passed")) is not bool or not isinstance(r.get("evidence"),str)
                           or not r["evidence"].strip() for r in rows)):
                raise ValueError("审美检查缺少逐条可复核的依据")
            verdict["passed"] = verdict["passed"] and all(r["passed"] for r in rows)
        if shot.get("subjects"):
            identifications = verdict.get("subjects")
            if not isinstance(identifications,list) or [r.get("name") for r in identifications]!=shot["subjects"]:
                raise ValueError("角色出场表缺少已登记的人物")
            for r in identifications:
                if not isinstance(r.get("evidence"),str) or not r["evidence"].strip():
                    raise ValueError("角色出场表缺少画面依据")
                if r.get("frame") is None:
                    verdict["passed"] = False
                elif type(r["frame"]) is not int or not 0<=r["frame"]<=4:
                    raise ValueError("角色参考帧索引错误")
        else:
            verdict.pop("subjects",None)
        checks = verdict.get("checks", [])
        if (not isinstance(checks,list) or [c.get("index") for c in checks] !=
            list(range(1,len(shot.get("obligations",[]))+1))):
            raise ValueError("审片返回的剧情验收项不完整")
        for item, duty in zip(checks, shot.get("obligations",[])):
            if item.get("result") not in {"seen","post","failed"} or not isinstance(item.get("evidence"),str) or not item["evidence"].strip():
                raise ValueError("缺少可复核的逐项证据")
            if item["result"] == "post" and duty["channel"] == "visual":
                item["result"] = "failed"
            if item["result"] == "failed":
                verdict["passed"] = False
        if not isinstance(verdict.get("notes"), str) or not isinstance(verdict.get("repair"), str):
            raise ValueError("Invalid visual review notes")
        return verdict

    def final_review(self, spec, plan, boards=()):
        shots_per_board = 16  # Each board covers up to 16 shots.
        if not plan or len(boards) != (len(plan) + shots_per_board - 1) // shots_per_board:
            raise ValueError("成片接触表数量与时间线不一致")
        instruction = """Audit the actual accepted timeline and per-shot observations against
the locked original script and requirements in brief. Preserve names, dialogue and titles
verbatim, except where a supplied user revision explicitly changes them. The contact sheets
show actual IN / MID / OUT frames per shot from the assembled master, with global frame numbers.
Frame labels give global shot number and time. Judge only the assigned inclusive shot_range
and its incoming cut. Other screenplay passages provide context: evaluate a beat where it
belongs. Compare identity, location, lighting, screen direction, action order and pacing.
For an incoming cut, compare the last shot on the context sheet with the first assigned
shot and their observations. Distinguish a motivated hard cut from a continuity error.
Compare OUT to the next IN and the planned sound bridge. These pictures cannot establish audible content. Use per-shot checks and
transcripts where relevant. Name missing evidence. Return {"passed":true or false,
"notes":"用中文按镜号记录所见关键剧情、剪辑和偏差","repair_shot":integer or null}.
Give a global shot number only if a change to that assigned shot can fix the issue.
When there is an incoming cut, also return boundary_notes describing its evidence."""
        segments = []
        for start in range(0, len(boards), 3):
            begin = start * shots_per_board
            end = min((start + 3) * shots_per_board, len(plan))
            context = max(0, start - 1)
            data = {"brief": spec, "shot_range": [begin + 1, end],
                    "accepted_timeline": plan[begin:end],
                    "image_order": [{"first_shot": i * shots_per_board + 1,
                                     "last_shot": min((i + 1) * shots_per_board, len(plan)),
                                     "role": "previous context" if i < start else "assigned"}
                                    for i in range(context, min(start + 3, len(boards)))]}
            if begin:
                data["previous_shot"] = plan[begin - 1]
            result = self.ask(instruction, data, boards[context:start+3], tokens=2000,
                              task=f"成片画面总审 S{begin+1:02}–S{end:02}")
            self._validate_master_review(result, begin + 1, end)
            boundary = result.get("boundary_notes")
            if begin and (not isinstance(boundary, str) or not boundary.strip()):
                raise ValueError(f"S{begin:02}–S{begin+1:02} 缺少跨段接点审片依据")
            segments.append(dict(result, first_shot=begin+1, last_shot=end))
        if len(segments) == 1:
            return segments[0]
        failures = [s for s in segments if not s["passed"]]
        if failures:
            return {"passed":False, "notes":"；".join(
                f'S{s["first_shot"]:02}–S{s["last_shot"]:02}：{s["notes"]}' for s in failures),
                "repair_shot": failures[0].get("repair_shot"), "segments": segments}
        summary = self.ask("""Review the whole assembled film using the locked original screenplay,
requirements, and ordered visual-editor reports. Inspect narrative causality, pacing,
recurring cast, scene changes and the ending across segments. Use the reported evidence,
without inventing observations or claiming to hear the film. Honor only explicit user
revisions of the original. When evidence fails to support a required beat, fail and cite
the shot(s). Return {"passed":true or false,"notes":"中文依据，引述镜号、剧情和跨段接点",
"repair_shot":integer or null}. Give a global shot number only for an actionable repair.""",
            {"brief":spec, "segments":segments,
             "revisions":[{"shot":s["shot"], "changes":s["changes"]} for s in plan if s.get("changes")]},
            tokens=2000, task="跨段剧情与节奏总审")
        self._validate_master_review(summary, 1, len(plan))
        return dict(summary, segments=segments)

    @staticmethod
    def _validate_master_review(result, first, last):
        if (type(result.get("passed")) is not bool or not isinstance(result.get("notes"), str)
                or not result["notes"].strip()):
            raise ValueError("成片审查缺少判定或依据")
        shot = result.get("repair_shot")
        if shot is not None and (type(shot) is not int or not first <= shot <= last):
            raise ValueError("成片返工指向了本段范围外或无效的镜号")
