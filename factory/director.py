import json
import os
import re

from .common import assets, image_url, request

DIMS = ("story", "composition", "identity", "continuity", "artifacts")


class Director:
    def __init__(self, cfg):
        self.cfg = cfg

    def ask(self, instruction, data, images=(), tokens=2500):
        key = os.environ.get("FILM_LLM_KEY")
        if not key:
            raise ValueError("请为配置的 Qwen 接口设置环境变量 FILM_LLM_KEY")
        content = [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}]
        content.extend({"type": "image_url", "image_url": {"url": image_url(p)}} for p in images)
        reply = request(self.cfg["llm"].rstrip("/") + "/chat/completions", {
            "model": self.cfg["model"], "temperature": 0.25, "max_tokens": tokens,
            "messages": [{"role": "system", "content": instruction + " Return one JSON object only."},
                         {"role": "user", "content": content}]},
            headers={"Authorization": "Bearer " + key}, timeout=240)
        s = reply["choices"][0]["message"]["content"].strip()
        return json.loads(re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", s))

    def plan(self, spec, clip_refs=None):
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
        notes = {name: p.with_suffix(".txt").read_text("utf-8-sig")[:800]
                 if p.with_suffix(".txt").exists() else "" for name, p in catalog.items()}
        notes.update({k: "已指定片库编号的最后一帧" for k in clip_refs or {}})
        instruction = """You direct an ECARX film. Preserve narrative order, all specific beats,
names, spoken lines, requested titles and ending. Work from the script and creative brief.
Design motivated cuts; use previous accepted tail for continuity, and cut to a fresh setup
when location/time changes. Use the literal sound value "silence" for intentional silence. Return {"bible":"recurring cast, wardrobe, locations, light and sound",
"music":"instrumental scoring brief","shots":[{"beat":"specific visible event + camera movement",
"seconds":4,"start":"previous","end":"","sound":"diegetic sound bridge",
"dialogue":"","narration":"","title":"","caption":"","overlay":""}]}.
start is exactly previous, empty for a fresh cut, or a listed image name. end is empty
or a listed image name and needs a start. First shot cannot start with previous. overlay
may name a supplied transparent fullscreen PNG. title is exact post-production text.
caption is verbatim subtitle text only if requested; dialogue is literal on-camera speech; narration is literal separately recorded voiceover.
Use empty strings for absent elements. Use whole seconds, 2–8 per shot, favor 4–6;
sum MUST match requested seconds. Budget enough time for speech. Keep the outline compact."""
        if continuation:
            instruction += " This sequence follows the previous beat. Its first shot MAY use previous."
        data = dict(spec, assets=notes)
        for _ in range(3):
            try:
                p = self.ask(instruction, data, tokens=min(24000, 2200 + spec["seconds"] * 27))
                self.validate_plan(p, spec["seconds"], catalog, continuation)
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
        return updated

    def prompt(self, spec, bible, shot, feedback):
        instruction = """Write one English LTX shot prompt of 90–200 words in present tense.
Describe visible action, camera, timing in seconds and lighting. End with Sound: and
diegetic audio, including the verbatim original-language dialogue if specified.
Exclude music, narration, captions, speech bubbles, typography and other post overlays.
Use 'Static camera' only for a locked-off shot with a start reference. Respect any
revision notes without altering continuity. Return {"prompt":"one paragraph"}."""
        data = dict(brief=spec["requirements"], bible=bible, shot=shot, feedback=feedback)
        for _ in range(3):
            value = self.ask(instruction, data).get("prompt", "")
            if isinstance(value, str):
                value = " ".join(value.split())
                if (90 <= len(value.split()) <= 200 and "Sound:" in value and value.split("Sound:")[-1].strip()
                    and (shot["start"] or "static camera" not in value.lower())
                    and (not shot["dialogue"] or shot["dialogue"] in value)):
                    return value
            data["format_error"] = "Check length, Sound: ending, camera lock and verbatim dialogue."
        raise ValueError("导演提示词连续三次未通过格式检查")

    def review(self, spec, plan, shot, pictures, roles):
        instruction = """Judge the candidate shot against the supplied creative brief, shot,
bible and labeled frames. Score each dimension from 1 (wrong) to 5 (fully meets):
story, composition, identity, continuity, artifacts. Compare references to candidate frames.
Inspect action progression, match cuts, anatomy, camera intent and hallucinated text.
Judge only visible evidence; titles/overlay/narration are added later. Do not claim to
hear audio. Return {"scores":{...},"notes":"specific defects or observations",
"repair":"concrete prompt changes if needed"}."""
        verdict = self.ask(instruction, {"requirements": spec["requirements"], "bible": plan["bible"],
                                  "shot": shot, "image_order": roles}, pictures)
        scores = verdict["scores"]
        if any(type(scores.get(k)) is not int or not 1 <= scores[k] <= 5 for k in DIMS):
            raise ValueError("Invalid visual review scores")
        verdict["passed"] = min(scores[k] for k in DIMS) >= self.cfg["threshold"]
        if not isinstance(verdict.get("notes"), str) or not isinstance(verdict.get("repair"), str):
            raise ValueError("Invalid visual review notes")
        return verdict

    def final_review(self, spec, plan, boards=()):
        result = self.ask("""Audit the actual accepted timeline and per-shot observations against
the original script and requirements. The attached contact sheets show the actual assembled master,
left-to-right in timeline order. Frame labels indicate shot and final timeline position.
Check lost story beats, wrong order, required words,
pacing, transitions, soundtrack intent and explicit constraints. Only use supplied evidence.
Return {"passed":true or false,"notes":"具体中文理由，未满足要求时标明镜号",
"repair_shot":integer or null}. If changing one shot can fix the issue, specify its 1-based number.""",
                          {"brief": spec, "accepted_timeline": plan}, boards, tokens=1500)
        if type(result.get("passed")) is not bool or not isinstance(result.get("notes"), str):
            raise ValueError("Invalid final audit")
        return result
