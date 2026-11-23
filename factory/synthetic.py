"""CPU-only reproducible nine-cut AV conform proof. Inputs are generated color/tone plates."""
import argparse
import hashlib
import tempfile
from pathlib import Path

from . import finishing,media
from .common import read,write


def build(output,font):
    output.mkdir(parents=True,exist_ok=False)
    cfg=dict(width=1920,height=1080,font=font,station_contract=True)
    durations=[8,8,6,6,6,6,6,6,8]
    colors=["2b3541","403048","283e3e","463829","393348","203445","402f28","293a30","222222"]
    shots=[dict(shot=i+1,seconds=n,frames=n*24,title="",dialogue="",narration="",sound="ambient tone")
           for i,n in enumerate(durations)]
    shots[1]["bubble"]="#TEST"
    plan=finishing.plan_layers(dict(script="#TEST 气泡 THE END",requirements="",seconds=60),
                               dict(shots=shots,endcard="THE END"))
    with tempfile.TemporaryDirectory(dir=output) as tmp:
        root=Path(tmp)
        clips=[]
        native=[]
        for i,shot in enumerate(shots):
            take=root/f"S{i+1:02}"
            take.mkdir()
            src=take/"source.mp4"
            seconds=shot["seconds"]
            media.ff("-f","lavfi","-i",f"color=c=0x{colors[i]}:s=1920x1088:r=24",
                     "-f","lavfi","-i",f"sine=f={280+i*40}:r=48000","-t",f"{seconds+1/24:.8f}",
                     "-c:v","libx264","-threads:v","2","-preset","ultrafast","-c:a","aac",src)
            native.append(media.technical(src,cfg,shot["frames"]))
            cut=media.conform(cfg,src,shot,None,take)
            clips.append((cut,seconds))
        bed=root/"synthetic-score.wav"
        media.ff("-f","lavfi","-i","sine=f=150:r=48000:d=60","-c:a","pcm_s16le",bed)
        master=media.assemble(clips,bed,60,root,timeline=shots,cfg=cfg)
        facts=media.technical(master,cfg,1440,master=True)
        # Decode the delivered raster before and on the title boundary, inside its safe area.
        roi=media.ff("-i",master,"-vf","select=eq(n\\,1343)+eq(n\\,1344)+eq(n\\,1439),crop=800:140:560:470,format=gray",
                     "-fps_mode","passthrough","-threads:v","1","-f","rawvideo","-")
        stride=800*140
        peaks=[max(roi[i:i+stride]) for i in range(0,len(roi),stride)]
        if len(peaks)!=3 or not(peaks[0]<100 and peaks[1]>220 and peaks[2]>220):
            raise ValueError(f"片尾定版像素边界错误：{peaks}")
        bubble=media.ff("-i",master,"-vf","select=eq(n\\,191)+eq(n\\,192),crop=520:170:140:140,format=gray",
                        "-fps_mode","passthrough","-threads:v","1","-f","rawvideo","-")
        step=520*170
        bubble_peaks=[max(bubble[i:i+step]) for i in range(0,len(bubble),step)]
        if len(bubble_peaks)!=2 or not (bubble_peaks[0]<110 and bubble_peaks[1]>220):
            raise ValueError(f"气泡进入时点错误：{bubble_peaks}")
        master.replace(output/"synthetic-60s.mp4")
        write(output/"timeline.json",plan["shots"])
        write(output/"evidence.json",dict(kind="synthetic_cpu_media_only",native=native,master=facts,
                audio_mix=read(root/"audio-mix.json"),endcard_frame_peaks=peaks,bubble_frame_peaks=bubble_peaks,
                sha256=hashlib.sha256((output/"synthetic-60s.mp4").read_bytes()).hexdigest(),
                unchecked=["ComfyUI","ACE-Step","Qwen","GPU placement","real speech","creative acceptance"]))


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output",type=Path)
    parser.add_argument("--font",required=True)
    args=parser.parse_args()
    build(args.output,args.font)
