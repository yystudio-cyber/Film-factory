import re

def risks(shot, signals, retry):
    text = " ".join(str(shot.get(k,"")) for k in ("beat","sound","dialogue","start","end"))
    text += " " + str((shot.get("source_clip") or {}).get("source",""))
    labels = []
    if shot.get("subjects") or shot.get("dialogue"):
        labels.append("performance")
    if shot.get("camera_motion") == "moving" or re.search(
            r"追|穿越|转身|伸手|递交|抬手|隧道|闪白|转场|跟拍|奔跑|turns?|reaches?|chase|handoff|flash|tunnel",text,re.I):
        labels.append("motion")
    if shot.get("end") or re.fullmatch(r"C\d+",str(shot.get("start","")),re.I):
        labels.append("reference_transition")
    if signals.get("freeze_events") or signals.get("black_intervals"):
        labels.append("signal")
    if retry:
        labels.append("prior_rejection")
    return labels


def motion_boards(source, count, folder):
    """One bounded decode of every used frame. Keep regular samples and strongest transitions."""
    from PIL import Image, ImageChops, ImageDraw, ImageStat
    from .media import ff
    from .common import Rejected
    if not 48 <= count <= 192:
        raise Rejected("运动检查仅接受 2–8 秒的原生 24fps 镜头")
    w,h=192,108
    raw=ff("-i",source,"-map","0:v:0","-an","-vf",f"scale={w}:{h}","-frames:v",str(count),
           "-threads:v","1","-fps_mode","passthrough","-pix_fmt","rgb24","-f","rawvideo","-")
    stride=w*h*3
    if len(raw)!=stride*count:
        raise Rejected("高风险镜头运动检查缺少采用区间的帧")
    pixels=memoryview(raw)
    def frame(i):
        return Image.frombytes("RGB",(w,h),pixels[i*stride:(i+1)*stride].tobytes())
    changes=[]
    pulses=[]
    prior_rgb=frame(0)
    older_rgb=None
    prior=prior_rgb.convert("L")
    for i in range(1,count):
        current_rgb=frame(i)
        current=current_rgb.convert("L")
        if older_rgb is not None:
            # A one-frame excursion has large changes on both sides and similar outer frames.
            a=ImageChops.difference(older_rgb,prior_rgb)
            b=ImageChops.difference(prior_rgb,current_rgb)
            outer=ImageChops.difference(older_rgb,current_rgb)
            residual=ImageChops.subtract(ImageChops.darker(a,b),outer)
            tiles=residual.resize((12,7),Image.Resampling.BOX)
            peak=max(top for _,top in tiles.getextrema())
            if peak>=24:
                pulses.append((peak,i-1))
        older_rgb,prior_rgb=prior_rgb,current_rgb
        delta=ImageStat.Stat(ImageChops.difference(current,prior)).mean[0]
        changes.append((delta,i))
        prior=current
    peaks=[]
    for delta,i in sorted(changes,key=lambda pair:-pair[0]):
        if delta < 8 or any(abs(i-j)<=2 for j in peaks):
            continue
        peaks.append(i)
        if len(peaks)==4:
            break
    selected=set(range(0,count,6)) | {count-1}
    pulse_frames=[]
    for score,i in sorted(pulses,key=lambda row:-row[0]):
        if all(abs(i-j)>2 for j in pulse_frames):
            pulse_frames.append(i)
        if len(pulse_frames)==2:
            break
    for i in set(peaks+pulse_frames):
        selected.update(range(max(0,i-1),min(count,i+2)))
    selected=sorted(selected)
    images=[]
    for start in range(0,len(selected),16):
        page=selected[start:start+16]
        board=Image.new("RGB",(4*w,((len(page)+3)//4)*(h+20)),"#152029")
        pen=ImageDraw.Draw(board)
        for pos,i in enumerate(page):
            x,y=pos%4*w,pos//4*(h+20)
            board.paste(frame(i),(x,y))
            pen.text((x+4,y+h+2),f"F{i:03}  {i/24:.2f}s",fill="white")
        path=folder/f"motion-{len(images)+1:02}.jpg"
        board.save(path,quality=90)
        images.append(path)
    return images,{"frames_scanned":count,"shown_frames":selected,"transition_frames":sorted(peaks),"pulse_frames":sorted(pulse_frames)}


def verify_windows(verdict, evidence):
    """Require frame citations from all thirds; visual veto survives other high marks."""
    frames=evidence["shown_frames"]
    n=evidence["frames_scanned"]
    rows=verdict.get("motion_checks")
    if not isinstance(rows,list) or len(rows)!=3:
        raise ValueError("高风险镜头缺少起、中、末三个区间的动作证据")
    for i,row in enumerate(rows):
        if not isinstance(row,dict):
            raise ValueError("时序审查格式错误")
        f=row.get("frame")
        if (type(f) is not int or f not in frames or not i*n <= 3*f < (i+1)*n
                or type(row.get("passed")) is not bool or not str(row.get("evidence") or "").strip()
                or not isinstance(row.get("evidence"),str)):
            raise ValueError("时序审片必须引用本区间实际提交的帧及画面依据")
    return all(row["passed"] for row in rows)


def verify_pulses(verdict,frames):
    if not frames:
        return True
    rows=verdict.get("pulse_checks")
    if (not isinstance(rows,list) or len(rows)!=len(frames) or any(not isinstance(r,dict) for r in rows)
            or any(type(r.get("frame")) is not int or r["frame"]!=f or
                   r.get("decision") not in {"motivated","defect","unclear"} or
                   not isinstance(r.get("evidence"),str) or not r["evidence"].strip()
                   for f,r in zip(frames,rows))):
        raise ValueError("局部短闪需要逐个核对实取帧、相邻画面及原稿意图")
    return all(r["decision"]=="motivated" for r in rows)
