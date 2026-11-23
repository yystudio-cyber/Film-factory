"""Station-specific behavior with invented media and closed, disposable stores."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory import archive, benchmark, gpu_usage, graphs, media, motion, pilot, speech, studio_import, timecode
from factory.common import read, write
from factory.store import Store


class CandidateEvidence(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)

    def test_flash_between_standard_samples_is_cited_and_vetoable(self):
        source=self.root/"flash.mkv"
        media.ff("-f","lavfi","-i","color=black:size=320x192:rate=24:duration=2",
                 "-vf","drawbox=color=white:t=fill:enable='eq(n,17)'","-c:v","ffv1",source)
        baseline=media.samples(source,{"frames":48},self.root)
        from PIL import Image, ImageStat
        for path in baseline:
            with Image.open(path) as image:
                self.assertLess(max(ImageStat.Stat(image).mean),10)
        boards,ev=motion.motion_boards(source,48,self.root)
        self.assertEqual(ev["frames_scanned"],48)
        self.assertIn(17,ev["transition_frames"])
        idx=ev["shown_frames"].index(17)
        with Image.open(boards[idx//16]) as board:
            x=(idx%4)*192; y=(idx%16//4)*128
            self.assertGreater(min(board.getpixel((x+10,y+10))),230)
        rows=[dict(frame=0,passed=True,evidence="黑场"),dict(frame=17,passed=False,evidence="手部中间突变"),
              dict(frame=47,passed=True,evidence="暗场结束")]
        self.assertFalse(motion.verify_windows({"motion_checks":rows},ev))
        rows[1].update(passed=True,evidence="原稿要求闪白")
        self.assertTrue(motion.verify_windows({"motion_checks":rows},ev))
        rows[1]["frame"]=2
        with self.assertRaises(ValueError):
            motion.verify_windows({"motion_checks":rows},ev)

    def test_nine_beats_ten_segments_attach_history_to_authored_part(self):
        ds=[8,8,6,6,6,6,6,6,8]
        def tc(t):return f"{t//60:02}:{t%60:02}"
        n=0; lines=[]
        for i,sec in enumerate(ds,1):
            lines.append(f"【Clip {i}｜{tc(n)}-{tc(n+sec)}（{sec}s）】虚构行动 {i}。")
            n+=sec
        script="\n".join(lines)
        req="Clip 2 分段：2+6 秒\nClip 2.2 首帧：C006"
        parts=timecode.breakdown(script,60,req)
        shots=[dict(seconds=p["seconds"],frames=24*p["seconds"],source_clip=p,start="previous",end="") for p in parts]
        spec=dict(requirements=req,studio_links={"3":{"start":"C010"}})
        studio_import.apply_links(spec,{"shots":shots})
        self.assertEqual((shots[2]["start"],shots[3]["start"]),("C006","C010"))
        self.assertTrue(pilot.pilot_segments(shots))
        shots[3]["source_clip"]["start_frame"]-=1
        self.assertFalse(pilot.pilot_segments(shots))
        with self.assertRaises(ValueError):
            timecode.breakdown(script,60,"Clip 2 分段：2+4 秒")

    def test_selective_drafts_do_not_add_an_unrequested_render(self):
        from factory import drafts
        high={"camera_motion":"moving"}
        low={"camera_motion":"locked","beat":"an empty desk"}
        self.assertFalse(drafts.requested({},high))
        self.assertTrue(drafts.requested({"render_drafts":True},low))
        self.assertFalse(drafts.requested({"render_drafts":True,"draft_policy":"risk"},low))
        self.assertTrue(drafts.requested({"render_drafts":True,"draft_policy":"risk"},high))

    def test_conflicting_historical_and_segment_anchor_blocks(self):
        shot=dict(start="",end="",source_clip={"clip":2,"part":1})
        with self.assertRaisesRegex(ValueError,"冲突"):
            studio_import.apply_links({"requirements":"Clip 2.1 首帧：C006","studio_links":{"2":{"start":"C007"}}},{"shots":[shot]})

    def test_obsolete_take_is_ineligible_even_if_explicitly_named(self):
        store=Store({"data":self.root})
        with store.tx() as db:
            db.execute("INSERT INTO takes(job,shot,path,eligible) VALUES('old',1,'unused',0)")
        with self.assertRaises(ValueError):
            archive.references(store,"首帧：C001")

    def test_mixed_dialogue_only_calls_correct_native_time_windows(self):
        timeline=[dict(seconds=2),dict(seconds=6,dialogue="Hallo",dialogue_language="de",delivery_voice="subtitles"),
                  dict(seconds=2,dialogue="你好",dialogue_language="zh",delivery_voice="dub")]
        with patch.object(speech,"verify_speech",side_effect=[{"status":"verified","language":"en"},
                {"status":"verified","language":"zh","transcript":"你好。","similarity":1}]) as check:
            rows=speech.review_mix({},self.root/"film.mp4",timeline)
        self.assertEqual([c.kwargs["offset"] for c in check.call_args_list],[2,8])
        self.assertEqual([r["status"] for r in rows],["failed","verified"])
        self.assertEqual([r["source"] for r in rows],["subtitles","dub"])
        self.assertTrue(all(r["status"]=="unverified" for r in speech.review_mix({"asr":"off"},None,timeline)))
        self.assertEqual(speech.review_mix({},None,[{"seconds":6}]),[])

    def test_final_sound_windows_measure_both_sides_of_the_cut(self):
        source=self.root/"tones.wav"
        media.ff("-f","lavfi","-i","sine=f=400:r=48000:d=4","-af","volume=0:enable='gte(t,2)'",
                 "-ac","2","-c:a","pcm_s16le",source)
        left,right=media.sound_windows(source,[{"seconds":2},{"seconds":2}],self.root)
        self.assertGreater(left["exit"]["rms_dbfs"],-50)
        self.assertEqual(right["entry"]["start_seconds"],2)
        self.assertIsNone(right["middle"]["peak_dbfs"])

    def test_extra_duck_uses_only_the_flagged_seconds_in_actual_audio(self):
        import array
        import math
        import sys
        raw=self.root/"silent.pcm"
        raw.write_bytes(bytes(4*48000*4))
        score=self.root/"score.wav"
        media.ff("-f","lavfi","-i","sine=f=300:r=48000:d=4","-ac","2",score)
        inputs,filters,fg,bed=media._audio_buses(raw,score,4,[],[],[(1,2)])
        buf=array.array("h")
        buf.frombytes(media.ff(*inputs,"-filter_complex",";".join(filters),"-map",bed,
                               "-ac","1","-f","s16le","-"))
        if sys.byteorder!="little":
            buf.byteswap()
        def rms(start):
            samples=buf[int(start*48000):int((start+.2)*48000)]
            return math.sqrt(sum(s*s for s in samples)/len(samples))
        self.assertAlmostEqual(rms(1.1)/rms(.4),.25,delta=.02)
        self.assertAlmostEqual(rms(2.2)/rms(.4),1,delta=.02)

    def test_stop_before_first_remote_post_preserves_existing_seed(self):
        from test_factory import Environment
        env=Environment()
        env.setUp()
        self.addCleanup(env.doCleanups)
        env.reject_first=False
        jid=env.new()
        original=graphs.render
        seeds=[]
        def observe(cfg,shot,first,last,seed,folder,checkpoint):
            seeds.append((seed,shot["prompt"]))
            if len(seeds)==1:
                raise InterruptedError("local stop before POST")
            return original(cfg,shot,first,last,seed,folder,checkpoint)
        with patch.object(graphs,"render",side_effect=observe):
            with self.assertRaises(InterruptedError):
                env.execute(jid)
            env.store.command(jid,"stop")
            env.store.command(jid,"resume")
            env.execute(jid)
        self.assertEqual(seeds[0],seeds[1])
        self.assertEqual(len(env.submitted),3)

    def test_missing_remote_clock_is_unknown_and_swapped_card_invalidates_peak(self):
        self.assertEqual(graphs.execution_clock({},None),{})
        known={"status":{"messages":[["execution_start",{"timestamp":2000}],["execution_success",{"timestamp":5000}]]}}
        self.assertEqual(graphs.execution_clock(known,1),{"remote_execution_seconds":3,"submit_to_start_seconds":1})
        record={"devices":{}}
        for card,mem in (("idA",1200),("idA",8100),("idB",10000)):
            gpu_usage.update(record,{"1":(card,mem,12288,True)})
        self.assertTrue(record["identity_changed"])
        self.assertEqual(record["devices"]["1"]["peak_mib"],8100)

    def test_damaged_cached_video_only_fetches_existing_ticket(self):
        (self.root/"source.mp4").write_bytes(b"modified")
        write(self.root/"remote.json",{"base":"http://localhost","output":"9","prompt_id":"existing"})
        write(self.root/"source-ready.json",{"file":"source.mp4","sha256":"wrong"})
        with patch.object(graphs,"collect",return_value=self.root/"source.mp4") as collect,patch.object(graphs,"request") as req:
            graphs.render({"comfy":"http://localhost"},{},None,None,4,self.root,lambda:None)
        req.assert_not_called()
        self.assertEqual(collect.call_args.args[2],"existing")

    def test_packet_omits_private_values_failed_jobs_count_as_zero(self):
        store=Store({"data":self.root})
        secret="PRIVATE-SCRIPT-027"
        job=store.add({"mode":"auto","script":secret})
        store.claim()
        with store.stage(job,1,"S01-V1-abc123","生成镜头 · T2V","GPU"):
            pass
        store.status(job,"failed",secret)
        write(self.root/job/"plan.json",{"shots":[{"seconds":8,"beat":secret}]})
        packet=benchmark.export(self.root,job,"fixture","other")
        for private in (secret,job,str(self.root),"abc123"):
            self.assertNotIn(private,json.dumps(packet))
        self.assertEqual(packet["qualified_seconds"],0)
        self.assertIsNotNone(packet["wall_seconds"])
        with store.tx() as db:
            db.execute("UPDATE jobs SET state='cancelled',detail='服务中断；需手动继续' WHERE id=?",(job,))
        uncertain=benchmark.export(self.root,job,"fixture","other")
        self.assertIsNone(uncertain["wall_seconds"])
        self.assertIsNone(packet["first_pass_fraction"])
        self.assertEqual(packet["gpu_sampled"],{})
        with self.assertRaises(ValueError):
            benchmark.compare(packet,dict(packet,origin="station"))
        self.assertIsNone(benchmark.compare(packet,packet)["gpu_peak_mib_delta"]["1"])
        (self.root/"factory.sqlite3-wal").touch()
        with self.assertRaisesRegex(ValueError,"WAL"):
            benchmark.export(self.root,job,"fixture","other")
