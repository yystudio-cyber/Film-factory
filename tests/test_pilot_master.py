"""60-second workstation-format CPU media acceptance using synthetic shot plates."""
import array
import json
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from factory import media
from factory.common import run


class PilotMaster(unittest.TestCase):
    def test_ten_shots_sixty_seconds_1080p_at_every_boundary(self):
        palette = ["eb3838","2cc04b","3052d5","ebc438","ad49ca",
                   "39c4cf","ef7b37","9557aa","74914a","dad8d2"]
        seconds = 6
        fps = 24
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clips, timeline = [], []
            for index, color in enumerate(palette):
                cut = root/f"S{index+1:02}.mp4"
                hz = 300 + index*90
                media.ff("-f","lavfi","-i",f"color=c=0x{color}:s=1920x1080:r={fps}:d={seconds}",
                         "-f","lavfi","-i",f"sine=f={hz}:r=48000:d={seconds}",
                         "-c:v","libx264","-threads:v","2","-preset","ultrafast","-bf","2",
                         "-pix_fmt","yuv420p","-c:a","aac","-ar","48000","-ac","2",cut)
                clips.append((cut,seconds))
                timeline.append({"shot":index+1,"seconds":seconds,"sound":f"{hz} Hz test signal"})
            movie = media.assemble(clips,None,60,root,timeline=timeline)
            facts = media.technical(movie,{"width":1920,"height":1080},1440,master=True)
            self.assertEqual([facts[k] for k in ("width","height","frames","fps","audio")],
                             [1920,1080,1440,24,"aac"])
            info = media.probe(movie)
            video = media.stream(info,"video")
            audio = media.stream(info,"audio")
            self.assertEqual((video["codec_name"],int(audio["sample_rate"]),audio["channels"]),("h264",48000,2))
            self.assertEqual(Fraction(info["format"]["duration"]),60)
            self.assertEqual(Fraction(audio["duration_ts"])*Fraction(audio["time_base"]),60)
            packets = json.loads(run(["ffprobe","-v","error","-select_streams","v:0","-show_entries",
                                      "packet=pts","-of","json",movie]))["packets"]
            self.assertEqual(sorted(int(p["pts"])*Fraction(video["time_base"]) for p in packets),
                             [Fraction(i,fps) for i in range(1440)])

            # Inspect the actual last and first decoded pictures on all nine cuts,
            # plus the first and last pictures of the programme.
            frames = [i*seconds*fps + offset for i in range(len(palette)) for offset in (0,seconds*fps-1)]
            select = "+".join(f"eq(n\\,{i})" for i in frames)
            rgb = media.ff("-i",movie,"-map","0:v:0","-vf",f"select={select},crop=2:2:960:540,format=rgb24",
                           "-fps_mode","passthrough","-threads:v","1","-f","rawvideo","-")
            self.assertEqual(len(rgb),len(frames)*12)
            for i,frame in enumerate(frames):
                actual = rgb[12*i:12*i+3]
                expected = bytes.fromhex(palette[frame//(seconds*fps)])
                with self.subTest(frame=frame):
                    self.assertLessEqual(max(abs(a-b) for a,b in zip(actual,expected)),8)

            # The picture edits must carry the corresponding sound. Check pitch on
            # both sides of every boundary outside the AAC transition window.
            rate = 8000
            samples = array.array("h")
            samples.frombytes(media.ff("-i",movie,"-map","0:a:0","-vn","-t","60",
                                       "-ar",str(rate),"-ac","1","-f","s16le","-"))
            if sys.byteorder != "little":
                samples.byteswap()
            self.assertEqual(len(samples),60*rate)
            for index in range(len(palette)):
                for inside in (.10,5.65):
                    start = round((seconds*index+inside)*rate)
                    window = samples[start:start+rate//4]
                    crossings = sum(a < 0 <= b for a,b in zip(window,window[1:]))
                    with self.subTest(shot=index+1,second=inside):
                        self.assertAlmostEqual(crossings*4,300+index*90,delta=5)
