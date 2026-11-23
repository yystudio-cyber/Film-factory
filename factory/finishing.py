"""Resolve local, editable graphics against the locked edit timeline."""
import re

TAG = re.compile(r"#[\w\-·]+",re.UNICODE)
END = re.compile(r"片尾定版|片尾文字|片尾卡|end\s*card",re.I)


def plan_layers(spec,plan):
    shots = plan["shots"]
    source = spec["script"]+"\n"+spec["requirements"]
    ending = plan.get("endcard","")
    if not isinstance(ending,str) or (ending and ending not in source):
        raise ValueError("片尾定版须逐字取自剧本或要求")
    if not ending and END.search(source):
        last = shots[-1]
        ending = last.get("title","")
        if not ending or ending not in source:
            raise ValueError("片尾定版缺少可核对的原文，请在剧本中给出定版文字")
    elapsed = 0
    requested_tags = set(TAG.findall(source))
    seen_tags = set()
    for shot in shots:
        tag = shot.get("bubble","")
        if not tag and TAG.fullmatch(shot.get("title","")):
            tag = shot["title"]
        if tag and (not TAG.fullmatch(tag) or tag not in source):
            raise ValueError("气泡必须是剧本中的原文 #标签")
        if tag:
            seen_tags.add(tag)
            shot["bubble"] = tag
            if shot.get("title")==tag:
                shot["title"]=""
        if ending:
            if shot.get("title")==ending:
                shot["title"]=""
            first = max(0,(spec["seconds"]-4-elapsed)*24)
            if first < shot["frames"]:
                shot["endcard"]={"text":ending,"first_frame":first}
        elapsed += shot["seconds"]
    missing = requested_tags-seen_tags
    if missing and ("气泡" in source or "bubble" in source.lower()):
        raise ValueError("缺少本地气泡图层："+", ".join(sorted(missing)))
    return plan
