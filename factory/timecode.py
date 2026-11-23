"""Lock authored timecodes before a language model can redistribute them."""
import re

HEADER = re.compile(r"【\s*Clip\s+(\d+)\s*[｜|]\s*(\d+):([0-5]\d)\s*[-–—]\s*"
                    r"(\d+):([0-5]\d)\s*[（(]\s*(\d+)\s*[s秒]\s*[）)]\s*】", re.I)


def requested_seconds(script,requirements):
    matches = list(HEADER.finditer(script))
    if matches:
        last = matches[-1]
        authored = int(last[4])*60+int(last[5])
    else:
        authored = None
    values={int(v) for v in re.findall(r"(?:总长|总时长|成片(?:时长)?|目标时长|全片|片长|时长)\s*[:：为约]?\s*(\d+)\s*(?:秒|[sS])",requirements)}
    if len(values)>1 or (values and authored is not None and authored not in values):
        raise ValueError("制作要求与剧本标注的总时长冲突")
    return authored if authored is not None else next(iter(values),60)


def breakdown(script, total, requirements=""):
    # An explicit production instruction can divide a locked beat without editing its script.
    cuts = {}
    for m in re.finditer(r"(?im)^\s*Clip\s+(\d+)\s+分段\s*[:：]\s*([^\r\n]+)",requirements):
        number=int(m[1])
        parts=re.fullmatch(r"(\d+(?:\s*[+＋]\s*\d+)+)\s*(?:s|秒)?",m[2].strip(),re.I)
        if not parts or number in cuts:
            raise ValueError("分段须逐行明确写为 Clip 2 分段：2+6 秒，且编号唯一")
        cuts[number]=[int(s) for s in re.split(r"[+＋]",parts[1])]

    starts = list(HEADER.finditer(script))
    if len(starts) != len(re.findall(r"【\s*Clip\b",script,re.I)):
        raise ValueError("Clip 标题无法解析，需要编号、起止时间码和整数秒数")
    if cuts.keys()-set(range(1,len(starts)+1)):
        raise ValueError('分段引用未知的原剧本 Clip')
    if not starts:
        return []
    shots = []
    cursor = 0
    for index, match in enumerate(starts, 1):
        number, m1, s1, m2, s2, duration = map(int,match.groups())
        start, end = m1*60+s1, m2*60+s2
        if number != index or start != cursor or end-start != duration or duration < 2:
            raise ValueError(f"Clip {number} 编号、连续时间码或标称时长不一致")
        body = script[match.end():starts[index].start() if index < len(starts) else None].strip()
        if not body:
            raise ValueError(f"Clip {number} 缺少镜头正文")
        count = (duration+7)//8
        lengths=cuts.get(number,[duration//count+int(i<duration%count) for i in range(count)])
        if sum(lengths)!=duration or any(not 2<=n<=8 for n in lengths):
            raise ValueError(f"Clip {number} 分段总和或单段时长不符")
        count=len(lengths)
        for part,length in enumerate(lengths):
            shots.append(dict(clip=number, part=part+1, parts=count, start_frame=cursor*24,
                              seconds=length, source=body))
            cursor += length
    if cursor != total:
        raise ValueError(f"Clip 时间码共 {cursor} 秒，提交时长为 {total} 秒")
    return shots


def authored_links(shot):
    """Explicit in-clip assignments bind the first/last segment of split clips."""
    timing = shot.get("source_clip")
    if not timing:
        return
    for label,field in (("首帧","start"),("尾帧","end")):
        if (field=="start" and timing["part"]!=1) or (field=="end" and timing["part"]!=timing["parts"]):
            continue
        found = re.findall(label+r"\s*[:：=]\s*(C\d+|上一镜(?:已采用)?尾帧|previous)",timing["source"],re.I)
        values = {f"C{int(v[1:]):03}" if v.lower().startswith("c") else "previous" for v in found}
        if len(values)>1:
            raise ValueError(f"Clip {timing['clip']} 的{label}指向多个版本")
        if values:
            shot[field]=values.pop()
