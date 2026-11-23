"""Compare localized temporal evidence on invented clips; intent labels are fixture ground truth."""
import argparse
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

def measure(repo,out,held):
    sys.path.insert(0,str(repo))
    from factory import media,motion
    from PIL import Image,ImageDraw
    frame_no=31 if held else 17
    x,y,size=(109,49,14) if held else (61,29,20)
    count=72 if held else 48
    result=[]
    with TemporaryDirectory() as tmp:
        root=Path(tmp)
        for kind in ("local_light","local_chroma","occlusion","cue_flash","still"):
            folder=root/kind
            folder.mkdir()
            bg=(180,0,0) if kind=="local_chroma" else (50,50,50)
            first=Image.new("RGB",(192,108),bg)
            event=first.copy()
            draw=ImageDraw.Draw(event)
            if kind in ("local_light","occlusion"):
                draw.rectangle((x,y,x+size,y+12),fill=(190,190,190))
            elif kind=="local_chroma":
                draw.rectangle((x,y,x+size,y+12),fill=(0,92,0))
            elif kind=="cue_flash":
                draw.rectangle((0,0,191,107),fill="white")
            for i in range(count):
                path=folder/f"{i:03}.png"
                (event if i==frame_no else first).save(path)
            video=folder/"clip.mkv"
            media.ff("-framerate","24","-i",folder/"%03d.png","-c:v","ffv1","-pix_fmt","yuv420p",video)
            _,evidence=motion.motion_boards(video,count,folder)
            # Measure the actual decoded pair including chroma subsampling.
            pixels=media.ff("-i",video,"-vf",f"select='between(n,{frame_no-1},{frame_no})'",
                            "-pix_fmt","rgb24","-fps_mode","passthrough","-f","rawvideo","-")
            stride=192*108*3
            left=Image.frombytes("RGB",(192,108),pixels[:stride])
            right=Image.frombytes("RGB",(192,108),pixels[stride:])
            from PIL import ImageChops,ImageStat
            mean_y=ImageStat.Stat(ImageChops.difference(left.convert("L"),right.convert("L"))).mean[0]
            visible=frame_no in evidence["shown_frames"]
            intentional=kind in ("occlusion","cue_flash","still")
            # Deterministic oracle sees labeled defect only when its frame was actually presented.
            # This is an upper bound on what a perfect fixture reviewer could see, not VLM accuracy.
            decision=visible and not intentional
            pulses=evidence.get("pulse_frames",[])
            if pulses and hasattr(motion,"verify_pulses"):
                rows=[{"frame":f,"decision":"defect" if f==frame_no and not intentional else "motivated",
                       "evidence":"labeled fictional prop cue"} for f in pulses]
                decision=not motion.verify_pulses({"pulse_checks":rows},pulses)
            result.append(dict(case=kind,marked_frame=frame_no,shown=visible,mean_luma_delta=round(mean_y,3),
                               harmful=not intentional,fixture_rejected=decision,
                               shown_count=len(evidence["shown_frames"]),pulses=evidence.get("pulse_frames",[])))
    with out.open("x",encoding="utf8") as f:json.dump(result,f,indent=2)

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository",type=Path)
    parser.add_argument("new_json",type=Path)
    parser.add_argument("--held-out",action="store_true")
    args=parser.parse_args()
    measure(args.repository.resolve(),args.new_json,args.held_out)
