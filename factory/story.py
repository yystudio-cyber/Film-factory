"""Trace source text to executable checks, preserving the author's wording."""
import re
import unicodedata


LITERAL_CHANNELS = {
    "title":r"标题|片名|片尾大字|定版文案|title",
    "caption":r"字幕|caption|subtitle",
    "dialogue":r"台词|对白|对话|dialogue|spoken line",
    "narration":r"旁白|画外音|解说词|narration|voiceover",
}


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
    for key,label in (("audience","受众"),("objective","目的"),("channel","渠道")):
        if key in spec.get("production_brief",{}):
            units.append({"id":"简报-"+label,"text":label+"："+spec["production_brief"][key]})
    return units


def literal_field(shot,channel):
    if channel=="title":
        return "\n".join(filter(None,[shot.get("title",""),shot.get("bubble",""),
                                      shot.get("endcard",{}).get("text","")]))
    return shot.get(channel,"")


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
Use channel visual, dialogue, narration, title, caption, audio, or edit. Exact bubble and endcard text are title-channel graphics. For explicit exact speech,
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
                    if literal not in literal_field(shots[c["shot"]-1],channel):
                        row["covered"] = False
                        row["notes"] += " 缺少原文 " + literal
            mapped.append({**unit, "covered":row["covered"], "notes":row["notes"], "checks":checks})
    return mapped


def obligations(ledger, idx):
    return [{"source": row["id"], "original":row["text"], "expect": c["expect"],
             "channel":c["channel"],"literal":c["literal"]}
            for row in ledger for c in row["checks"] if c["shot"] == idx]


def _literal_match(expected, actual, spoken=False):
    if not isinstance(actual,str):
        return False
    if spoken:
        # ASR punctuation and case may differ. Keep word boundaries and individual
        # CJK characters so "we can" does not match the beginning of "we cannot".
        def tokens(value):
            return re.findall(r"[a-z0-9]+|[^\W_]", unicodedata.normalize("NFKC",value).casefold())
        needle, haystack = tokens(expected), tokens(actual)
        return bool(needle) and any(haystack[i:i+len(needle)] == needle for i in range(len(haystack)-len(needle)+1))
    left = r"(?<![A-Za-z0-9])" if expected[:1].isascii() and expected[:1].isalnum() else ""
    right = r"(?![A-Za-z0-9])" if expected[-1:].isascii() and expected[-1:].isalnum() else ""
    return bool(expected) and bool(re.search(left + re.escape(expected) + right, actual))


_QUOTES = r'“[^”]*”|「[^」]*」|『[^』]*』|《[^》]*》|"[^"]*"'
_REPLACE = r"改成|改为|替换为|替换成|换成|换为|改叫|change\b.*?\bto|replace\b.*?\bwith"


def _literal_revision(note, channel, expected, sole, unique_channel):
    """A shot-scoped human text edit may change a lock; ordinary staging edits cannot."""
    alias = LITERAL_CHANNELS[channel]
    # Split editorial instructions outside quoted text; a literal may itself contain punctuation.
    clauses = re.findall(rf'(?:{_QUOTES}|[^；;。\n“「『《"]+)+',note)
    for clause in reversed(clauses):
        quotes = list(re.finditer(_QUOTES,clause))
        words = re.sub(_QUOTES,"",clause)
        if re.search(r"不要|不得|不许|不能|不用|无需|别|禁止|do not|don't|keep unchanged",words,re.I):
            continue
        labels = {key for key,pattern in LITERAL_CHANNELS.items() if re.search(pattern,words,re.I)}
        named = any(m[0][1:-1] == expected for m in quotes)
        if (labels and labels != {channel}) or not (named or (sole and labels == {channel})):
            continue
        if not labels and not unique_channel:
            continue
        if re.search(r"颜色|字体|字号|字重|位置|音量|音调|音色|语速|语气|节奏|样式|大小|"
                     r"color|font|size|position|pitch|volume|speed|style|timing",words,re.I):
            continue
        replace = re.search(_REPLACE,clause,re.I)
        if replace:
            scope = re.sub(_QUOTES,"",clause[:replace.end()])
            if labels and not re.search(alias,scope,re.I):
                continue
            if labels:
                # The object must be the wording itself, e.g. 标题从“旧”改为“新”.
                # "change the title background to blue" changes no locked words.
                after_label = re.split(alias,scope,flags=re.I)[-1]
                if not re.fullmatch(r"\s*(?:(?:的文字|的内容|的文案|文字|文案|里边的|里的|中的|"
                                    r"从|由|改成|改为|替换为|替换成|换成|换为|改叫|"
                                    r"wording|text|from|to|with)\s*)*",after_label,re.I):
                    continue
            old_quotes = [q[0][1:-1] for q in quotes if q.end() <= replace.end()]
            if old_quotes and expected not in old_quotes:
                continue
            tail = clause[replace.end():].strip(" ：:，,")
            target = re.match(rf'(?:为\s*)?({_QUOTES})(?=$|[.!！。，,\s])', tail)
            if target:
                return "replace",target[1][1:-1]
            if tail and not re.search(r"[，“”「」；,。]|\b(?:and|but)\b", tail,re.I):
                # Unquoted exact-copy form: 『标题改为：花园园艺』.
                if clause[replace.end():].lstrip().startswith(("：",":")):
                    return "replace",tail
        if re.search(rf"(?:删除|移除|去掉|取消|删去|remove|delete|omit)\s*(?:本镜|这句|该|the\s+)?(?:{alias})"
                     r"(?:中的|里的|文字|内容|文案)?[\s.!！]*$",words,re.I):
            return "remove",""
        if re.search(rf"(?:(?:改写|重写|简写|缩短|精简|rephrase|rewrite|shorten)\s*(?:本镜|这句|该|the\s+)?"
                     rf"(?:{alias})|(?:{alias})\s*(?:改短|重写|精简|改写))[\s.!！]*$", words,re.I):
            return "editor",""
    return None


def _literal_evidence(ledger, timeline):
    locks = {}
    for unit in ledger:
        for check in unit["checks"]:
            if check.get("literal"):
                locks.setdefault((check["shot"],check["channel"]),set()).add(check["literal"])
    evidence = {}
    for unit in ledger:
        rows = []
        for check in unit["checks"]:
            original = check.get("literal")
            if not original:
                continue
            index,channel = check["shot"],check["channel"]
            cut = timeline[index-1]
            if cut["shot"] != index or channel not in LITERAL_CHANNELS:
                raise ValueError("锁定原文未能关联到采用镜头")
            expected, action = original, "source"
            unique_channel = sum(original in values for (n,_),values in locks.items() if n==index)==1
            revisions = []
            for entry in cut.get("revision_history",[]):
                if entry.get("origin") != "user":
                    continue
                note = entry["note"]
                update = _literal_revision(note,channel,expected,len(locks[index,channel])==1,unique_channel)
                if update:
                    action,expected = update
                    revisions.append(note)
            field = literal_field(cut,channel)
            row = dict(shot=index, code=cut.get("code"), channel=channel, source_literal=original,
                       expected=expected, adopted=field, rule=action, user_revisions=revisions)
            if action == "remove":
                passed = (not field.strip() if len(locks[index,channel])==1
                          else not _literal_match(original,field))
            elif action == "editor":
                passed = bool(field.strip())
            else:
                passed = _literal_match(expected,field)
            if channel == "dialogue" and cut.get("speech_review") == "unverified":
                row["audio_status"] = "unverified"
            elif channel == "dialogue":
                transcript = cut.get("transcript","")
                row["transcript"] = transcript
                if action == "remove":
                    passed = passed and not _literal_match(original,transcript,spoken=True)
                else:
                    passed = passed and _literal_match(field if action=="editor" else expected,transcript,spoken=True)
            row["passed"] = passed
            rows.append(row)
        evidence[unit["id"]] = rows
    return evidence


def audit_edit(director, ledger, timeline, picture_review):
    """Ask the text editor to decide from observations, actual titles/lines and source spans."""
    evidence = [{k: s.get(k) for k in ("shot","code","observations","checks","speech_review","transcript","dialogue","narration","title",
                 "caption","bubble","endcard","seconds","changes","revision_history","start","sound")} for s in timeline]
    literal_evidence = _literal_evidence(ledger,timeline)
    rows = []
    for i in range(0,len(ledger),16):
        group = ledger[i:i+16]
        result = director.ask("""Evaluate each source unit against accepted-shot evidence and the
visual master audit. For speech_review=unverified, judge written line alignment only; never claim a listening check. Distinguish intended sound from measured audio and observed image content.
Check literal lines, titles, order, causal connections, character and location. User-requested
later revisions override the initial text only where explicit. Cite shot numbers and concrete
evidence. literal_checks records exact matches from the adopted take. For rule=editor, evaluate
the wording against the referenced user revision and all unchanged obligations. An editorial
repair request cannot authorize replacing source text. Preserve a literal failure. Cite
evidence; fail missing evidence for required story events. For each unit in input order return
{"rows":[{"id":"unit ID","passed":boolean,"notes":"中文证据或具体修复意见","repair_shot":integer or null}]}.""",
            {"units":group,"evidence":evidence,"visual_master":picture_review,
             "literal_checks":{u["id"]:literal_evidence[u["id"]] for u in group}}, tokens=4600, task="成片剧情总审")
        batch = result.get("rows")
        if not isinstance(batch,list) or [r["id"] for r in batch] != [u["id"] for u in group]:
            raise ValueError("成片剧情总审遗漏原文条目")
        for row in batch:
            if type(row["passed"]) is not bool or not isinstance(row["notes"],str) or not row["notes"].strip():
                raise ValueError("成片剧情总审缺少依据")
            idx = row.get("repair_shot")
            if idx is not None and (type(idx) is not int or not 1<=idx<=len(timeline)):
                raise ValueError("成片返工指向无效镜号")
            row["literal_checks"] = literal_evidence[row["id"]]
            failures = [r for r in row["literal_checks"] if not r["passed"]]
            if failures:
                row["editor_notes"] = row["notes"]
                row["passed"], row["repair_shot"] = False, failures[0]["shot"]
                row["notes"] = "；".join(
                    f'S{r["shot"]:02} {r["channel"]} 逐字验收失败，采用版本与要求不符，请核对“{r["expected"] or r["source_literal"]}”'
                    for r in failures)
        rows.extend(batch)
    return rows
