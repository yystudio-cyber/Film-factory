import os
import socket
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from factory import acceptance, finishing, graphs, media, ownership, speech, timecode
from factory.common import Rejected, read, write
from factory.director import Director
from factory.store import Store


class StationAdaptation(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)

    def test_locked_timecode_split_and_historical_tail(self):
        text = "【Clip 1｜00:00-00:09（9s）】首帧：C006 杯口沿屏幕右侧移动。"
        parts = timecode.breakdown(text,9)
        self.assertEqual([s["seconds"] for s in parts],[5,4])
        self.assertEqual([s["start_frame"] for s in parts],[0,120])
        shots = [dict(source_clip=part,start="previous") for part in parts]
        for s in shots:
            timecode.authored_links(s)
        self.assertEqual([s["start"] for s in shots],["C006","previous"])
        for invalid in (text.replace("00:00","00:01"),text.replace("9s","8s"),text.replace("9s","?")):
            with self.assertRaises(ValueError):
                timecode.breakdown(invalid,9)

    def test_script_and_requirements_alone_supply_duration_and_title(self):
        from factory.web import specification
        spec=specification(dict(script="夜班换岗",requirements="成片时长 24 秒",music=False,mode="auto"))
        self.assertEqual((spec["seconds"],spec["title"]),(24,"夜班换岗"))
        with self.assertRaisesRegex(ValueError,"总时长冲突"):
            specification(dict(script="【Clip 1｜00:00-00:08（8s）】开门",requirements="时长 12 秒",music=True,mode="auto"))

    def test_station_model_authorization_is_separate_from_production(self):
        d=Director(dict(llm="https://example.invalid/v1",model="test",allow_hosted_models=False))
        with patch("factory.director.request") as remote, self.assertRaisesRegex(ValueError,"尚未授权"):
            d.ask("test",{"nonce":"synthetic"})
        remote.assert_not_called()

    def test_repeated_asset_isolation_checks_bytes_and_ignores_sidecars(self):
        cfg=dict(data=self.root,assets=self.root)
        names=["a.png","b.png"]
        for n in names:
            (self.root/n).write_bytes(n.encode())
        hashes=[acceptance._file_hash(self.root/n) for n in names]
        first=acceptance.isolated_brief(cfg,*names,hashes)
        self.assertEqual(first,acceptance.isolated_brief(cfg,*names,hashes))
        (self.root/names[0]).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError,"不一致"):
            acceptance.isolated_brief(cfg,*names,hashes)

    def test_station_only_authorization_cannot_submit_new_script_via_revisions(self):
        store=Store({"data":self.root})
        job=store.add({"mode":"auto","station_test":True})
        with self.assertRaisesRegex(ValueError,"冻结"):
            store.command(job,"revise",shot=1,note="production text")
        store.command(job,"stop")
        self.assertEqual(store.job(job)["state"],"cancelled")

    def test_read_only_clip_scan_uses_real_files_and_counter(self):
        from factory.archive import scan
        approved=self.root/"approved"
        approved.mkdir()
        for name in ("C001-take.mp4","C006-plate.mp4"):
            (approved/name).touch()
        write(self.root/"clip_code_counter.json",{"last":21})
        result=scan(dict(studio_root=self.root,counter_field="last"))
        self.assertEqual(result["last_issued"],21)
        self.assertEqual(set(result["clips"]),{"C001","C006"})

    def test_known_wrong_device_is_recorded_before_station_use(self):
        stats={"devices":[{"type":"cuda","index":0}],"system":{"argv":["main.py","--default-device","0","--lowvram"]}}
        with patch.object(acceptance,"request",return_value=stats):
            result=acceptance._comfy({"station_contract":True,"comfy":"http://localhost"})
        self.assertTrue(any("GPU1" in err for err in result["placement_errors"]))

    def test_native_geometry_and_audio(self):
        inputs = lambda k,**kw:dict(class_type=k,inputs=kw)
        graph={"1":inputs("PrimitiveInt",value=2),"2":inputs("PrimitiveInt",value=25),
               "3":inputs("ComfyMathExpression",expression="a*b+1",**{"values.a":["1",0],"values.b":["2",0]}),
               "4":inputs("ResizeImageMaskNode",width=1920,height=1088),
               "5":inputs("EmptyLTXVLatentVideo",length=["3",1],width=1280,height=720,image=["4",0]),
               "6":inputs("Audio"),"7":inputs("CreateVideo",fps=["2",0],audio=["6",0],images=["5",0])}
        profile={"graph":graph,"bindings":{"fps":["2.value"],"seconds":["1.value"],
                 "width":["5.width"],"height":["5.height"]},"output":"7"}
        graphs.check_native_profile(profile,"FLF",(1920,1088))
        for n,field in [("4","width"),("7","audio")]:
            saved=graph[n]["inputs"].pop(field)
            with self.assertRaises(ValueError):
                graphs.check_native_profile(profile,"FLF",(1920,1088))
            graph[n]["inputs"][field]=saved
        graph["3"]["inputs"]["expression"]="a*b"
        with self.assertRaisesRegex(ValueError,"表达式"):
            graphs.check_native_profile(profile,"FLF",(1920,1088))

    def test_dynamic_registry_selects_actual_resize_children_and_math_operands(self):
        schema={"required":{"size":["COMFY_DYNAMICCOMBO_V3",{"options":[
            {"key":"exact","inputs":{"required":{"width":["INT"],"height":["INT"]}}},
            {"key":"ratio","inputs":{"required":{"scale":["FLOAT"]}}}]}],
            "values":["COMFY_AUTOGROW_V3",{"template":{"names":["a","b","c"],"min":1,
                      "input":{"required":{"number":["FLOAT"]}}}}]}}
        required,fields=acceptance.registry_fields(schema,{"size":"exact","values.a":["1",0]})
        self.assertEqual(set(required),{"size","size.width","size.height","values.a"})
        self.assertIn("values.b",fields)
        self.assertNotIn("size.scale",fields)

    def test_asr_off_and_unsupported_sapi_language_remain_unverified(self):
        shot=dict(dialogue="Tack så mycket",dialogue_language="sv",narration="")
        with patch("factory.media.narrate") as voice:
            result=speech.screen_dialogue(dict(asr="off",dialogue_fallback="dub"),None,dict(shot,seconds=2),self.root,True)
        voice.assert_not_called()
        self.assertEqual((result["status"],result["transcript"],result["fallback"]),("unverified","","subtitles"))

    def test_bubble_and_four_second_endcard_across_a_cut(self):
        shots=[dict(seconds=n,frames=n*24,title="") for n in (6,2,2)]
        shots[0]["bubble"]="#TEST"
        plan={"shots":shots,"endcard":"THE END"}
        finishing.plan_layers(dict(script="#TEST 气泡；片尾定版 THE END",requirements="",seconds=10),plan)
        self.assertNotIn("endcard",shots[0])
        self.assertEqual([s["endcard"]["first_frame"] for s in shots[1:]],[0,0])
        with self.assertRaisesRegex(ValueError,"缺少"):
            finishing.plan_layers(dict(script="#OTHER 气泡 #TEST THE END",requirements="",seconds=10),plan)

    def test_number_lease_survives_db_rollback_and_detects_old_writer(self):
        cfg=dict(data=self.root,studio_root=self.root,station_contract=True)
        write(self.root/"clip_code_counter.json",16)
        store=Store(cfg)
        with store.tx() as db:
            db.execute("INSERT INTO takes(code,job,shot,path) VALUES(16,'studio-archive',0,'original')")
        jid=store.add({"mode":"auto"})
        store.claim()
        manager=ownership.StationOwner(cfg)
        with patch.dict(os.environ,{"LOCALAPPDATA":str(self.root)}):
            manager.check_namespace(store)
            self.assertEqual(store.register(jid,1,"new"),17)
            with self.assertRaises(sqlite3.IntegrityError):
                store.register(jid,1,"new")
            self.assertEqual(store.register(jid,1,"new2"),19)
            write(self.root/"clip_code_counter.json",17)
            with self.assertRaisesRegex(ValueError,"已改变"):
                manager.check_namespace(store)

    def test_post_graphics_count_toward_locked_literal_without_claiming_speech(self):
        from factory.story import literal_field,_literal_evidence
        shot=dict(shot=1,bubble="#生活",endcard={"text":"回家","first_frame":96})
        self.assertEqual(literal_field(shot,"title"),"#生活\n回家")
        shot.update(dialogue="Tack",speech_review="unverified",transcript="")
        evidence=_literal_evidence([{"id":"one","checks":[dict(shot=1,channel="dialogue",literal="Tack")]}],[shot])
        self.assertEqual(evidence["one"][0]["audio_status"],"unverified")

    def test_aesthetic_failure_cannot_be_hidden_by_high_visual_scores(self):
        d=Director(dict(threshold=4))
        d.ask=Mock(return_value={"scores":{k:5 for k in ("story","composition","identity","continuity","artifacts")},
                                 "notes":"镜头审查","repair":"correct hand direction","checks":[],
                                 "aesthetic_checks":[{"index":1,"passed":False,"evidence":"手的方向反转"}]})
        result=d.review(dict(requirements="实拍质感",aesthetic_rules=["手部连续"]),{"bible":"room"},{},[],[])
        self.assertFalse(result["passed"])

    def test_failed_score_candidate_is_retained_and_not_resubmitted(self):
        calls=[]
        def generate(cfg,prompt,seconds,work,checkpoint):
            calls.append(work.name)
            if work.name.endswith("-1"):
                raise Rejected("静音")
            write(work/"score-ticket.json",{"task_id":"two"})
            wav=work/"score.wav"
            wav.write_bytes(b"fixture")
            return wav
        cfg={"score_candidates":2}
        with patch.object(media,"music",side_effect=generate),patch.object(media,"programme_level",
                return_value={"integrated_lufs":-19,"true_peak_dbtp":-3}):
            for _ in range(2):
                media.select_music(cfg,"strings",6,self.root,lambda:None)
        self.assertEqual(calls,["score-option-1","score-option-2"])
        self.assertEqual(read(self.root/"score-selection.json")["selected"],2)

    def test_legacy_producer_blocks_acquisition_even_when_queue_is_empty(self):
        cfg=dict(data=self.root,station_contract=True)
        manager=ownership.StationOwner(cfg)
        port=Mock()
        port.bind.side_effect=OSError("in use")
        with patch.object(socket,"socket",return_value=port),self.assertRaisesRegex(ValueError,"8765"):
            manager.acquire(Mock())
        self.assertIsNone(manager.guard)
        port.close.assert_called_once()
