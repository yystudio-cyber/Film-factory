"""Trace source text to executable checks, preserving the author's wording."""
import re


def source_units(spec):
    units = []
    for field, prefix in (("script", "剧"), ("requirements", "需")):
        # Preserve punctuation. Even a one-paragraph script gets identifiable source spans.
        parts = re.findall(r'[^。！？!?\r\n]+(?:[。！？!?]+[”’」』"]*)?|[。！？!?]+',spec[field])
        index = 0
        for part in parts:
            for offset in range(0, len(part), 600):
                text = part[offset:offset+600].strip()
                if text:
                    index += 1
                    units.append({"id": f"{prefix}{index:03}", "text": text})
    return units


def coverage(director, spec, plan):
    units = source_units(spec)
    mapped = []
    shots = [{k: v for k, v in s.items() if k not in {"frames", "prompt"}} for s in plan["shots"]]
    instruction = """Act as script supervisor. Audit source coverage against numbered planned shots (1-based).
For EVERY unit return one row in input order. Preserve explicit plot, locations, props,
people, act order, quotes and title spelling. A global instruction may reference several shots.
Separate multiple actions within a unit into individual checks; include a counterexample if
anything is missing, misordered, contradicted or invented. Do not invent coverage. Supply
concise, falsifiable obligations for the shots, in Chinese. Return
{"rows":[{"id":"source ID","covered":true,"notes":"reason","checks":[
{"shot":1,"expect":"specific obligation", "channel":"visual","literal":""}]}]}.
Use channel visual, dialogue, narration, title, caption, audio, or edit. For explicit exact speech,
titles or captions copy the required literal directly from this source unit into literal, otherwise use empty.
Mark production instructions about music, edit, titles and narration as post-production
in the obligation. Uncovered units can have empty checks."""
    for offset in range(0, len(units), 16):
        batch = units[offset:offset+16]
        result = director.ask(instruction, {"units": batch, "shots": shots, "music": plan["music"],
                              "music_enabled": spec["music"], "bible": plan["bible"]},
                              tokens=6000, task="剧本覆盖核对")
        rows = result.get("rows")
        if not isinstance(rows, list) or [r["id"] for r in rows] != [u["id"] for u in batch]:
            raise ValueError("剧本核对缺行或重复，无法确认覆盖")
        for unit, row in zip(batch, rows):
            checks = row["checks"]
            if (type(row["covered"]) is not bool or not isinstance(row["notes"], str)
                    or not isinstance(checks, list) or (row["covered"] and not checks)):
                raise ValueError("无效的剧情覆盖依据：" + unit["id"])
            for c in checks:
                if (type(c["shot"]) is not int or not 1 <= c["shot"] <= len(shots)
                        or not isinstance(c["expect"], str) or not c["expect"].strip()):
                    raise ValueError("无效的镜号或验收项：" + unit["id"])
                channel, literal = c["channel"], c["literal"]
                if channel not in {"visual","dialogue","narration","title","caption","audio","edit"} or not isinstance(literal,str):
                    raise ValueError("未知的验收类型：" + unit["id"])
                if literal:
                    if literal not in unit["text"] or channel not in {"dialogue","narration","title","caption"}:
                        raise ValueError("原文验收项引用了无效的逐字要求：" + unit["id"])
                    if literal not in shots[c["shot"]-1].get(channel,""):
                        row["covered"] = False
                        row["notes"] += " 缺少原文 " + literal
            mapped.append({**unit, "covered":row["covered"], "notes":row["notes"], "checks":checks})
    return mapped


def obligations(ledger, idx):
    return [{"source": row["id"], "original":row["text"], "expect": c["expect"],
             "channel":c["channel"],"literal":c["literal"]}
            for row in ledger for c in row["checks"] if c["shot"] == idx]


def audit_edit(director, ledger, timeline, picture_review):
    """Ask the text editor to decide from observations, actual titles/lines and source spans."""
    evidence = [{k: s.get(k) for k in ("shot","code","observations","checks","transcript","narration","title",
                 "caption","seconds","changes","start","sound")} for s in timeline]
    rows = []
    for i in range(0,len(ledger),16):
        group = ledger[i:i+16]
        result = director.ask("""Evaluate each source unit against accepted-shot evidence and the
visual master audit. Distinguish intended sound from measured audio and observed image content.
Check literal lines, titles, order, causal connections, character and location. User-requested
later revisions override the initial text only where explicit. Cite shot numbers and concrete
evidence; fail missing evidence for required story events. For each unit in input order return
{"rows":[{"id":"unit ID","passed":boolean,"notes":"中文证据或具体修复意见","repair_shot":integer or null}]}.""",
            {"units":group,"evidence":evidence,"visual_master":picture_review}, tokens=4600, task="成片剧情总审")
        batch = result.get("rows")
        if not isinstance(batch,list) or [r["id"] for r in batch] != [u["id"] for u in group]:
            raise ValueError("成片剧情总审遗漏原文条目")
        for row in batch:
            if type(row["passed"]) is not bool or not isinstance(row["notes"],str) or not row["notes"].strip():
                raise ValueError("成片剧情总审缺少依据")
            idx = row.get("repair_shot")
            if idx is not None and (type(idx) is not int or not 1<=idx<=len(timeline)):
                raise ValueError("成片返工指向无效镜号")
        rows.extend(batch)
    return rows
