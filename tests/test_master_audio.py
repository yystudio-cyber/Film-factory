import array
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from factory import media, speech
from factory.common import Rejected, read, write


class MasterAudioTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.clip = self.folder/"take.mp4"
        media.ff("-f","lavfi","-i","color=black:s=160x96:r=24",
                 "-f","lavfi","-i","sine=f=500:r=48000","-t","2",
                 "-c:v","libx264","-c:a","aac","-ac","2","-ar","48000",self.clip)

    def tone(self, path, t, hz):
        data = media.ff("-ss",str(t),"-i",path,"-map","0:a:0","-t","0.3",
                        "-ar","48000","-ac","1","-f","f32le","-")
        samples = array.array("f",data)
        phase = 2*math.pi*hz/48000
        i = sum(x*math.cos(phase*n) for n,x in enumerate(samples))
        q = sum(x*math.sin(phase*n) for n,x in enumerate(samples))
        return 2*math.hypot(i,q)/len(samples)

    def test_dialogue_ducks_score_only_in_speaking_cut(self):
        score = self.folder/"music.wav"
        media.ff("-f","lavfi","-i","sine=f=2200:r=48000","-t","6",score)
        clips = [(self.clip,2)]*3
        timeline = [{"seconds":2, "sound":"工位底噪"},
                    {"seconds":2, "sound":"工位底噪", "dialogue":"收到"},
                    {"seconds":2, "sound":"silence"}]
        out = media.assemble(clips,score,6,self.folder,timeline=timeline)
        report = read(self.folder/"audio-mix.json")
        self.assertEqual(report["duck_intervals_seconds"],[[2,4]])
        self.assertEqual(report["silent_intervals_seconds"],[[4,6]])
        music_before = self.tone(out,.8,2200)
        music_during = self.tone(out,2.8,2200)
        self.assertGreater(music_before,0.002)
        self.assertLess(music_during,0.65*music_before)
        self.assertAlmostEqual(self.tone(out,.8,500),self.tone(out,2.8,500),delta=.015)
        self.assertLess(self.tone(out,4.8,2200),.0001)
        self.assertLess(self.tone(out,4.8,500),.0001)
        self.assertLessEqual(report["encoded"]["true_peak_dbtp"],report["ceiling_dbtp"])
        self.assertAlmostEqual(report["encoded"]["integrated_lufs"],-18,delta=1)
        info = media.probe(out,count=True)
        self.assertEqual(media.stream(info,"video")["nb_read_frames"],"144")
        self.assertAlmostEqual(float(info["format"]["duration"]),6,places=3)
        self.assertAlmostEqual(float(media.stream(info,"video")["start_time"]),0,places=3)
        self.assertEqual(media.stream(info,"audio")["channels"],2)
        self.assertAlmostEqual(float(media.stream(info,"audio")["duration"]),6,places=2)

        fg, bed = media.export_stems(clips,score,6,self.folder/"editor",timeline=timeline,
                                     gain_db=report["gain_db"])
        for stem in (fg, bed):
            track = media.stream(media.probe(stem), "audio")
            self.assertEqual((track["codec_name"], track["channels"], track["duration_ts"]),
                             ("pcm_f32le",2,6*48000))
        for t in (.8,2.8,4.8):
            for hz, stem in ((500,fg),(2200,bed)):
                self.assertAlmostEqual(self.tone(stem,t,hz), self.tone(out,t,hz), delta=.005)

    def test_music_keeps_its_audible_tail_after_sidechain_release(self):
        score = self.folder/"music.wav"
        media.ff("-f","lavfi","-i","sine=f=2200:r=48000", "-t","4", score)
        clips = [(self.clip,2)]*2
        _, ref = media.export_stems(clips,score,4,self.folder/"ref",
                                    timeline=[{"seconds":2}]*2, gain_db=0)
        _, duck = media.export_stems(clips,score,4,self.folder/"duck",
                                     timeline=[{"seconds":2,"dialogue":"再见"},{"seconds":2}],gain_db=0)
        tail = self.tone(ref,3.65,2200)
        self.assertGreater(tail,.001)
        self.assertAlmostEqual(self.tone(duck,3.65,2200), tail, delta=.0004)

    def test_intentionally_silent_even_with_noisy_render_and_score(self):
        out = media.assemble([(self.clip,2)], self.clip, 2, self.folder,
                             timeline=[{"seconds":2,"sound":"静音"}])
        level = read(self.folder/"audio-mix.json")
        self.assertEqual(level["gain_decision"],"silence")
        self.assertIsNone(level["encoded"]["integrated_lufs"])
        self.assertIsNone(level["encoded"]["true_peak_dbtp"])
        facts = media.technical(out,{"width":160,"height":96},48,master=True,audible=False)
        self.assertEqual(facts["frames"],48)

    def test_legacy_signature_does_not_require_timeline(self):
        out = media.assemble([(self.clip,2)],None,2,self.folder)
        fg, bed = media.export_stems([(self.clip,2)],None,2,self.folder/"editor",
                                     timeline=[{"seconds":2}], gain_db=0)
        self.assertTrue(out.is_file() and fg.is_file())
        self.assertIsNone(bed)
        self.assertEqual(read(self.folder/"audio-mix.json")["duck_intervals_seconds"],[])

    def test_native_audio_cut_short_or_only_outside_edit(self):
        cfg = {"width":160,"height":96}
        short = self.folder/"short.mp4"
        media.ff("-i", self.clip, "-f", "lavfi", "-i", "sine=f=300:d=0.3",
                 "-map","0:v", "-map","1:a", "-c:v","copy", "-c:a", "aac", short)
        with self.assertRaisesRegex(Rejected, "提前结束"):
            media.technical(short, cfg, 48)
        # A mono AAC source also produces an n_samples:0 line at graph init.
        self.assertEqual(media.technical(self.clip, cfg, 48)["frames"],48)
        after = self.folder/"outside.mp4"
        media.ff("-i", self.clip, "-f","lavfi", "-i","sine=f=300:r=48000:d=0.2",
                 "-map","0:v", "-map","1:a", "-af","adelay=2100", "-c:v","copy", "-c:a","aac", after)
        with self.assertRaisesRegex(Rejected, "静音"):
            media.technical(after, cfg, 48)

    def test_voice_cache_verifies_the_actual_wave_and_fits_in_the_cut(self):
        wav = self.folder/"voice.wav"
        media.ff("-f","lavfi","-i","sine=f=630:r=22050:d=0.6",wav)
        line = "请开始"
        write(self.folder/"voice.json", {"input":{"line":line,"requested_voice":"","culture":""}})
        with patch.dict("os.environ", {"FILM_VOICE":"","FILM_VOICE_CULTURE":""}):
            self.assertEqual(media.narrate({"narration":line,"seconds":2}, self.folder),wav)
            with self.assertRaisesRegex(ValueError, "超过镜头"):
                media.narrate({"narration":line,"seconds":.5}, self.folder)
            shot = {"narration":line,"seconds":2, "title":"", "sound":"静音"}
            out = media.conform({"width":160,"height":96}, self.clip, shot, None, self.folder)
            self.assertGreater(self.tone(out,.1,630),.05)
            self.assertLess(self.tone(out,.9,500),.0002)
            media.ff("-f","lavfi","-i","anullsrc=r=24000:cl=mono","-t","0.5",wav)
            with self.assertRaisesRegex(ValueError, "语音电平"):
                media.narrate(shot,self.folder)

    def test_job_voice_cache_survives_timing_failure_and_seeds_candidates(self):
        line = "静下心来听。"
        def synthesize(text, folder):
            self.assertEqual(text,line)
            media.ff("-f","lavfi","-i","sine=f=612:r=22050:d=0.6",folder/"voice.wav")
            (folder/"line.txt").write_text(text, encoding="utf-8-sig")
            write(folder/"selected-voice.json", {"name":"fixture", "culture":"zh-CN"})

        with patch.object(media, "_record_voice", side_effect=synthesize) as calls:
            with self.assertRaises(media.VoiceTimingError) as raised:
                media.prepare_voice({"narration":line,"seconds":.3},self.folder)
            self.assertAlmostEqual(raised.exception.seconds, .6, places=2)
            self.assertEqual(raised.exception.budget, .3)
            shot = {"narration":line,"seconds":1}
            cached = media.prepare_voice(shot,self.folder)
            take = self.folder/"candidate"
            installed = media.prepare_voice(shot,self.folder,take)
            self.assertEqual(cached.read_bytes(),installed.read_bytes())
            self.assertEqual(read(cached.with_name("voice.json")),read(take/"voice.json"))
            self.assertEqual(media.narrate(shot, take), installed)
            self.assertEqual(calls.call_count,1)
            with patch.dict("os.environ", {"FILM_VOICE":"another voice"}):
                self.assertNotEqual(media.prepare_voice(shot,self.folder), cached)
            self.assertEqual(calls.call_count,2)
            self.assertIsNone(media.prepare_voice({"narration":""},self.folder))

    def test_speech_evidence_uses_only_accepted_edit_audio(self):
        class Recognizer:
            def transcribe(inner, path, **options):
                audio = media.stream(media.probe(path),"audio")
                self.assertEqual((audio["duration_ts"],audio["channels"]),(16000,1))
                self.assertTrue(options["vad_filter"])
                return iter([SimpleNamespace(start=.1,end=.9,text="ＰＭＤＣ 已 启动。")]),SimpleNamespace(language="zh")
        with patch.object(speech,"_model",return_value=Recognizer()):
            facts = speech.verify_speech({"asr_model":"test-model"},self.clip,"pmdc已启动",1)
            self.assertEqual(facts["similarity"],1)
            self.assertEqual(facts["segments"][0]["end"],.9)
            with self.assertRaises(Rejected):
                speech.verify_speech({"asr_model":"test-model"},self.clip,"维修站准备交班",1)

    def test_caps_for_quiet_ambience_and_transient_headroom(self):
        gain, reason = media._programme_gain({"integrated_lufs":-60,"true_peak_dbtp":-45},-18,-1)
        self.assertEqual((gain,reason),(12,"quiet_programme"))
        gain, reason = media._programme_gain({"integrated_lufs":-32,"true_peak_dbtp":-3.5},-18,-1)
        self.assertEqual((gain,reason),(1.5,"peak_limited"))


if __name__ == "__main__":
    unittest.main()
