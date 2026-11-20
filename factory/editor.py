import csv
import shutil
from pathlib import Path

from .common import read
from .media import ff


def export_edit(store, jid, output):
    """Export importable 720p media without touching the user's active NLE project."""
    if store.job(jid)["state"] != "done":
        raise ValueError("请先完成成片验收")
    folder = store.root/jid
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    timeline=read(folder/"timeline.json")
    try:
        with (output/"timeline.csv").open("w",encoding="utf-8-sig",newline="") as f:
            table=csv.writer(f)
            table.writerow(["shot","clip","file","in_frame","out_frame_exclusive","fps","description"])
            pos=0
            for s in timeline:
                name=f"S{s['shot']:02}-{s['code']}.mp4"
                ff("-i",folder/s["file"],"-vf","scale=1280:720:force_original_aspect_ratio=decrease,"
                   "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1","-c:v","libx264","-pix_fmt","yuv420p",
                   "-crf","18","-c:a","copy","-movflags","+faststart",output/name)
                table.writerow([s["shot"],s["code"],name,pos,pos+s["frames"],24,s["beat"]])
                pos+=s["frames"]
        if (folder/"score.wav").exists():
            shutil.copyfile(folder/"score.wav",output/"score.wav")
    except Exception:
        shutil.rmtree(output)
        raise
