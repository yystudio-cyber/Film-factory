"""Two independent department breakdowns consume an approved director/scenario plan."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

from .common import read, write
from .story import obligations

ROLES = {
    "camera": ("摄影与美术执行单", ("framing", "blocking", "continuity", "edit_point"),
               "Act as cinematographer and art supervisor. Specify shot scale, camera move, subject blocking, "
               "screen direction, cast/wardrobe/props/location/light to preserve, and a motivated cut point. "
               "Name only assets actually supplied. Do not invent dramatic events."),
    "sound": ("声音与剪辑执行单", ("sync", "ambience", "bridge", "mix_intent"),
              "Act as sound editor. Preserve exact sync lines, planned silence and narration. Distinguish "
              "on-camera events from post. Specify ambience, salient onset and what continues or stops at the cut. "
              "Respect music_enabled and the requested mood. Do not create new speech, effects assets or music cues.")
}


def _department(director, role, payload, cache):
    label, fields, instruction = ROLES[role]
    base, model, _, _ = director.endpoint(False)
    fingerprint = json.dumps([2,role,base,model,instruction,fields,payload], sort_keys=True,ensure_ascii=False)
    key = hashlib.sha256(fingerprint.encode()).hexdigest()
    path = cache/f"{role}-{key}.json"
    if path.exists():
        return read(path)
    prompt = (instruction+" The script, numbered shots, actions, durations and review obligations are locked. "
              "Return a rows array in input order. Each row has integer id and concise Chinese text fields "
              +", ".join(fields)+". Use concrete executable direction; no extra fields.")
    correction = ""
    for _ in range(2):
        try:
            result = director.ask(prompt,dict(payload, correction=correction),tokens=4400,task=label)["rows"]
            if not isinstance(result,list) or [r["id"] for r in result]!=[s["id"] for s in payload["shots"]]:
                raise ValueError("镜号不完整或顺序错误")
            cleaned = []
            for row in result:
                if any(not isinstance(row.get(f),str) or not 0<len(row[f].strip())<=700 for f in fields):
                    raise ValueError("执行单缺少有效字段")
                cleaned.append({f:row[f].strip() for f in fields})
            write(path,cleaned)
            return cleaned
        except (KeyError, TypeError, ValueError) as error:
            correction = str(error)
    raise ValueError(label+"未能形成可执行的逐镜记录："+correction)


def prepare(director, spec, plan, folder, *, revision=None):
    shots = plan["shots"]
    groups = [range(revision[0]-1,revision[0])] if revision else [
        range(start,min(start+8,len(shots))) for start in range(0,len(shots),8)]
    result = {}
    # Independently persisted outputs allow recovery after one department or one batch fails.
    with ThreadPoolExecutor(max_workers=2,thread_name_prefix="film-dept") as pool:
        for indices in groups:
            rows = []
            for i in indices:
                current = revision[1] if revision else shots[i]
                row = {k:current.get(k) for k in ("beat","seconds","start","end","sound","dialogue","narration","title",
                                                    "caption","overlay","anchors","subjects","camera_motion","changes")}
                rows.append(dict(row,id=i+1,obligations=obligations(plan["coverage"],i+1)))
            payload = dict(shots=rows,bible=plan["bible"],brief=spec["requirements"],production_brief=spec.get("production_brief",{}),music_enabled=spec["music"],
                           preceding=shots[indices.start-1]["beat"] if indices.start else "",
                           following=shots[indices.stop]["beat"] if indices.stop<len(shots) else "")
            tasks = {role:pool.submit(_department,director,role,payload,folder/"departments") for role in ROLES}
            values = {role:task.result() for role,task in tasks.items()}
            for offset,i in enumerate(indices):
                result[str(i+1)] = {role:entries[offset] for role,entries in values.items()}
    return result
